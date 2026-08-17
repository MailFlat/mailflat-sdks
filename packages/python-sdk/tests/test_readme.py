"""🔒 Runs the README's own code — the examples a new user copies first.

Why this exists: four rounds of external review found the same class of bug over and over —
things that were written but never executed. The most expensive instance was line 3 of the
quickstart: it used `label=` while the printed address comment showed the value of `prefix=`.
It had already shaped at least one reader's code before anyone noticed.

Three layers, each proving something the others cannot:
  1. Every keyword argument in a README example really exists in the SDK signature.
  2. Every method called on `MailFlat`/`Inbox`/`Message` really exists.
  3. Any example that prints an address actually gets executed against a fake transport, and
     the printed value is compared to the `# →` comment beside it.

Layer 3 is the one that would have caught the original bug, and it only works because the fake
honours the real server rule: the address is shaped by `prefix`, never by `label`. That rule is
locked separately on the server side (backend test_agent.py::test_prefix_shapes_address_label_does_not),
so this fake cannot drift into agreeing with a wrong README.

Connected to:
  - exercises: ../README.md against mailflat (SDK)

Running it: cd packages/python-sdk && python -m pytest tests/test_readme.py
"""
from __future__ import annotations

import inspect
import io
import re
from contextlib import redirect_stdout
from pathlib import Path

import httpx
import pytest

from mailflat import Inbox, MailFlat, Message

README = Path(__file__).resolve().parent.parent / "README.md"
CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def blocks() -> list[str]:
    return CODE_BLOCK.findall(README.read_text())


def test_readme_has_python_examples():
    """Guard the guard: an empty match list would make every test below pass for free."""
    assert len(blocks()) >= 2, "no ```python blocks found — has the README format changed?"


# --------------------------------------------------------------- 1) keyword arguments exist
def test_every_create_kwarg_exists_in_the_sdk():
    """🔒 An invented kwarg is silently ignored by the server, so the docs must not invent any."""
    allowed = set(inspect.signature(MailFlat.create).parameters)
    for block in blocks():
        for call in re.findall(r"\.create(?:_inbox)?\((.*?)\)", block, re.DOTALL):
            for kwarg in re.findall(r"(\w+)\s*=", call):
                assert kwarg in allowed, f"README calls create({kwarg}=…) but the SDK has no such argument"


def test_every_send_kwarg_exists_in_the_sdk():
    allowed = set(inspect.signature(Inbox.send).parameters)
    for block in blocks():
        for call in re.findall(r"\.send\((.*?)\)", block, re.DOTALL):
            for kwarg in re.findall(r"(\w+)\s*=", call):
                assert kwarg in allowed, f"README calls send({kwarg}=…) but the SDK has no such argument"


# --------------------------------------------------------------- 2) methods exist
def test_every_documented_method_exists():
    """Catches a method that was renamed in code but left behind in the README."""
    known = {n for n in dir(Inbox) + dir(MailFlat) + dir(Message) if not n.startswith("_")}
    # Only check calls on the objects the quickstart names, so unrelated stdlib calls are ignored.
    for block in blocks():
        for obj, method in re.findall(r"\b(mf|inbox|msg|message)\.(\w+)\s*\(", block):
            assert method in known, f"README calls {obj}.{method}() but no such method exists"


# --------------------------------------------------------------- 3) run it, compare the output
class FakeBackend:
    """A minimal /api/v1 that honours the REAL address rule: prefix shapes it, label does not.

    That rule is not invented here — it is locked server-side. If the server ever changes it,
    the backend test fails first and this fake is updated deliberately rather than silently.
    """

    DOMAIN = "x7k2m.mailflat.net"

    def handler(self, req: httpx.Request) -> httpx.Response:
        import json

        path = req.url.path
        if req.method == "POST" and path == "/api/v1/inboxes":
            body = json.loads(req.content or b"{}")
            prefix = body.get("prefix") or "agent-d220c0"        # random when omitted
            return httpx.Response(200, json={
                "ok": True, "address": f"{prefix}@{self.DOMAIN}",
                "name": body.get("label"), "retention_hours": 2,
            })
        if path.endswith("/latest"):
            # A real message always has a sender and (on a plain inbox) headers — an earlier
            # version of this fake omitted both, and the README's reply() example failed here
            # for a reason that could never happen in production. A fake that is thinner than
            # reality fails honest examples and passes broken ones.
            return httpx.Response(200, json={"ok": True, "email": {
                "id": 1, "sender": "human@gmail.com", "subject": "Verify",
                "body_text": "Your code is 405992", "otp_code": "405992", "direction": "in",
                "links": [], "attachments": [],
                "headers": {"Message-ID": "<abc@mail.gmail.com>"},
            }})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"ok": True, "emails": []})
        return httpx.Response(200, json={"ok": True})


def run_block(code: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Execute a README block against the fake backend and capture stdout.

    The network is made UNREACHABLE first. An early version of this test stripped the README's
    own `MailFlat(api_key="mf_live_...")` line with a regex that missed a trailing comment, so
    the example ran for real and hit production (it came back 401). A docs test that can reach
    the internet is a docs test that will, one day, do something to it.
    """
    backend = FakeBackend()
    transport = httpx.MockTransport(backend.handler)

    class OfflineClient(httpx.Client):
        def __init__(self, *a, **kw):
            kw["transport"] = transport          # any client built anywhere is offline
            super().__init__(*a, **kw)

    monkeypatch.setattr("mailflat.client.httpx.Client", OfflineClient)

    http = OfflineClient(base_url="https://mailflat.net", headers={"X-API-Key": "mf_test_x"})
    scope = {"MailFlat": MailFlat, "mf": MailFlat(api_key="mf_test_x", http_client=http)}
    # Drop the README's own constructor and imports; ours carry the offline transport.
    # `[^\n]*` (not `.*?$`) so a trailing comment on the same line cannot break the match.
    code = re.sub(r"^\s*(mf|mailflat)\s*=\s*MailFlat\([^\n]*$", "", code, flags=re.M)
    code = re.sub(r"^\s*from mailflat import[^\n]*$", "", code, flags=re.M)
    buf = io.StringIO()
    with redirect_stdout(buf):
        exec(compile(code, "<readme>", "exec"), scope)   # noqa: S102 - the README is our own file
    return buf.getvalue()


def address_blocks() -> list[tuple[str, str]]:
    """README blocks that print an address next to a `# →` expectation."""
    out = []
    for block in blocks():
        for line in block.splitlines():
            m = re.search(r"print\(\s*inbox\.address\s*\).*#\s*→\s*(\S+)", line)
            if m:
                out.append((block, m.group(1)))
    return out


def test_readme_actually_prints_the_address_it_claims(monkeypatch):
    """🔒 THE test the four review rounds asked for: run the example, compare the output.

    The original bug was invisible to every static check: the call was valid, the method
    existed, only the printed value was wrong. Nothing but executing it could catch that.
    """
    pairs = address_blocks()
    assert pairs, "no `print(inbox.address)  # → …` example found — did the quickstart change?"
    for block, expected in pairs:
        printed = run_block(block, monkeypatch).strip().splitlines()[0]
        local, _, domain = expected.partition("@")
        assert printed.startswith(local + "@"), (
            f"README promises {expected!r} but the example prints {printed!r} — "
            "the local part comes from `prefix`, not `label`"
        )


def test_quickstart_that_shows_a_named_address_passes_prefix():
    """A named address in the docs must come from `prefix`; `label` alone yields `agent-…`."""
    for block, expected in address_blocks():
        if expected.startswith("agent-"):
            continue
        create_calls = re.findall(r"\.create(?:_inbox)?\((.*?)\)", block, re.DOTALL)
        assert create_calls, "an address example with no create() call"
        assert any("prefix=" in call for call in create_calls), (
            f"the example promises {expected!r} but never passes prefix= — label does not shape the address"
        )
