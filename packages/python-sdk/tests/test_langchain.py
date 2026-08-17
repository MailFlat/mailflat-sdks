"""mailflat.langchain tests — MailFlatToolkit tools driven by an injected mock client.

Runs when `langchain-core` is installed (skipped otherwise). Verifies that each tool makes the
right /api/v1 call and returns the expected result, and that the toolkit produces LangChain
BaseTool objects.

Connected to:
  - imports from: mailflat (SDK), mailflat.langchain, httpx
"""
from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("langchain_core")  # skip this module when the extra is not installed

from mailflat import MailFlat
from mailflat.langchain import MailFlatToolkit

ADDR = "signup-test@x7k2m.mailflat.net"


def make_toolkit(handler) -> MailFlatToolkit:
    """Build a MailFlatToolkit backed by MockTransport using the given handler."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport, base_url="https://mailflat.net", headers={"X-API-Key": "mf_test_x"}
    )
    return MailFlatToolkit(client=MailFlat(api_key="mf_test_x", http_client=http))


def _tools_by_name(toolkit: MailFlatToolkit) -> dict:
    return {t.name: t for t in toolkit.get_tools()}


# --------------------------------------------------------------------- shape
def test_get_tools_returns_the_full_named_tool_set():
    """The toolkit's tool set is fixed here on purpose — a tool appearing or disappearing
    changes what the model can do, which is a product change, not an implementation detail.

    ⚠️ This lock was RED and unnoticed: it still asserted the six tools of an early version
    while the toolkit had grown to ten (`wait_for_message`, `reply`, `mark_read`,
    `burn_inbox` were added without updating it). A shape lock nobody runs locks nothing —
    see the note about `packages/` tests in projectMDs/214.
    """
    toolkit = make_toolkit(lambda req: httpx.Response(200, json={}))
    tools = _tools_by_name(toolkit)
    assert set(tools) == {
        "create_inbox",
        "list_inboxes",
        "read_messages",
        "wait_for_otp",
        "wait_for_message",
        "send_email",
        "reply",
        "wait_until_sent",
        "mark_read",
        "burn_inbox",
        "delete_inbox",
        "delete_message",
    }
    # delete_message used to be withheld here, with the rationale that "a model which can
    # delete single messages can destroy evidence of what it did". The list above refutes
    # it: delete_inbox is granted, and it destroys every message AND the address. Denying
    # the smaller capability while granting the larger one bought no safety and left this
    # toolkit the only model surface without a tool MCP and the AI SDK have had since day 42.
    # Every tool must carry a description and a usable args schema (LangChain BaseTool).
    for tool in tools.values():
        assert tool.description
        assert tool.args_schema is not None


# -------------------------------------------------------------------- create
def test_create_inbox():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/api/v1/inboxes"
        assert json.loads(req.content) == {"label": "deep-research"}
        return httpx.Response(200, json={"ok": True, "address": ADDR, "name": "deep-research"})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["create_inbox"].invoke({"label": "deep-research"})
    assert out["address"] == ADDR


# ---------------------------------------------------------------------- list
def test_list_inboxes():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/inboxes"
        return httpx.Response(200, json={"ok": True, "inboxes": [{"address": ADDR}]})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["list_inboxes"].invoke({})
    assert out["inboxes"][0]["address"] == ADDR


# --------------------------------------------------------------------- read
def test_read_messages():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/messages"
        return httpx.Response(200, json={"ok": True, "emails": [{"id": 1, "subject": "Hi"}]})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["read_messages"].invoke({"address": ADDR})
    assert out["emails"][0]["subject"] == "Hi"


# ------------------------------------------------------------------ wait_otp
def test_wait_for_otp_success():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/latest"
        return httpx.Response(200, json={"email": {"otp_code": "987654"}})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_for_otp"].invoke({"address": ADDR, "timeout": 5})
    assert out["otp_code"] == "987654"


def test_wait_for_otp_timeout():
    handler = lambda req: httpx.Response(200, json={"email": None})
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_for_otp"].invoke({"address": ADDR, "timeout": 0})
    assert out["otp_code"] is None and out["error"] == "timeout"


def test_wait_for_otp_encrypted():
    handler = lambda req: httpx.Response(200, json={"encrypted": True, "note": "E2E inbox"})
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_for_otp"].invoke({"address": ADDR, "timeout": 5})
    assert out["otp_code"] is None and out["encrypted"] is True


# --------------------------------------------------------------------- send
def test_send_email():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/send"
        assert json.loads(req.content) == {"to": "x@y.com", "subject": "Hi", "body": "Yo"}
        return httpx.Response(200, json={"ok": True, "status": "sent"})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["send_email"].invoke(
        {"address": ADDR, "to": "x@y.com", "subject": "Hi", "body": "Yo"}
    )
    assert out["status"] == "sent"


def test_send_email_passes_cc_and_bcc():
    """cc/bcc reach the wire under their real names — a rename here surfaces as a 422."""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(202, json={"ok": True, "queued": True, "message_id": 3})

    tools = _tools_by_name(make_toolkit(handler))
    tools["send_email"].invoke({"address": ADDR, "to": "x@y.com", "body": "Yo",
                                "cc": ["w@y.com"], "bcc": ["s@y.com"]})
    assert seen[0]["cc"] == ["w@y.com"] and seen[0]["bcc"] == ["s@y.com"]


def test_model_facing_tools_take_no_attachments():
    """🔒 K7: file bytes must never cross the MODEL surface.

    cc/bcc are addresses — short strings a model can reasonably choose. An attachment is
    bytes: it would travel through the model's context, costing tokens and being plainly
    impossible for a multi-megabyte file. Files are attached from the SDK
    (`inbox.send(..., attachments=[...])`), which is code, not a model decision.
    """
    tools = _tools_by_name(make_toolkit(lambda req: httpx.Response(200, json={})))
    for name in ("send_email", "reply"):
        fields = set(tools[name].args_schema.model_json_schema()["properties"])
        assert "attachments" not in fields, f"{name} exposes attachments to the model"
        assert {"cc", "bcc"} <= fields, f"{name} is missing cc/bcc"


# ------------------------------------------------------------------- delete
def test_delete_inbox():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert req.url.path == f"/api/v1/inboxes/{ADDR}"
        return httpx.Response(200, json={"ok": True})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["delete_inbox"].invoke({"address": ADDR})
    assert out["ok"] is True


def test_delete_message_hits_the_single_message_endpoint():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/messages/7"
        return httpx.Response(200, json={"ok": True, "message": "Email deleted"})

    tools = _tools_by_name(make_toolkit(handler))
    out = tools["delete_message"].invoke({"address": ADDR, "message_id": 7})
    assert out["message"] == "Email deleted"


# -------------------------------------------------------------- wait_until_sent
def _status_handler(status, error=None):
    """A backend whose single message reports the given send_status."""
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/messages/5"
        return httpx.Response(200, json={"ok": True, "email": {
            "id": 5, "direction": "out", "send_status": status, "send_error": error,
            "subject": "hi", "is_encrypted": False}})
    return handler


def test_wait_until_sent_reports_delivery():
    tools = _tools_by_name(make_toolkit(_status_handler("sent")))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 5})
    assert out["delivered"] is True
    assert out["status"] == "sent"
    assert out["timed_out"] is False
    assert out["message"]["id"] == 5


def test_wait_until_sent_treats_unsigned_as_delivered():
    """`unsigned` means it went out without a DKIM signature — it still went out.

    Reporting it as undelivered would push an agent to resend a mail the recipient has.
    """
    tools = _tools_by_name(make_toolkit(_status_handler("unsigned")))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 5})
    assert out["delivered"] is True
    assert out["status"] == "unsigned"


def test_wait_until_sent_reports_permanent_failure_with_the_reason():
    tools = _tools_by_name(make_toolkit(_status_handler("failed", "550 unknown mailbox")))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 5})
    assert out["delivered"] is False
    assert out["timed_out"] is False
    assert out["status"] == "failed"
    assert "550 unknown mailbox" in out["error"]


def test_wait_until_sent_timeout_does_not_read_as_failure():
    """🔒 The reason this tool exists.

    A queued mail is not a lost mail. If this answer contains the word "failed", or omits
    the instruction not to resend, the model's reasonable next move is to send again — and
    the recipient gets the mail twice. Duplicate delivery, not silent loss, is the failure
    mode this whole surface is defending against.
    """
    tools = _tools_by_name(make_toolkit(_status_handler("queued")))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 0})
    assert out["timed_out"] is True
    assert out["delivered"] is False
    assert out["status"] == "queued"
    assert "fail" not in json.dumps(out).lower()
    assert "do not send it again" in out["note"].lower()


def test_retrying_does_not_read_as_queued():
    """🔒 `queued` and `retrying` are different answers to "should I keep waiting?".

    The queue went to the trouble of separating "never attempted" from "attempted and
    scheduled again", the status field carried it, and the SENTENCE said "Still queued" for
    both — putting the distinction back into a field a model reads past (B-099). Two
    external test rounds reported it independently on the same day.
    """
    handler = _status_handler("retrying", "Could not reach example.invalid: timed out")
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 0})
    assert out["status"] == "retrying"
    assert "Still queued" not in out["note"]
    assert "attempted" in out["note"].lower()
    assert "do not send it again" in out["note"].lower()
    assert "fail" not in json.dumps(out).lower()


def test_the_last_delivery_error_reaches_the_model():
    """🔒 `last_error` is the evidence behind "keep waiting".

    `errors.py` states the reason the field exists: the difference between "greylisted, try
    again in two minutes" and a problem no amount of waiting will fix. It sat on the
    exception and never reached the payload, so the one surface that could not tell those
    apart was the model surface — the one the distinction was added for.
    """
    handler = _status_handler("retrying", "Could not reach example.invalid: timed out")
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 0})
    assert "Could not reach example.invalid" in (out["error"] or "")


def test_every_branch_answers_with_the_same_keys():
    """🔒 One shape for all outcomes, read from SEND_RESULT_KEYS itself.

    The old lock was a hand-written copy of the list inside one TypeScript test, covering
    one branch. A copy agrees with itself forever; the branch that had drifted was the one
    it did not cover, on a surface it did not call (B-097).
    """
    from mailflat.agent_results import SEND_RESULT_KEYS

    shapes = set()
    for status, error in (("sent", None), ("queued", None), ("retrying", "temporary"),
                          ("failed", "550 unknown mailbox")):
        tools = _tools_by_name(make_toolkit(_status_handler(status, error)))
        out = tools["wait_until_sent"].invoke(
            {"address": ADDR, "message_id": 5, "timeout": 0})
        assert set(out) == set(SEND_RESULT_KEYS), f"{status} branch has a different shape"
        shapes.add(tuple(sorted(out)))
    assert len(shapes) == 1


def test_an_api_key_in_a_rejection_sentence_is_redacted():
    """🔒 The branch carrying text we do NOT author was the unredacted one.

    Only the success branch went through `redact_secrets`, and `redact.py` names "an error
    sentence" among the cases value scanning exists for. The remote mail server writes that
    sentence; we hand it to the model (B-098).
    """
    canary = "mf_live_CANARY123456789"
    handler = _status_handler("failed", f"would not accept {canary}@x: 550 No such user")
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["wait_until_sent"].invoke({"address": ADDR, "message_id": 5, "timeout": 0})
    assert canary not in json.dumps(out)
    assert "[redacted]" in json.dumps(out)


# ----------------------------------------------------------------- err guard
def test_error_returned_not_raised():
    handler = lambda req: httpx.Response(401, json={"detail": "Invalid API key"})
    tools = _tools_by_name(make_toolkit(handler))
    out = tools["create_inbox"].invoke({"label": "x"})
    assert "Invalid API key" in out["error"]


# ================================================== secret redaction (B-055)
def test_tool_output_never_carries_the_inbox_api_key():
    """🔒 LangChain tool output reaches the model (and LangSmith traces) — no keys allowed.

    `create_inbox` used to return the backend payload as-is, and `list_inboxes` dumped every
    inbox key on the account in a single call.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/inboxes" and req.method == "POST":
            return httpx.Response(200, json={
                "ok": True, "address": ADDR, "name": "leak-test",
                "api_key": "mf_sk_should_never_reach_the_model", "retention_hours": 2,
            })
        return httpx.Response(200, json={"ok": True, "inboxes": [
            {"address": ADDR, "api_key": "mf_sk_one"},
            {"address": "b@x.net", "api_key": "mf_sk_two"},
        ]})

    tools = _tools_by_name(make_toolkit(handler))

    created = tools["create_inbox"].invoke({"label": "leak-test"})
    assert "mf_sk_" not in json.dumps(created), created
    assert created["address"] == ADDR and created["retention_hours"] == 2   # useful fields survive

    listed = tools["list_inboxes"].invoke({})
    assert "mf_sk_" not in json.dumps(listed), listed
    assert [i["address"] for i in listed["inboxes"]] == [ADDR, "b@x.net"]


def test_sdk_client_still_sees_the_key():
    """The split: SDK = code surface (key present), tool = model surface (key absent)."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "address": ADDR, "api_key": "mf_sk_visible"})

    toolkit = make_toolkit(handler)
    inbox = toolkit._client.create(label="sdk-side")
    assert inbox.api_key == "mf_sk_visible"
