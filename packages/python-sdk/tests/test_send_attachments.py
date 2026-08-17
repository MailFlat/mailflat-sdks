"""Outgoing attachments, cc/bcc and wait_until_sent — the Python side of phase 3b.

What these prove (behaviour, not shape):
  - An agent never has to base64 anything: a file path is read, its type is guessed from
    the extension, and the bytes go out encoded. Hiding that is most of what an SDK is for.
  - `reply()` accepts everything `send()` accepts. Answering a mail with a file attached is
    the normal case; without this an agent would have to drop back to `send()` and lose the
    threading headers that make a reply look like a reply.
  - `wait_until_sent()` never returns quietly on failure. `send()` is asynchronous, so a
    helper that hands back a failed mail as if nothing happened is how mail goes missing.
  - HTTP 202 is a success. The endpoint answers "accepted", not "delivered"; a client that
    treats 202 as an error would break every send.

Connected to:
  - exercises: mailflat.inbox (_encode_attachment, send, reply, wait_until_sent, message),
               mailflat.errors (SendFailedError, SendTimeoutError)
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from mailflat import MailFlat, SendFailedError, SendTimeoutError

ADDR = "agent@x7k2m.mailflat.net"
PDF = b"%PDF-1.4\ninvoice bytes\n%%EOF\n"


def make_client(handler) -> MailFlat:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://mailflat.net",
                        headers={"X-API-Key": "mf_test_x"})
    return MailFlat(api_key="mf_test_x", http_client=http)


def capture_send(status: int = 202, payload: dict | None = None):
    """Client + a list that receives every request body sent to /send."""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(status, json=payload or {"ok": True, "queued": True,
                                                       "message_id": 7})

    return make_client(handler), seen


# ------------------------------------------------------------------ encoding

def test_send_reads_a_file_path_and_encodes_it(tmp_path):
    """🔒 The whole point of the ergonomics: hand it a path, it does the rest."""
    path = tmp_path / "invoice.pdf"
    path.write_bytes(PDF)

    client, seen = capture_send()
    client.inbox(ADDR).send("peer@example.com", attachments=[str(path)])

    att = seen[0]["attachments"][0]
    assert att["filename"] == "invoice.pdf"
    assert att["content_type"] == "application/pdf"      # guessed from the extension
    assert base64.b64decode(att["content_b64"]) == PDF   # bytes survive the round trip


def test_send_accepts_raw_bytes_and_ready_made_base64():
    client, seen = capture_send()
    client.inbox(ADDR).send(
        "peer@example.com",
        attachments=[
            {"filename": "a.pdf", "content": PDF},
            {"filename": "b.txt", "content_b64": base64.b64encode(b"hello").decode()},
        ],
    )
    a, b = seen[0]["attachments"]
    assert base64.b64decode(a["content_b64"]) == PDF
    assert base64.b64decode(b["content_b64"]) == b"hello"
    assert b["content_type"] == "text/plain"


def test_attachment_without_content_is_rejected_before_the_request():
    """A missing body must fail here, not as a confusing 422 from the server."""
    client, seen = capture_send()
    with pytest.raises(TypeError, match="content"):
        client.inbox(ADDR).send("peer@example.com", attachments=[{"filename": "a.pdf"}])
    assert seen == [], "invalid attachment still reached the network"


def test_unreadable_path_names_the_file():
    client, _ = capture_send()
    with pytest.raises(ValueError, match="nope.pdf"):
        client.inbox(ADDR).send("peer@example.com", attachments=["/nonexistent/nope.pdf"])


def test_no_attachment_key_when_none_given():
    """An empty list must not appear in the payload — silence is the default contract."""
    client, seen = capture_send()
    client.inbox(ADDR).send("peer@example.com", subject="hi")
    assert "attachments" not in seen[0] and "cc" not in seen[0] and "bcc" not in seen[0]


# --------------------------------------------------------------------- cc/bcc

def test_cc_and_bcc_are_sent_under_the_wire_names():
    client, seen = capture_send()
    client.inbox(ADDR).send("peer@example.com", cc=["watcher@example.com"],
                            bcc=["secret@example.com"])
    assert seen[0]["cc"] == ["watcher@example.com"]
    assert seen[0]["bcc"] == ["secret@example.com"]


# ---------------------------------------------------------------------- reply

def test_reply_carries_attachments_cc_and_bcc(tmp_path):
    """🔒 K6: reply() takes the same surface as send().

    Without this an agent answering "here is the invoice you asked for" would have to use
    send(), which starts a NEW conversation in the recipient's client.
    """
    path = tmp_path / "report.pdf"
    path.write_bytes(PDF)
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/send"):
            seen.append(json.loads(req.content))
            return httpx.Response(202, json={"ok": True, "queued": True, "message_id": 9})
        return httpx.Response(200, json={"emails": [{
            "id": 1, "sender": "human@gmail.com", "subject": "Invoice?",
            "headers": {"Message-ID": "<abc@gmail.com>", "From": "human@gmail.com"},
        }]})

    client = make_client(handler)
    message = client.inbox(ADDR).messages(direction="all")[0]
    message.reply("attached", cc=["boss@example.com"], bcc=["audit@example.com"],
                  attachments=[str(path)])

    body = seen[0]
    assert body["in_reply_to"] == "<abc@gmail.com>"        # still a threaded reply
    assert body["cc"] == ["boss@example.com"]
    assert body["bcc"] == ["audit@example.com"]
    assert base64.b64decode(body["attachments"][0]["content_b64"]) == PDF


# ------------------------------------------------------------- 202 + waiting

def test_202_is_not_an_error():
    """🔒 The endpoint returns 202 Accepted. A client treating that as failure breaks
    every send — and this is a regression lock, since the status changed under us."""
    client, _ = capture_send(status=202)
    res = client.inbox(ADDR).send("peer@example.com")
    assert res["queued"] is True


def _status_client(statuses: list[dict]):
    """Serves one message payload per poll, in order (last one repeats)."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True, "email": {"id": 7, **statuses[index]}})

    return make_client(handler), calls


def test_wait_until_sent_returns_when_delivered():
    client, calls = _status_client([{"send_status": "queued"}, {"send_status": "sent"}])
    message = client.inbox(ADDR).wait_until_sent(7, timeout=5, poll_interval=0.01)
    assert message.send_status == "sent"
    assert calls["n"] == 2, "did not poll again after seeing 'queued'"


def test_wait_until_sent_accepts_unsigned_as_delivered():
    """`unsigned` means delivered without a DKIM signature — delivered nonetheless."""
    client, _ = _status_client([{"send_status": "unsigned"}])
    assert client.inbox(ADDR).wait_until_sent(7, timeout=1).send_status == "unsigned"


def test_wait_until_sent_raises_on_permanent_failure():
    """🔒 No silent failure: the server's reason must reach the caller as an exception."""
    client, _ = _status_client([{"send_status": "failed",
                                 "send_error": "550 user unknown"}])
    with pytest.raises(SendFailedError, match="550 user unknown") as exc:
        client.inbox(ADDR).wait_until_sent(7, timeout=1)
    assert exc.value.message_id == 7 and exc.value.status == "failed"


def test_wait_until_sent_raises_on_timeout_and_says_it_may_still_arrive():
    """Timeout is NOT failure: the queue keeps retrying, and the message says so —
    an agent that treats a timeout as 'lost' would resend and double-deliver."""
    client, _ = _status_client([{"send_status": "queued"}])
    with pytest.raises(SendTimeoutError, match="still be delivered") as exc:
        client.inbox(ADDR).wait_until_sent(7, timeout=0.05, poll_interval=0.01)
    assert exc.value.status == "queued"


def test_message_by_id_hits_the_single_message_endpoint():
    """Checking one send must not pull the whole outbound list (100 mails → 100 per check)."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json={"ok": True, "email": {"id": 42, "subject": "x"}})

    client = make_client(handler)
    assert client.inbox(ADDR).message(42).id == 42
    assert seen == [f"/api/v1/inboxes/{ADDR}/messages/42"]


def test_wait_until_sent_timeout_reports_what_the_last_attempt_said():
    """A timeout that describes nothing is a timeout the caller cannot act on.

    The queue distinguishes "accepted, never attempted" (`queued`) from "attempted, did
    not get through" (`retrying`), and on the second one it knows WHY. Before this, both
    produced the same sentence — "it may still be delivered" — which was true but useless:
    greylisting held for two minutes and a recipient nobody will ever reach read alike.
    """
    client, _ = _status_client([{"send_status": "retrying",
                                 "send_error": "451 4.7.1 Greylisted, try again later"}])
    with pytest.raises(SendTimeoutError) as excinfo:
        client.inbox(ADDR).wait_until_sent(7, timeout=0.05, poll_interval=0.01)

    err = excinfo.value
    assert err.status == "retrying"
    assert err.last_error == "451 4.7.1 Greylisted, try again later"
    assert "451 4.7.1 Greylisted" in str(err)
    # Still NOT a failure: the sentence must keep saying the queue is working on it.
    assert "keeps retrying" in str(err)


def test_wait_until_sent_timeout_says_nothing_extra_when_never_attempted():
    """`queued` with no attempt yet has no reason to report — don't invent one."""
    client, _ = _status_client([{"send_status": "queued"}])
    with pytest.raises(SendTimeoutError) as excinfo:
        client.inbox(ADDR).wait_until_sent(7, timeout=0.05, poll_interval=0.01)

    assert excinfo.value.last_error is None
    assert "last attempt reported" not in str(excinfo.value)
