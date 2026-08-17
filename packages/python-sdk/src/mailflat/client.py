"""MailFlat client — a thin, typed Python wrapper around the /api/v1 automation API.

One account API key (X-API-Key) creates inboxes, lists them, reads mail/OTP and sends mail.
Every higher layer (MCP, LangChain) calls this client, so behaviour lives in one place.

Connected to:
  - imports from: mailflat.inbox, mailflat.errors (+ httpx)
  - imported by:  mailflat.__init__

Key exports:
  - `MailFlat(api_key=..., base_url=...)` — client
  - `.create(...) / .create_inbox(...)` — new inbox → `Inbox`
  - `.list()` — list of `Inbox`
  - `.inbox(address)` — attach to an existing address
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from ._http import backoff_delay, error_detail, interpret, retry_after_seconds, should_retry
from .errors import MailFlatError, raise_for_status
from .inbox import Inbox

DEFAULT_BASE_URL = "https://mailflat.net"

logger = logging.getLogger("mailflat")


class MailFlat:
    """MailFlat automation API client.

    >>> from mailflat import MailFlat
    >>> mf = MailFlat(api_key="mf_live_...")
    >>> inbox = mf.create(label="signup-test")
    >>> inbox.address
    'signup-test-...@mailflat.net'
    >>> otp = inbox.wait_for_otp(timeout=30)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        api_key = api_key or os.environ.get("MAILFLAT_API_KEY")
        if not api_key:
            raise MailFlatError(
                "An API key is required. Pass MailFlat(api_key=...) or set MAILFLAT_API_KEY."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        # Injecting http_client keeps tests simple (httpx.MockTransport).
        self._http = http_client or httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": api_key, "User-Agent": self.user_agent()},
            timeout=timeout,
        )

    @staticmethod
    def user_agent() -> str:
        """Return the `User-Agent` this client sends, e.g. `mailflat-python/<version>`.

        Sending the version lets the server tell which SDK build is misbehaving.
        """
        # Read the version here so the module level stays free of a circular import.
        from . import __version__

        return f"mailflat-python/{__version__}"

    # --------------------------------------------------------------- context
    def __enter__(self) -> "MailFlat":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -------------------------------------------------------------- HTTP internals
    def _request(self, method: str, path: str, json: dict[str, Any] | None = None,
                 *, idempotent: bool | None = None) -> dict[str, Any]:
        """`idempotent=True` marks a call as safe to repeat regardless of its method.

        mark_read/burn are POSTs but repeating them is harmless (same end state). It is
        never passed for send: a repeated send delivers the same mail twice.
        """
        attempt = 0
        while True:
            try:
                resp = self._http.request(method, path, json=json)
            except httpx.HTTPError as exc:  # network / timeout
                raise MailFlatError(f"Network error: {exc}") from exc

            retry_after = retry_after_seconds(resp.headers)
            if should_retry(resp.status_code, method, idempotent, attempt, self.max_retries):
                attempt += 1
                delay = backoff_delay(attempt, retry_after)
                logger.debug("retrying %s %s after %s (status %s, attempt %s/%s)",
                             method, path, delay, resp.status_code, attempt, self.max_retries)
                time.sleep(delay)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = {}

            if resp.status_code >= 400:
                logger.debug("%s %s failed with %s: %s", method, path, resp.status_code,
                             error_detail(data))
            return interpret(resp.status_code, data, retry_after=retry_after)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def _post(self, path: str, json: dict[str, Any] | None = None,
              *, idempotent: bool = False) -> dict[str, Any]:
        return self._request("POST", path, json=json or {}, idempotent=idempotent)

    def _delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    def _get_bytes(self, path: str) -> httpx.Response:
        """Raw response for binary endpoints (attachment download).

        Retries on the same statuses as any other GET: this path bypassed the retry loop
        entirely until 0.4.1, so an attachment download gave up on the first 503 while
        every other read retried.
        """
        attempt = 0
        while True:
            try:
                resp = self._http.request("GET", path)
            except httpx.HTTPError as exc:
                raise MailFlatError(f"Network error: {exc}") from exc
            retry_after = retry_after_seconds(resp.headers)
            if should_retry(resp.status_code, "GET", None, attempt, self.max_retries):
                attempt += 1
                delay = backoff_delay(attempt, retry_after)
                logger.debug("retrying GET %s after %s (status %s)", path, delay, resp.status_code)
                time.sleep(delay)
                continue
            break
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise_for_status(resp.status_code, error_detail(body),
                             retry_after=retry_after_seconds(resp.headers))
        return resp

    # --------------------------------------------------------------- public
    def create(
        self,
        *,
        prefix: str | None = None,
        label: str | None = None,
        subdomain: str | None = None,
        domain: str | None = None,
        retention_hours: int | None = None,
    ) -> Inbox:
        """Create a new inbox and return it as an `Inbox`.

        - prefix: local part of the address (random `agent-...` when omitted)
        - label: display name shown in the dashboard (paid plans only — on Free the
          response carries `ignored_fields` saying it had no effect)
        - subdomain: mailflat.net subdomain (random when omitted)
        - domain: one of your own verified BYOD domains, e.g. `acme.com`
        - retention_hours: how long messages are kept (defaults to your plan, capped by it)
        """
        payload: dict[str, Any] = {}
        if prefix is not None:
            payload["prefix"] = prefix
        if label is not None:
            payload["label"] = label
        if subdomain is not None:
            payload["subdomain"] = subdomain
        if domain is not None:
            payload["domain"] = domain
        if retention_hours is not None:
            payload["retention_hours"] = retention_hours
        res = self._post("/api/v1/inboxes", payload)
        return Inbox(self, res["address"], **{k: v for k, v in res.items() if k != "address"})

    # Alias so the landing-page snippets match the SDK exactly.
    create_inbox = create

    def list(self) -> list[Inbox]:
        """Return the inboxes created with this API key."""
        res = self._get("/api/v1/inboxes")
        return [
            Inbox(self, i["address"], **{k: v for k, v in i.items() if k != "address"})
            for i in (res.get("inboxes") or [])
        ]

    def inbox(self, address: str) -> Inbox:
        """Attach to an existing address without making an API call."""
        return Inbox(self, address)
