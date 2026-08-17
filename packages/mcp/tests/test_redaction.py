"""Does tool output leak secrets — guards against a repeat of B-055.

What it protects: `create_inbox` and `list_inboxes` returned the backend payload as-is, and
that payload carried a per-inbox `api_key` (`mf_sk_…`). MCP tool output goes straight into the
MODEL's context, and from there into prompt logs, the client transcript and the model's later
answers. `list_inboxes` dumped every inbox key on the account in a single call.

The shape of the test is deliberate: checking tools one by one is not enough, because the real
risk is the tool added TOMORROW forgetting redaction. So it first asserts "registered tools =
tools exercised here"; adding a tool turns this file red.

Connected to:
  - tests: mailflat_mcp.server (every @mcp.tool) → mailflat.redact

Running it: cd packages/mcp && python -m pytest tests/test_redaction.py
"""
import asyncio
import json

from mailflat_mcp import server

from test_mcp import ADDR, patched  # noqa: F401  (the patched fixture is used)

#: Prefix of the per-inbox key the backend generates.
INBOX_KEY_PREFIX = "mf_sk_"


def _calls(addr: str) -> dict:
    """One call per tool. The dict key is the registered tool name."""
    return {
        "create_inbox": lambda: server.create_inbox(label="redact"),
        "list_inboxes": lambda: server.list_inboxes(),
        "read_messages": lambda: server.read_messages(addr),
        "wait_for_otp": lambda: server.wait_for_otp(addr, timeout=0),
        "wait_for_message": lambda: server.wait_for_message(addr, timeout=0),
        "send_email": lambda: server.send_email(addr, to="x@example.com", subject="s", body="b"),
        "reply": lambda: server.reply(addr, 1, body="ok"),
        # timeout=0 keeps it fast; the returned payload embeds no message, but the FAILURE
        # path of this tool does carry server text, and that is scanned below too.
        "wait_until_sent": lambda: server.wait_until_sent(addr, 1, timeout=0),
        "mark_read": lambda: server.mark_read(addr, 1),
        "burn_inbox": lambda: server.burn_inbox(addr),
        "delete_message": lambda: server.delete_message(addr, 1),
        "delete_inbox": lambda: server.delete_inbox(addr),  # en sonda: kutuyu siler
    }


def test_every_registered_tool_is_covered_by_this_file(patched):
    """🔒 Adding a tool turns this red — no tool can slip past the leak test."""
    registered = {t.name for t in asyncio.run(server.mcp.list_tools())}
    covered = set(_calls(ADDR))
    assert registered == covered, (
        f"uncovered tools: {registered - covered} · unknown: {covered - registered}"
    )


def test_no_tool_output_contains_an_inbox_api_key(patched):
    """🔒 No tool output may contain an `api_key` field or an `mf_sk_` value."""
    addr = server.create_inbox(label="seed")["address"]
    patched.deliver(addr, sender="s", subject="Verify", body_text="code 111222", otp_code="111222")

    for name, call in _calls(addr).items():
        # Tools now RAISE on failure (MCP isError). An error message can leak just as easily
        # as a result, so the message is scanned too.
        try:
            blob = json.dumps(call(), default=str)
        except Exception as exc:      # noqa: BLE001 - the message is what we are inspecting
            blob = str(exc)
        assert INBOX_KEY_PREFIX not in blob, f"{name} returned an inbox key to the model: {blob[:200]}"
        assert "api_key" not in blob, f"{name} output still carries an api_key field: {blob[:200]}"


def test_list_inboxes_was_the_worst_case_and_is_clean(patched):
    """This was the tool that could dump every key at once; inboxes still list, keys do not."""
    a1 = server.create_inbox(label="one")["address"]
    out = server.list_inboxes()
    assert out["ok"] is True
    assert any(i["address"] == a1 for i in out["inboxes"]), "redaction must not empty the list"
    for inbox in out["inboxes"]:
        assert "api_key" not in inbox


def test_redaction_keeps_everything_the_agent_actually_needs(patched):
    """Secrets are gone, useful fields REMAIN — over-redaction would be a bug too."""
    res = server.create_inbox(label="keeps")
    assert res["address"].endswith(".mailflat.net")
    assert res["retention_hours"] == 2
    assert res["name"] == "keeps"

    addr = res["address"]
    patched.deliver(addr, sender="stripe", subject="Verify", body_text="code 424242", otp_code="424242")
    otp = server.wait_for_otp(addr, timeout=3)
    assert otp["otp_code"] == "424242"           # the very thing the agent came for
    assert otp["email"]["subject"] == "Verify"


def test_sdk_still_exposes_the_key_to_code(patched):
    """The split: the SDK (code surface) exposes the key, the tool (model surface) does not."""
    with server._client() as c:
        inbox = c.create(label="sdk-side")
    assert inbox.api_key and inbox.api_key.startswith(INBOX_KEY_PREFIX)
    assert inbox.raw["api_key"] == inbox.api_key
