"""Sync ↔ async BEHAVIOUR parity — every rule tested against both clients.

Why behaviour and not a name list: B-055 was a method that EXISTED on both surfaces and
still misbehaved on one (`waitForOtp` skipped `guarded()`). Comparing method names would
have called those surfaces identical. So the rules below are parametrised over both
clients and each one runs twice; the name/signature comparison at the bottom is an EXTRA
lock, never the only one.

Every scenario here is a rule with a scar behind it:
  - `messages()` defaults to received mail (B-057)
  - POST is never retried; GET/DELETE are (B-058 — the Java twin is still open as B-066)
  - `Retry-After` is obeyed rather than guessed at
  - the OTP timeout sentence tells "no mail" apart from "mail, no code" (B-062)
  - an encrypted inbox raises instead of returning empty
  - `send()` accepts 202 and builds the same body with cc/bcc/attachments
  - `wait_until_sent` raises on failure and on timeout — never returns quietly

Connected to:
  - exercises: mailflat.client (sync), mailflat.aio (async), mailflat._http (shared rules)
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json

import httpx
import pytest

from mailflat import EncryptedInboxError, MailFlat, OTPTimeoutError, RateLimitError
from mailflat.aio import AsyncInbox, AsyncMailFlat
from mailflat.inbox import Inbox

ADDR = "agent@x7k2m.mailflat.net"


def run(value):
    """Await a coroutine, pass anything else through — lets one test body drive both."""
    if inspect.isawaitable(value):
        return asyncio.run(_await(value))
    return value


async def _await(value):
    return await value


def make(kind: str, handler, **opts):
    """Build a sync or async client on the same MockTransport handler."""
    if kind == "sync":
        http = httpx.Client(transport=httpx.MockTransport(handler),
                            base_url="https://mailflat.net")
        return MailFlat(api_key="mf_test_x", http_client=http, **opts)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://mailflat.net")
    return AsyncMailFlat(api_key="mf_test_x", http_client=http, **opts)


BOTH = pytest.mark.parametrize("kind", ["sync", "async"])


# ------------------------------------------------------------------- reading

@BOTH
def test_messages_defaults_to_received_mail(kind):
    """🔒 B-057: without this an agent's send() shows up as the reply it is waiting for."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"emails": []})

    run(make(kind, handler).inbox(ADDR).messages())
    assert seen[0].endswith("direction=in")


@BOTH
def test_invalid_direction_is_rejected_before_any_request(kind):
    calls: list[str] = []
    client = make(kind, lambda r: (calls.append(str(r.url)), httpx.Response(200, json={}))[1])
    with pytest.raises(ValueError, match="direction"):
        run(client.inbox(ADDR).messages(direction="sideways"))
    assert calls == [], "invalid direction still hit the network"


@BOTH
def test_encrypted_inbox_raises_instead_of_returning_nothing(kind):
    """Silence would look like "no code yet" and the caller would poll until timeout."""
    client = make(kind, lambda r: httpx.Response(
        200, json={"encrypted": True, "note": "This inbox is end-to-end encrypted."}))
    with pytest.raises(EncryptedInboxError):
        run(client.inbox(ADDR).wait_for_otp(timeout=1))


@BOTH
def test_otp_timeout_tells_no_mail_apart_from_no_code(kind):
    """🔒 B-062: one sentence for both faults sent people hunting a delivery failure while
    the mail sat in the inbox with a code the parser did not know."""
    empty = make(kind, lambda r: httpx.Response(200, json={"email": None}))
    with pytest.raises(OTPTimeoutError, match="No message arrived"):
        run(empty.inbox(ADDR).wait_for_otp(timeout=0.01, poll_interval=0.01))

    arrived = make(kind, lambda r: httpx.Response(200, json={
        "email": {"subject": "Your code", "body_text": "it is 4 8 2", "otp_code": None}}))
    with pytest.raises(OTPTimeoutError, match="no OTP could be extracted"):
        run(arrived.inbox(ADDR).wait_for_otp(timeout=0.01, poll_interval=0.01))


# -------------------------------------------------------------------- retry

@BOTH
def test_get_is_retried_but_post_is_not(kind):
    """🔒 B-058: retrying a send after a 504 delivers the SAME MAIL TWICE.

    The Java SDK still has this bug (B-066), which is exactly why it is locked on both
    Python surfaces rather than assumed.
    """
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        # Retry-After: 0 so the backoff does not make this test sleep for seconds. It does
        # not weaken the assertion — what is under test is the attempt COUNT, and the
        # Retry-After parsing itself has its own test below.
        return httpx.Response(503, json={"detail": "unavailable"}, headers={"Retry-After": "0"})

    client = make(kind, handler, max_retries=2)
    with pytest.raises(Exception):
        run(client.inbox(ADDR).messages())
    gets = calls.count("GET")

    calls.clear()
    with pytest.raises(Exception):
        run(client.inbox(ADDR).send("x@y.com"))
    posts = calls.count("POST")

    assert gets == 3, f"GET should retry twice, made {gets} attempts"
    assert posts == 1, f"POST must NOT be retried, made {posts} attempts"


@BOTH
def test_mark_read_is_a_post_but_still_retried(kind):
    """Repeating mark_read reaches the same end state, so it is explicitly idempotent."""
    calls: list[str] = []
    client = make(kind, lambda r: (calls.append(r.method),
                                   httpx.Response(503, json={},
                                                  headers={"Retry-After": "0"}))[1],
                  max_retries=2)
    with pytest.raises(Exception):
        run(client.inbox(ADDR).mark_read(1))
    assert calls.count("POST") == 3


@BOTH
def test_retry_after_header_reaches_the_error(kind):
    """Coming back early both earns another 429 and stretches the window."""
    client = make(kind, lambda r: httpx.Response(
        429, json={"detail": "slow down"}, headers={"Retry-After": "12"}), max_retries=0)
    with pytest.raises(RateLimitError) as exc:
        run(client.inbox(ADDR).messages())
    assert exc.value.retry_after == 12.0


# --------------------------------------------------------------------- send

@BOTH
def test_send_builds_the_same_body_on_both_clients(kind):
    """Payload shape is shared code — this proves the async path really uses it."""
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(202, json={"ok": True, "queued": True, "message_id": 5})

    run(make(kind, handler).inbox(ADDR).send(
        "peer@example.com", subject="Hi", cc=["c@example.com"], bcc=["b@example.com"],
        attachments=[{"filename": "a.txt", "content": b"hello"}]))

    body = bodies[0]
    assert body["cc"] == ["c@example.com"] and body["bcc"] == ["b@example.com"]
    att = body["attachments"][0]
    assert att["filename"] == "a.txt"
    assert base64.b64decode(att["content_b64"]) == b"hello"


@BOTH
def test_202_is_success_on_both(kind):
    client = make(kind, lambda r: httpx.Response(202, json={"ok": True, "queued": True}))
    assert run(client.inbox(ADDR).send("peer@example.com"))["queued"] is True


@BOTH
def test_wait_until_sent_raises_on_failure_and_on_timeout(kind):
    """🔒 It must never hand back a failed mail as if nothing happened."""
    from mailflat import SendFailedError, SendTimeoutError

    failed = make(kind, lambda r: httpx.Response(200, json={
        "email": {"id": 7, "send_status": "failed", "send_error": "550 user unknown"}}))
    with pytest.raises(SendFailedError, match="550 user unknown"):
        run(failed.inbox(ADDR).wait_until_sent(7, timeout=1))

    queued = make(kind, lambda r: httpx.Response(
        200, json={"email": {"id": 7, "send_status": "queued"}}))
    with pytest.raises(SendTimeoutError, match="still be delivered"):
        run(queued.inbox(ADDR).wait_until_sent(7, timeout=0.02, poll_interval=0.01))

    # Both clients must report the last attempt IDENTICALLY. The sentence lives in
    # `_http.send_timeout_message` for exactly this reason: two copies drift, and a shape
    # comparison would happily pass two differently worded sentences.
    retrying = make(kind, lambda r: httpx.Response(200, json={
        "email": {"id": 7, "send_status": "retrying", "send_error": "451 greylisted"}}))
    with pytest.raises(SendTimeoutError) as excinfo:
        run(retrying.inbox(ADDR).wait_until_sent(7, timeout=0.02, poll_interval=0.01))
    assert excinfo.value.last_error == "451 greylisted"
    assert "451 greylisted" in str(excinfo.value)


@BOTH
def test_user_agent_is_identical(kind):
    """The server should see one SDK, not two — a split here makes usage stats lie."""
    seen: list[str] = []
    client = make(kind, lambda r: (seen.append(r.headers.get("user-agent", "")),
                                   httpx.Response(200, json={"emails": []}))[1])
    run(client.inbox(ADDR).messages())
    assert MailFlat.user_agent() == AsyncMailFlat.user_agent()


# ------------------------------------------------- async-only guarantees

def test_async_really_runs_concurrently():
    """The point of the whole phase: N inboxes on ONE loop, not N blocked threads.

    Asserted by ordering, not by clock: the second request must start before the first
    finishes. A thread-pool wrapper pretending to be async would fail this.
    """
    started: list[str] = []
    release = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        started.append(str(req.url))
        if len(started) < 2:
            await release.wait()          # first call parks until the second one arrives
        else:
            release.set()
        return httpx.Response(200, json={"emails": []})

    async def main():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://mailflat.net")
        mf = AsyncMailFlat(api_key="mf_test_x", http_client=http)
        await asyncio.wait_for(
            asyncio.gather(mf.inbox("a@x.mailflat.net").messages(),
                           mf.inbox("b@x.mailflat.net").messages()),
            timeout=5,
        )
        await mf.aclose()

    asyncio.run(main())
    assert len(started) == 2


def test_async_context_manager_closes_the_transport():
    async def main():
        http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})), base_url="https://mailflat.net")
        async with AsyncMailFlat(api_key="mf_test_x", http_client=http) as mf:
            assert mf.api_key == "mf_test_x"
        return http.is_closed

    assert asyncio.run(main()) is True


# ------------------------------------------------- EXTRA lock: shape, not the only one

def test_async_surface_covers_the_sync_one():
    """Name/signature comparison — an EXTRA check.

    Deliberately not the only lock: B-055 slipped through exactly this kind of comparison.
    Its job is to catch the opposite failure — a method added to one client and forgotten
    on the other.
    """
    def public(cls):
        return {n for n, _ in inspect.getmembers(cls, callable) if not n.startswith("_")}

    missing = public(Inbox) - public(AsyncInbox)
    assert missing == set(), f"AsyncInbox is missing: {sorted(missing)}"

    missing_client = public(MailFlat) - public(AsyncMailFlat) - {"close"}
    assert missing_client == set(), f"AsyncMailFlat is missing: {sorted(missing_client)}"

    # Same keyword arguments, so a caller can switch clients without rewriting calls.
    for name in ("send", "messages", "wait_for_otp", "wait_until_sent", "wait_for_message"):
        sync_kw = {p.name for p in inspect.signature(getattr(Inbox, name)).parameters.values()
                   if p.kind is p.KEYWORD_ONLY}
        async_kw = {p.name for p in inspect.signature(getattr(AsyncInbox, name)).parameters.values()
                    if p.kind is p.KEYWORD_ONLY}
        assert sync_kw == async_kw, f"{name}: keyword arguments differ ({sync_kw} vs {async_kw})"


def test_shared_rules_are_not_duplicated():
    """🔒 The retry rules must live in _http.py only.

    If a client grows its own copy of the status set or the idempotent-method set, the two
    will drift and nothing else in this file would notice — both would still pass, each
    against its own copy.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "mailflat"
    for name in ("client.py", "aio.py"):
        src = (root / name).read_text()
        assert "429, 502, 503, 504" not in src, f"{name} re-declares the retry status set"
        assert '"GET", "HEAD", "DELETE"' not in src, f"{name} re-declares idempotent methods"
