"""MailFlat Python SDK tests — real /api/v1 responses are faked with httpx.MockTransport.

Connected to:
  - imports from: mailflat (SDK), httpx

Covers: create/list/messages/latest/send/delete + wait_for_otp (success/timeout/encrypted)
+ error mapping (401/403/404/429) + the env API key.
"""
from __future__ import annotations

import json

import httpx
import pytest

from mailflat import (
    AuthenticationError,
    EncryptedInboxError,
    MailFlat,
    MailFlatError,
    NotFoundError,
    OTPTimeoutError,
    PermissionError,
    RateLimitError,
)

ADDR = "signup-test@x7k2m.mailflat.net"


def make_client(handler) -> MailFlat:
    """Build a MailFlat client backed by MockTransport using the given handler."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://mailflat.net",
                        headers={"X-API-Key": "mf_test_x"})
    return MailFlat(api_key="mf_test_x", http_client=http)


# --------------------------------------------------------------------- create
def test_create_returns_inbox():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/api/v1/inboxes"
        assert req.headers["X-API-Key"] == "mf_test_x"
        body = json.loads(req.content)
        assert body == {"label": "signup-test"}
        return httpx.Response(200, json={
            "ok": True, "address": ADDR, "api_key": "ik_1",
            "name": "signup-test", "retention_hours": 2,
        })

    mf = make_client(handler)
    inbox = mf.create(label="signup-test")
    assert inbox.address == ADDR
    assert inbox.name == "signup-test"
    assert inbox.retention_hours == 2


def test_create_inbox_alias_and_full_payload():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "address": ADDR})

    mf = make_client(handler)
    mf.create_inbox(prefix="bob", subdomain="acme", retention_hours=6)
    assert seen["body"] == {"prefix": "bob", "subdomain": "acme", "retention_hours": 6}


# ----------------------------------------------------------------------- list
def test_list_inboxes():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET" and req.url.path == "/api/v1/inboxes"
        return httpx.Response(200, json={"ok": True, "inboxes": [
            {"address": ADDR, "name": "a", "retention_hours": 2, "via_api": True},
            {"address": "b@x.mailflat.net", "name": "b"},
        ]})

    mf = make_client(handler)
    inboxes = mf.list()
    assert [i.address for i in inboxes] == [ADDR, "b@x.mailflat.net"]
    assert inboxes[0].via_api is True


# ------------------------------------------------------------------- messages
def test_messages_and_latest():
    email = {"id": 1, "sender": "no-reply@figma.com", "subject": "Your code",
             "body_text": "Code: 123456", "otp_code": "123456", "direction": "in",
             "is_encrypted": False, "received_at": "2026-06-21T00:00:00"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"ok": True, "emails": [email]})
        return httpx.Response(200, json={"ok": True, "email": email})

    mf = make_client(handler)
    inbox = mf.inbox(ADDR)
    msgs = inbox.messages()
    assert len(msgs) == 1
    assert msgs[0].otp == "123456"
    assert msgs[0].text == "Code: 123456"
    assert inbox.latest().subject == "Your code"


def test_latest_none_when_empty():
    mf = make_client(lambda req: httpx.Response(200, json={"ok": True, "email": None}))
    assert mf.inbox(ADDR).latest() is None


# ----------------------------------------------------------------- wait_for_otp
def test_wait_for_otp_success():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:  # first two polls come back empty
            return httpx.Response(200, json={"ok": True, "email": None})
        return httpx.Response(200, json={"ok": True, "email": {"otp_code": "987654"}})

    mf = make_client(handler)
    otp = mf.inbox(ADDR).wait_for_otp(timeout=10, poll_interval=0)
    assert otp == "987654"
    assert calls["n"] == 3


def test_wait_for_otp_timeout():
    mf = make_client(lambda req: httpx.Response(200, json={"ok": True, "email": None}))
    with pytest.raises(OTPTimeoutError):
        mf.inbox(ADDR).wait_for_otp(timeout=0, poll_interval=0)


def test_wait_for_otp_encrypted_inbox():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "ok": True, "encrypted": True, "note": "end-to-end encrypted",
            "email": {"is_encrypted": True},
        })

    mf = make_client(handler)
    with pytest.raises(EncryptedInboxError):
        mf.inbox(ADDR).wait_for_otp(timeout=5, poll_interval=0)


# ------------------------------------------------------------------- send/delete
def test_send():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path.endswith("/send")
        assert json.loads(req.content) == {
            "to": "x@y.com", "subject": "Hi", "body": "Hello", "html": "<b>Hello</b>"}
        return httpx.Response(200, json={"ok": True, "message": "Sent to x@y.com"})

    mf = make_client(handler)
    res = mf.inbox(ADDR).send("x@y.com", subject="Hi", body="Hello", html="<b>Hello</b>")
    assert res["ok"] is True


def test_delete():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        return httpx.Response(200, json={"ok": True, "message": "Inbox deleted"})

    mf = make_client(handler)
    assert mf.inbox(ADDR).delete()["message"] == "Inbox deleted"


def test_delete_message():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/messages/42"
        return httpx.Response(200, json={"ok": True, "message": "Email deleted"})

    mf = make_client(handler)
    assert mf.inbox(ADDR).delete_message(42)["message"] == "Email deleted"


def test_message_delete_sugar():
    # .delete() on a Message returned by latest() must hit the right endpoint
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"ok": True, "email": {"id": 7, "subject": "Hi"}})
        assert req.method == "DELETE" and req.url.path.endswith("/messages/7")
        return httpx.Response(200, json={"ok": True, "message": "Email deleted"})

    mf = make_client(handler)
    msg = mf.inbox(ADDR).latest()
    assert msg is not None and msg.id == 7
    assert msg.delete()["message"] == "Email deleted"


# ----------------------------------------------------------------------- errors
@pytest.mark.parametrize("status,exc", [
    (401, AuthenticationError),
    (403, PermissionError),
    (404, NotFoundError),
    (429, RateLimitError),
])
def test_error_mapping(status, exc):
    mf = make_client(lambda req: httpx.Response(status, json={"detail": "nope"}))
    # It must still raise after the retries are exhausted → retries=0 keeps it fast
    mf.max_retries = 0
    with pytest.raises(exc):
        mf.inbox(ADDR).messages()


def test_200_with_error_field_raises():
    mf = make_client(lambda req: httpx.Response(200, json={"error": "boom"}))
    with pytest.raises(MailFlatError):
        mf.inbox(ADDR).messages()


# -------------------------------------------------------------------- api key
def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("MAILFLAT_API_KEY", "mf_env")
    mf = MailFlat()
    assert mf.api_key == "mf_env"
    mf.close()


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("MAILFLAT_API_KEY", raising=False)
    with pytest.raises(MailFlatError):
        MailFlat()
