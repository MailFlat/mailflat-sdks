"""AsyncMailFlatToolkit — real `coroutine=` binding, and parity with the sync toolkit.

The model must not see two different MailFlats. Tool names, descriptions and argument
schemas are the model-facing contract, so they are compared field by field here rather
than kept in sync by hand — the docstrings are written out twice because they ARE the
schema, and this file is what makes that safe.

Parity is checked on RESULTS too, not only on shape. The first draft of the async
`wait_for_message` returned the exception text on timeout while the sync one returned
`"timeout"`; a shape-only comparison would have passed it.

Connected to:
  - exercises: mailflat.langchain (MailFlatToolkit, AsyncMailFlatToolkit), mailflat.aio
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mailflat import MailFlat
from mailflat.aio import AsyncMailFlat
from mailflat.langchain import AsyncMailFlatToolkit, MailFlatToolkit

ADDR = "agent-7f3@x7k2m.mailflat.net"


def sync_tools(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://mailflat.net")
    return {t.name: t for t in MailFlatToolkit(client=MailFlat(api_key="mf_test_x",
                                                               http_client=http)).get_tools()}


def async_tools(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://mailflat.net")
    return {t.name: t for t in AsyncMailFlatToolkit(
        client=AsyncMailFlat(api_key="mf_test_x", http_client=http)).get_tools()}


EMPTY = lambda req: httpx.Response(200, json={})  # noqa: E731


# ------------------------------------------------------------------- binding

def test_every_tool_binds_a_coroutine():
    """🔒 The whole point: without `coroutine=`, LangChain parks a THREAD per tool call.

    An agent waiting 30s for an OTP holds that thread for 30s, and a fleet of inboxes holds
    one each.
    """
    for name, tool in async_tools(EMPTY).items():
        assert tool.coroutine is not None, f"{name} has no coroutine bound"


def test_async_tool_actually_awaits_the_api():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"ok": True, "emails": []})

    tool = async_tools(handler)["read_messages"]
    out = asyncio.run(tool.ainvoke({"address": ADDR}))
    assert out["ok"] is True
    assert calls[0].endswith("direction=in")     # the SDK's default survives the tool layer


def test_sync_invoke_on_the_async_toolkit_explains_itself():
    """Calling `.invoke()` on the async toolkit is a real mistake people make.

    LangChain's own failure is a bare NotImplementedError that says nothing about which
    toolkit to use, so the stub answers in words instead.
    """
    out = async_tools(EMPTY)["list_inboxes"].invoke({})
    assert "async-only" in out["error"] and "MailFlatToolkit" in out["error"]


# -------------------------------------------------------------------- parity

def test_tool_names_match_exactly():
    assert set(sync_tools(EMPTY)) == set(async_tools(EMPTY))


def test_descriptions_and_schemas_match_exactly():
    """🔒 The model-facing contract is identical on both toolkits.

    Written twice in the source (a docstring is the schema), so drift is caught here
    instead of by a user noticing the model behaves differently on the async path.
    """
    sync, aio = sync_tools(EMPTY), async_tools(EMPTY)
    for name in sorted(sync):
        assert sync[name].description.strip() == aio[name].description.strip(), \
            f"{name}: description differs between the sync and async toolkits"
        assert (json.dumps(sync[name].args_schema.model_json_schema()["properties"], sort_keys=True)
                == json.dumps(aio[name].args_schema.model_json_schema()["properties"], sort_keys=True)), \
            f"{name}: argument schema differs between the sync and async toolkits"


def _status(status, error=None):
    """A backend whose single message reports the given send_status."""
    return lambda req: httpx.Response(200, json={"ok": True, "email": {
        "id": 5, "direction": "out", "send_status": status, "send_error": error,
        "subject": "hi", "is_encrypted": False}})


@pytest.mark.parametrize("scenario", [
    # (handler, tool name, arguments) — the same fault must produce the same answer.
    (lambda req: httpx.Response(200, json={"email": None}), "wait_for_message",
     {"address": ADDR, "timeout": 0}),
    (lambda req: httpx.Response(200, json={"email": None}), "wait_for_otp",
     {"address": ADDR, "timeout": 0}),
    (lambda req: httpx.Response(403, json={"detail": "Inbox not found for this key"}),
     "list_inboxes", {}),
    (lambda req: httpx.Response(200, json={"encrypted": True, "note": "E2E inbox"}),
     "wait_for_otp", {"address": ADDR, "timeout": 0}),
    # All three outcomes of the delivery question. The timeout one matters most: it is the
    # branch a model acts on, and two surfaces wording it differently is two products.
    (_status("sent"), "wait_until_sent", {"address": ADDR, "message_id": 5, "timeout": 5}),
    (_status("failed", "550 unknown mailbox"), "wait_until_sent",
     {"address": ADDR, "message_id": 5, "timeout": 5}),
    (_status("queued"), "wait_until_sent", {"address": ADDR, "message_id": 5, "timeout": 0}),
])
def test_same_fault_gives_the_same_answer(scenario):
    """🔒 Behaviour parity, not just shape.

    This is the one that caught a real drift while writing the async toolkit: its
    `wait_for_message` returned the exception text on timeout where the sync one returned
    `"timeout"`. The name/description comparison above passed it happily.
    """
    handler, name, args = scenario
    got_sync = sync_tools(handler)[name].invoke(args)
    got_async = asyncio.run(async_tools(handler)[name].ainvoke(args))
    assert got_sync == got_async, f"{name}: sync and async answered differently"


def test_async_wait_until_sent_timeout_does_not_read_as_failure():
    """🔒 The sync toolkit has this lock; without it here, only one surface is protected.

    Parity above proves the two answers are EQUAL — equal and both wrong is still wrong, so
    the content is asserted on the async surface in its own right.
    """
    out = asyncio.run(async_tools(_status("queued"))["wait_until_sent"].ainvoke(
        {"address": ADDR, "message_id": 5, "timeout": 0}))
    assert out["timed_out"] is True
    assert out["delivered"] is False
    assert "fail" not in json.dumps(out).lower()
    assert "do not send it again" in out["note"].lower()


def test_secrets_are_redacted_on_the_async_path_too():
    """B-055: a per-inbox api_key must never reach the model's context — on either path."""
    handler = lambda req: httpx.Response(200, json={  # noqa: E731
        "ok": True, "address": ADDR, "api_key": "mf_sk_secret"})
    out = asyncio.run(async_tools(handler)["create_inbox"].ainvoke({"label": "x"}))
    assert "mf_sk_secret" not in json.dumps(out)
