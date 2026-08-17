"""AsyncMailFlat — the same API over real async I/O.

    from mailflat.aio import AsyncMailFlat

    async with AsyncMailFlat(api_key="mf_live_...") as mf:
        inbox = await mf.create(label="signup-test")
        otp = await inbox.wait_for_otp(timeout=30)

Why real async and not a thread wrapper: an agent watching 100 inboxes blocks 100 threads
for 30 seconds each with the sync client, one per `wait_for_otp`. Here they are 100
coroutines on one event loop, so `asyncio.gather` over a fleet of inboxes actually works.

⚠️ Everything that is a DECISION lives in `_http.py`, shared with the sync client: what is
retriable, how long to back off, which exception a status becomes. The two clients differ
only in how they wait (`time.sleep` vs `await asyncio.sleep`) and how they issue requests.
That is deliberate — two copies of those rules would drift, and the drift would be
invisible because both sides would still pass their own tests (B-055's exact shape).
Everything pure — payload building, message parsing, subject/threading rules — is imported
from `inbox.py`, not reimplemented.

⚠️ `AsyncMessage` / `AsyncAttachment` exist because `reply()`, `mark_read()`, `delete()` and
`download()` do I/O. Sharing one class between both clients would make the return type
depend on which client produced the object — `msg.reply()` sometimes a dict, sometimes a
coroutine — and a forgotten `await` would silently do nothing. Separate types turn that
into a visible error.

Connected to:
  - imports from: mailflat._http (rules), mailflat.inbox (pure helpers + dataclasses),
                  mailflat.errors, httpx
  - imported by:  user code, mailflat.langchain (async tools)

Key exports:
  - `AsyncMailFlat` · `AsyncInbox` · `AsyncMessage` · `AsyncAttachment`
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from ._http import (backoff_delay, error_detail, interpret, retry_after_seconds,
                    send_timeout_message, should_retry)
from .errors import (EncryptedInboxError, MailFlatError, OTPTimeoutError, SendFailedError,
                     SendTimeoutError, raise_for_status)
from .inbox import (Attachment, Message, _check_direction, _reply_subject,
                    _send_payload, otp_timeout_message)

DEFAULT_BASE_URL = "https://mailflat.net"

logger = logging.getLogger("mailflat")


class AsyncAttachment(Attachment):
    """An attachment whose `download()` is awaitable."""

    async def download(self) -> bytes:  # type: ignore[override]
        if self._message is None or self._message._inbox is None or self.id is None:
            raise MailFlatError(
                "This attachment isn't attached to a message. "
                "Use await inbox.download_attachment(message_id, attachment_id) instead."
            )
        if self.truncated:
            raise MailFlatError(f"Attachment {self.filename!r} was too large to store")
        return await self._message._inbox.download_attachment(self._message.id, self.id)


class AsyncMessage(Message):
    """A message whose write operations are awaitable.

    Read-only fields (`otp`, `subject`, `links`, `headers`, …) behave exactly as on the sync
    `Message`; they are parsed the same way, by the same code.
    """

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AsyncMessage":
        message = super().from_dict(data)          # one parser, shared with the sync path
        # Re-wrap attachments so their download() is awaitable too.
        message.attachments = [AsyncAttachment.from_dict(a.raw) for a in message.attachments]
        for att in message.attachments:
            att._message = message
        return message                              # type: ignore[return-value]

    async def reply(self, body: str = "", *, html: str | None = None,   # type: ignore[override]
                    subject: str | None = None, cc: list[str] | None = None,
                    bcc: list[str] | None = None,
                    attachments: list[Any] | None = None) -> dict[str, Any]:
        """Reply in the same conversation. Same rules as the sync version, awaited."""
        if self._inbox is None:
            raise ValueError(
                "This message isn't attached to an inbox. Use inbox.send(...) instead."
            )
        target = self.reply_to_address
        if not target:
            raise ValueError("This message has no sender to reply to.")
        return await self._inbox.send(
            target,
            subject=subject if subject is not None else _reply_subject(self.subject),
            body=body, html=html, in_reply_to=self.message_id,
            cc=cc, bcc=bcc, attachments=attachments,
        )

    async def mark_read(self) -> dict[str, Any]:    # type: ignore[override]
        if self._inbox is None or self.id is None:
            raise ValueError("This message isn't attached to an inbox.")
        return await self._inbox.mark_read(self.id)

    async def delete(self) -> dict[str, Any]:       # type: ignore[override]
        if self._inbox is None or self.id is None:
            raise ValueError("This message isn't attached to an inbox.")
        return await self._inbox.delete_message(self.id)


class AsyncInbox:
    """One inbox, async. Mirrors `Inbox` method for method."""

    def __init__(self, client: "AsyncMailFlat", address: str, **meta: Any) -> None:
        self._client = client
        self.address = address
        self.raw = {"address": address, **meta}
        self.name = meta.get("name") or meta.get("label")
        self.retention_hours = meta.get("retention_hours")

    def __repr__(self) -> str:
        return f"AsyncInbox(address={self.address!r})"

    def _wrap(self, data: dict[str, Any]) -> AsyncMessage:
        message = AsyncMessage.from_dict(data)
        message._inbox = self
        return message

    # ------------------------------------------------------------------ read
    async def messages(self, *, direction: str = "in") -> list[AsyncMessage]:
        """This inbox's messages, newest first. Defaults to received mail only (B-057)."""
        _check_direction(direction)
        res = await self._client._get(
            f"/api/v1/inboxes/{self.address}/messages?direction={direction}")
        return [self._wrap(e) for e in (res.get("emails") or [])]

    async def message(self, message_id: int) -> AsyncMessage:
        """One message by id — how you check what happened to a send."""
        res = await self._client._get(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}")
        return self._wrap(res.get("email") or {})

    async def latest(self, *, direction: str = "in") -> AsyncMessage | None:
        _check_direction(direction)
        res = await self._client._get(
            f"/api/v1/inboxes/{self.address}/latest?direction={direction}")
        email = res.get("email")
        return self._wrap(email) if email else None

    async def wait_for_message(self, *, timeout: float = 30, poll_interval: float = 1.0,
                               direction: str = "in") -> AsyncMessage:
        """Poll until a message arrives. Received mail only by default, so an agent can
        send to a peer and wait for the answer without matching its own outgoing mail."""
        _check_direction(direction)
        deadline = time.monotonic() + timeout
        while True:
            message = await self.latest(direction=direction)
            if message is not None:
                return message
            if time.monotonic() >= deadline:
                raise OTPTimeoutError(
                    f"No message arrived for {self.address} within {timeout}s")
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    async def wait_for_otp(self, *, timeout: float = 30, poll_interval: float = 1.0) -> str:
        """Poll until a one-time code arrives and return it.

        The timeout message distinguishes "no mail arrived" from "mail arrived but no code
        could be extracted" — the same wording as the sync client, from the same helper.
        Those are different problems and one sentence for both sent people hunting a
        delivery failure while the message sat in the inbox (B-062).
        """
        deadline = time.monotonic() + timeout
        seen: dict[str, Any] | None = None
        while True:
            res = await self._client._get(f"/api/v1/inboxes/{self.address}/latest")
            if res.get("encrypted"):
                raise EncryptedInboxError(
                    res.get("note")
                    or "This inbox is end-to-end encrypted; the server cannot read its messages."
                )
            email = res.get("email") or None
            if email:
                seen = email
                otp = email.get("otp_code")
                if otp:
                    return otp
            if time.monotonic() >= deadline:
                raise OTPTimeoutError(
                    otp_timeout_message(self.address, timeout, seen))  # shared wording
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    async def download_attachment(self, message_id: int, attachment_id: int) -> bytes:
        resp = await self._client._get_bytes(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}/attachments/{attachment_id}"
        )
        if resp.headers.get("content-type", "").startswith("application/json"):
            raise EncryptedInboxError(
                "This inbox is end-to-end encrypted; the server cannot decrypt this attachment."
            )
        return resp.content

    # ----------------------------------------------------------------- write
    async def send(self, to: str, *, subject: str = "", body: str = "",
                   html: str | None = None, in_reply_to: str | None = None,
                   cc: list[str] | None = None, bcc: list[str] | None = None,
                   attachments: list[Any] | None = None) -> dict[str, Any]:
        """Send from this inbox. Resolves once the mail is ACCEPTED, not delivered —
        use `wait_until_sent(message_id)` or the delivery webhook for the result.

        Not retried on gateway errors: a retried send can deliver the same mail twice.
        """
        payload = _send_payload(to, subject, body, html, in_reply_to, cc, bcc, attachments)
        return await self._client._post(f"/api/v1/inboxes/{self.address}/send", payload)

    async def wait_until_sent(self, message_id: int, *, timeout: float = 120.0,
                              poll_interval: float = 2.0) -> AsyncMessage:
        """Wait for a final delivery state. Raises on permanent failure and on timeout —
        it never hands back a failed mail as if nothing happened."""
        deadline = time.monotonic() + timeout
        while True:
            message = await self.message(message_id)
            status = message.send_status
            if status in ("sent", "unsigned"):
                return message
            if status == "failed":
                raise SendFailedError(message.send_error or "Delivery failed",
                                      message_id=message_id, status=status)
            if time.monotonic() >= deadline:
                raise SendTimeoutError(
                    send_timeout_message(message_id, status, timeout, message.send_error),
                    message_id=message_id, status=status,
                    last_error=message.send_error)
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    async def mark_read(self, message_id: int) -> dict[str, Any]:
        return await self._client._post(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}/read", idempotent=True)

    async def burn(self) -> dict[str, Any]:
        """Delete every message but keep the address."""
        return await self._client._post(f"/api/v1/inboxes/{self.address}/burn",
                                        idempotent=True)

    async def delete(self) -> dict[str, Any]:
        return await self._client._delete(f"/api/v1/inboxes/{self.address}")

    async def delete_message(self, message_id: int) -> dict[str, Any]:
        return await self._client._delete(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}")


class AsyncMailFlat:
    """MailFlat automation API client over asyncio."""

    def __init__(self, api_key: str | None = None, *, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 30.0, max_retries: int = 2,
                 http_client: httpx.AsyncClient | None = None) -> None:
        api_key = api_key or os.environ.get("MAILFLAT_API_KEY")
        if not api_key:
            raise MailFlatError(
                "An API key is required. Pass AsyncMailFlat(api_key=...) or set MAILFLAT_API_KEY."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        self._http = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": api_key, "User-Agent": self.user_agent()},
            timeout=timeout,
        )

    @staticmethod
    def user_agent() -> str:
        """Same `User-Agent` as the sync client: the server should see one SDK, not two."""
        from . import __version__

        return f"mailflat-python/{__version__}"

    async def __aenter__(self) -> "AsyncMailFlat":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -------------------------------------------------------- HTTP internals
    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None,
                       *, idempotent: bool | None = None) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = await self._http.request(method, path, json=json)
            except httpx.HTTPError as exc:
                raise MailFlatError(f"Network error: {exc}") from exc

            retry_after = retry_after_seconds(resp.headers)
            if should_retry(resp.status_code, method, idempotent, attempt, self.max_retries):
                attempt += 1
                delay = backoff_delay(attempt, retry_after)
                logger.debug("retrying %s %s after %s (status %s, attempt %s/%s)",
                             method, path, delay, resp.status_code, attempt, self.max_retries)
                await asyncio.sleep(delay)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = {}

            if resp.status_code >= 400:
                logger.debug("%s %s failed with %s: %s", method, path, resp.status_code,
                             error_detail(data))
            return interpret(resp.status_code, data, retry_after=retry_after)

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path)

    async def _post(self, path: str, json: dict[str, Any] | None = None,
                    *, idempotent: bool = False) -> dict[str, Any]:
        return await self._request("POST", path, json=json or {}, idempotent=idempotent)

    async def _delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    async def _get_bytes(self, path: str) -> httpx.Response:
        """Raw response for binary endpoints, retried like any other GET."""
        attempt = 0
        while True:
            try:
                resp = await self._http.request("GET", path)
            except httpx.HTTPError as exc:
                raise MailFlatError(f"Network error: {exc}") from exc
            retry_after = retry_after_seconds(resp.headers)
            if should_retry(resp.status_code, "GET", None, attempt, self.max_retries):
                attempt += 1
                await asyncio.sleep(backoff_delay(attempt, retry_after))
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

    # ---------------------------------------------------------------- public
    async def create(self, *, prefix: str | None = None, label: str | None = None,
                     subdomain: str | None = None, domain: str | None = None,
                     retention_hours: int | None = None) -> AsyncInbox:
        """Create a new inbox. Same arguments and same server rules as the sync client."""
        payload: dict[str, Any] = {}
        for key, value in (("prefix", prefix), ("label", label), ("subdomain", subdomain),
                           ("domain", domain), ("retention_hours", retention_hours)):
            if value is not None:
                payload[key] = value
        res = await self._post("/api/v1/inboxes", payload)
        return AsyncInbox(self, res["address"],
                          **{k: v for k, v in res.items() if k != "address"})

    create_inbox = create

    async def list(self) -> list[AsyncInbox]:
        res = await self._get("/api/v1/inboxes")
        return [AsyncInbox(self, i["address"], **{k: v for k, v in i.items() if k != "address"})
                for i in (res.get("inboxes") or [])]

    def inbox(self, address: str) -> AsyncInbox:
        """Attach to an existing address without making an API call (no await needed)."""
        return AsyncInbox(self, address)
