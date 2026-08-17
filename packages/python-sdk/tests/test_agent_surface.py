"""Tests for the plan-203 fixes — retry safety, direction filter, new Message fields.

Every test here maps to a finding that an external review VERIFIED. The goal is not "make it
pass" but to make the finding impossible to reintroduce.

Connected to:
  - imports from: mailflat (SDK), httpx
"""
from __future__ import annotations

import json

import httpx
import pytest

from mailflat import (
    MailFlat,
    MailFlatPermissionError,
    PermissionError as DeprecatedPermissionError,
    RateLimitError,
)

ADDR = "agent@x7k2m.mailflat.net"


def make_client(handler, **kw) -> MailFlat:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://mailflat.net",
                        headers={"X-API-Key": "mf_test_x"})
    return MailFlat(api_key="mf_test_x", http_client=http, **kw)


# ------------------------------------------------------------------ retry safety
def test_send_is_not_retried():
    """POST /send is NOT retried — if the MTA accepts it and returns 504, it would go twice.

    The heart of the finding: the user never learns about the duplicate. So the test counts.
    """
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(504, json={"detail": "gateway timeout"})

    mf = make_client(handler, max_retries=3)
    with pytest.raises(Exception):
        mf.inbox(ADDR).send("peer@example.com", subject="hi")
    assert len(calls) == 1, f"send retried {len(calls)} times; a retried send can deliver twice"


def test_get_is_still_retried():
    """Reads are safe, so retries STAY (otherwise the fix would have killed resilience)."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if len(calls) < 3:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"ok": True, "emails": []})

    mf = make_client(handler, max_retries=3)
    mf.inbox(ADDR).messages()
    assert len(calls) == 3


def test_delete_is_retried_but_send_is_not():
    """DELETE is idempotent (deleting the same mail twice ends the same way) → retried."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        if len(calls) == 1:
            return httpx.Response(502, json={})
        return httpx.Response(200, json={"ok": True})

    mf = make_client(handler, max_retries=2)
    mf.inbox(ADDR).delete_message(5)
    assert calls == ["DELETE", "DELETE"]


def test_retry_after_header_is_honored(monkeypatch):
    """On 429 the server's own delay wins — a fixed backoff must not override it."""
    slept = []
    monkeypatch.setattr("mailflat.client.time.sleep", lambda s: slept.append(s))
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"ok": True, "emails": []})

    mf = make_client(handler, max_retries=2)
    mf.inbox(ADDR).messages()
    assert slept == [7.0], f"expected to wait the server's 7s, waited {slept}"


def test_rate_limit_error_carries_retry_after(monkeypatch):
    """When retries run out, the error CARRIES Retry-After so the caller can schedule."""
    monkeypatch.setattr("mailflat.client.time.sleep", lambda s: None)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, json={"detail": "slow down"})

    mf = make_client(handler, max_retries=0)
    with pytest.raises(RateLimitError) as exc:
        mf.inbox(ADDR).messages()
    assert exc.value.retry_after == 12.0


def test_unparsable_retry_after_falls_back_to_backoff(monkeypatch):
    """We do not guess at an HTTP-date Retry-After; we fall back to our own backoff."""
    slept = []
    monkeypatch.setattr("mailflat.client.time.sleep", lambda s: slept.append(s))
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
                                  json={})
        return httpx.Response(200, json={"ok": True, "emails": []})

    mf = make_client(handler, max_retries=2)
    mf.inbox(ADDR).messages()
    assert slept == [1.0]


# ------------------------------------------------------------------ direction filter
def test_reads_default_to_incoming():
    """latest/messages/wait_for_otp ask for `direction=in` by default.

    This was the bug: without the filter, `wait_for_message()` after `send()` returned the
    agent's OWN mail — the core pattern of agent-to-agent messaging.
    """
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path.endswith("/latest"):
            return httpx.Response(200, json={"ok": True, "email": {"id": 1, "otp_code": "123456"}})
        return httpx.Response(200, json={"ok": True, "emails": []})

    mf = make_client(handler)
    ib = mf.inbox(ADDR)
    ib.messages()
    ib.latest()
    ib.wait_for_otp(timeout=1)
    assert all("direction=in" in url for url in seen), seen


def test_direction_can_be_overridden():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"ok": True, "emails": [], "email": None})

    mf = make_client(handler)
    mf.inbox(ADDR).messages(direction="out")
    mf.inbox(ADDR).latest(direction="all")
    assert "direction=out" in seen[0] and "direction=all" in seen[1]


def test_invalid_direction_rejected_client_side():
    """A wrong value needs no network round trip — the error is immediate and explicit."""
    mf = make_client(lambda req: httpx.Response(200, json={"ok": True}))
    with pytest.raises(ValueError, match="direction"):
        mf.inbox(ADDR).messages(direction="sideways")


# ------------------------------------------------------------------ Message fields
_RICH_EMAIL = {
    "id": 42, "sender": "billing@acme.com", "subject": "Invoice",
    "body_text": "See https://acme.com/inv/1", "body_html": "", "otp_code": None,
    "direction": "in", "is_read": False,
    "links": ["https://acme.com/inv/1"],
    "attachments": [{"id": 9, "filename": "invoice.pdf", "content_type": "application/pdf",
                     "size_bytes": 1234, "is_encrypted": False, "truncated": False}],
    "spam": {"score": 0.4, "required": 5.0, "is_spam": False, "rules": [], "scanner": "sa"},
    "headers": {"Message-Id": "<abc@acme.com>"},
}


def test_message_exposes_links_attachments_spam_headers():
    """The backend already sent these; the SDK swallowed them (the cheapest finding)."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": _RICH_EMAIL})

    msg = make_client(handler).inbox(ADDR).latest()
    assert msg.links == ["https://acme.com/inv/1"]
    assert msg.spam["score"] == 0.4
    assert msg.headers["Message-Id"] == "<abc@acme.com>"
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "invoice.pdf"
    assert msg.attachments[0].size_bytes == 1234


def test_attachment_download_fetches_bytes():
    def handler(req: httpx.Request) -> httpx.Response:
        if "attachments" in req.url.path:
            assert req.url.path.endswith("/messages/42/attachments/9")
            return httpx.Response(200, content=b"%PDF-1.4 fake",
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(200, json={"ok": True, "email": _RICH_EMAIL})

    msg = make_client(handler).inbox(ADDR).latest()
    assert msg.attachments[0].download() == b"%PDF-1.4 fake"


def test_truncated_attachment_explains_itself():
    """Attachment over the cap: metadata exists, bytes do not → a clear error, not empty bytes."""
    email = json.loads(json.dumps(_RICH_EMAIL))
    email["attachments"][0]["truncated"] = True

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": email})

    msg = make_client(handler).inbox(ADDR).latest()
    with pytest.raises(Exception, match="too large"):
        msg.attachments[0].download()


# ------------------------------------------------------------------ M2 surface
def test_mark_read_and_burn_call_expected_endpoints():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json={"ok": True, "burned": 3})

    ib = make_client(handler).inbox(ADDR)
    ib.mark_read(42)
    assert ib.burn()["burned"] == 3
    assert seen == [
        ("POST", f"/api/v1/inboxes/{ADDR}/messages/42/read"),
        ("POST", f"/api/v1/inboxes/{ADDR}/burn"),
    ]


def test_message_mark_read_shortcut():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/latest"):
            return httpx.Response(200, json={"ok": True, "email": _RICH_EMAIL})
        return httpx.Response(200, json={"ok": True})

    msg = make_client(handler).inbox(ADDR).latest()
    msg.mark_read()
    assert seen[-1] == f"/api/v1/inboxes/{ADDR}/messages/42/read"


# ------------------------------------------------------------------ diagnosability
def test_user_agent_reports_sdk_version():
    from mailflat import __version__

    assert MailFlat.user_agent() == f"mailflat-python/{__version__}"
    mf = MailFlat(api_key="mf_test_x")
    assert mf._http.headers["User-Agent"] == f"mailflat-python/{__version__}"
    mf.close()


def test_permission_error_no_longer_shadows_builtin():
    """The new name no longer shadows the built-in; the old name still works as an alias."""
    import builtins

    assert DeprecatedPermissionError is MailFlatPermissionError   # old name still works
    assert MailFlatPermissionError is not builtins.PermissionError  # but does not shadow it
    assert not issubclass(MailFlatPermissionError, builtins.PermissionError)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "not yours"})

    mf = make_client(handler)
    with pytest.raises(MailFlatPermissionError):
        mf.inbox(ADDR).messages()


# ------------------------------------------------------------------ 0.4.1 fixes
# All of these are real findings measured by a second external review.
def test_attachment_download_retries_like_any_get(monkeypatch):
    """`download` sat OUTSIDE the retry loop: every GET tried 3 times, this one only once.

    Subtle, but the inconsistency misleads: on the same network blip a list call recovers
    while an attachment download fails.
    """
    monkeypatch.setattr("mailflat.client.time.sleep", lambda s: None)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if "attachments" not in req.url.path:
            return httpx.Response(200, json={"ok": True, "email": _RICH_EMAIL})
        if len([c for c in calls if "attachments" in c]) < 3:
            return httpx.Response(503, json={})
        return httpx.Response(200, content=b"PDF", headers={"content-type": "application/pdf"})

    msg = make_client(handler, max_retries=3).inbox(ADDR).latest()
    assert msg.attachments[0].download() == b"PDF"
    assert len([c for c in calls if "attachments" in c]) == 3


def test_mark_read_and_burn_retry_but_send_never_does(monkeypatch):
    """Both are POSTs but idempotent: repeating them is harmless. `send` stays single-shot."""
    monkeypatch.setattr("mailflat.client.time.sleep", lambda s: None)

    def flaky(path_marker):
        n = {"c": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            n["c"] += 1
            return httpx.Response(503, json={}) if n["c"] < 2 else httpx.Response(200, json={"ok": True})
        return handler, n

    for label, call in (("mark_read", lambda ib: ib.mark_read(1)), ("burn", lambda ib: ib.burn())):
        handler, n = flaky(label)
        call(make_client(handler, max_retries=2).inbox(ADDR))
        assert n["c"] == 2, f"{label} retry edilmedi"

    sends = []

    def send_handler(req: httpx.Request) -> httpx.Response:
        sends.append(1)
        return httpx.Response(503, json={})

    with pytest.raises(Exception):
        make_client(send_handler, max_retries=3).inbox(ADDR).send("x@example.com")
    assert len(sends) == 1, "send was retried — the duplicate-mail risk is back"


def test_header_lookup_is_case_insensitive():
    """The real key is `Message-ID`; the docs showed `Message-Id` → KeyError.

    Header names are case-insensitive per RFC 5322, but `headers` is a plain dict.
    """
    email = dict(_RICH_EMAIL, headers={"Message-ID": "<abc@acme.com>", "Subject": "Invoice"})

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": email})

    msg = make_client(handler).inbox(ADDR).latest()
    assert msg.message_id == "<abc@acme.com>"
    for spelling in ("Message-ID", "Message-Id", "message-id", "MESSAGE-ID", "message_id"):
        assert msg.header(spelling) == "<abc@acme.com>", spelling
    assert msg.header("X-Nope") is None
    assert msg.header("X-Nope", "fallback") == "fallback"


def test_header_helper_survives_missing_headers():
    """On an encrypted inbox headers arrive as None — the helper must not blow up."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": {"id": 1, "headers": None}})

    msg = make_client(handler).inbox(ADDR).latest()
    assert msg.message_id is None and msg.header("message-id") is None


# ------------------------------------------------------------------ 0.5.1: diagnosable timeout
# Fourth external review: the most expensive part of a missed OTP was not the miss itself but
# that it was UNDIAGNOSABLE — mail had arrived and the error still said "no OTP arrived",
# sending the user to hunt for a delivery problem that did not exist.
def test_otp_timeout_says_nothing_arrived_when_inbox_is_empty():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": None})

    with pytest.raises(Exception) as exc:
        make_client(handler).inbox(ADDR).wait_for_otp(timeout=0)
    assert "No message arrived" in str(exc.value)


def test_otp_timeout_quotes_the_message_when_one_did_arrive():
    """🔒 The two failures must not share a sentence: 'nothing came' vs 'no code in it'."""
    email = {"id": 1, "subject": "Sign in", "body_text": "Use 552134 to sign in",
             "otp_code": None, "direction": "in"}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": email})

    with pytest.raises(Exception) as exc:
        make_client(handler).inbox(ADDR).wait_for_otp(timeout=0)
    msg = str(exc.value)
    assert "Mail arrived" in msg
    assert "no OTP could be extracted" in msg
    assert "Sign in" in msg, "the subject must be quoted so the user can find the message"
    assert "Use 552134 to sign in" in msg, "the body snippet is what lets an agent self-recover"
    assert "inbox.latest().text" in msg, "the message must say what to do next"


def test_otp_timeout_handles_a_message_with_no_body():
    """A subject-only message must not break the message builder."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True,
                                         "email": {"id": 1, "subject": None, "body_text": None}})

    with pytest.raises(Exception) as exc:
        make_client(handler).inbox(ADDR).wait_for_otp(timeout=0)
    assert "Mail arrived" in str(exc.value) and "(no subject)" in str(exc.value)


# ------------------------------------------------------------------ reply / threading
def test_reply_fills_recipient_subject_and_thread_headers():
    """🔒 A reply must land in the SAME conversation, not open a new one."""
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/send"):
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": True, "email": {
            "id": 5, "sender": "human@gmail.com", "subject": "Question",
            "body_text": "can you help?", "headers": {"Message-ID": "<abc@mail.gmail.com>"}}})

    msg = make_client(handler).inbox(ADDR).latest()
    msg.reply("sure, here you go")
    assert sent["to"] == "human@gmail.com"
    assert sent["subject"] == "Re: Question"
    assert sent["in_reply_to"] == "<abc@mail.gmail.com>"
    assert sent["body"] == "sure, here you go"


def test_reply_does_not_stack_re_prefixes():
    """"Re: Re: Re:" hurts threading in some clients as much as a missing prefix."""
    from mailflat.inbox import _reply_subject
    assert _reply_subject("Question") == "Re: Question"
    assert _reply_subject("Re: Question") == "Re: Question"
    assert _reply_subject("RE: Question") == "RE: Question"
    assert _reply_subject(None) == "Re:"
    assert _reply_subject("   ") == "Re:"


def test_reply_without_thread_id_still_sends():
    """An encrypted inbox stores no headers; a reply must degrade, not crash."""
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/send"):
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": True, "email": {
            "id": 6, "sender": "human@gmail.com", "subject": "Hi", "headers": None}})

    make_client(handler).inbox(ADDR).latest().reply("ok")
    assert sent["to"] == "human@gmail.com" and "in_reply_to" not in sent


def test_reply_refuses_when_there_is_no_sender():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": {"id": 7, "sender": None}})

    with pytest.raises(ValueError, match="no sender"):
        make_client(handler).inbox(ADDR).latest().reply("ok")


# ------------------------------------------------------------------ reply target (5th review)
# `.sender` is the SMTP envelope sender. For transactional mail that is a bounce address, so a
# reply sent there reaches a bounce processor and the human never sees it.
def _msg_with(headers, sender="bounces+abc@sendgrid.net"):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/send"):
            return httpx.Response(200, json={"ok": True, "echo": json.loads(req.content)})
        return httpx.Response(200, json={"ok": True, "email": {
            "id": 9, "sender": sender, "subject": "Welcome", "headers": headers}})
    return make_client(handler).inbox(ADDR).latest()


def test_reply_prefers_reply_to_over_envelope_sender():
    """🔒 A reply must reach a person, not the bounce address in MAIL FROM."""
    msg = _msg_with({"Reply-To": "Support <support@acme.com>", "From": "Acme <no-reply@acme.com>"})
    assert msg.reply_to_address == "support@acme.com"
    assert msg.reply("hi")["echo"]["to"] == "support@acme.com"


def test_reply_falls_back_to_from_when_no_reply_to():
    msg = _msg_with({"From": "Acme Billing <billing@acme.com>"})
    assert msg.reply_to_address == "billing@acme.com"


def test_reply_falls_back_to_envelope_sender_as_last_resort():
    """Encrypted inbox: no headers at all — replying to the envelope beats not replying."""
    msg = _msg_with(None, sender="human@gmail.com")
    assert msg.reply_to_address == "human@gmail.com"
    assert msg.reply("hi")["echo"]["to"] == "human@gmail.com"


def test_reply_to_handles_a_repeated_header():
    """Some servers send Reply-To twice; the parser stores a list."""
    msg = _msg_with({"Reply-To": ["first@acme.com", "second@acme.com"]})
    assert msg.reply_to_address == "first@acme.com"


def test_reply_to_ignores_a_malformed_header():
    msg = _msg_with({"Reply-To": "not-an-address", "From": "Real <real@acme.com>"})
    assert msg.reply_to_address == "real@acme.com"


def test_reply_subject_repairs_control_characters_it_did_not_write():
    """🔒 A stranger's subject must not make a message permanently unanswerable.

    Inbound mail is parsed with encoded-words decoded, so `=?utf-8?B?YQ0KYg==?=` is stored
    as a subject containing a real CRLF. This helper prefixed `Re: ` and handed it back, the
    server's header guard rejected it, and `reply()` failed forever on that message — for
    anyone, triggered by anyone who can send mail to a MailFlat inbox.

    Same rule as the length axis, which was already applied here: a value WE derived gets
    repaired, a value the CALLER wrote gets rejected, because only the caller can fix theirs.
    """
    from mailflat.inbox import _reply_subject

    out = _reply_subject("x\r\nBcc: victim@example.com")
    assert "\r" not in out and "\n" not in out
    assert out.startswith("Re: x")
    assert "Bcc: victim@example.com" in out, "the text was dropped, not repaired"
    # A space, not deletion: "a\r\nb" must not silently become one word.
    assert _reply_subject("a\r\nb") == "Re: a b"
    # Tab is horizontal space; it does not split a header line and survives.
    assert "\t" in _reply_subject("a\tb")
