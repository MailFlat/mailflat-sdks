"""MailFlat MCP server — a 12-tool set for GPT/Claude/Cursor/LangChain.

A thin MCP shell over the `mailflat` Python SDK; HTTP and behaviour live in the SDK.
Auth: `MAILFLAT_API_KEY` env var (the `mf_live_...` key from your dashboard).

Running it:
    uvx mailflat-mcp                 # from PyPI, in an isolated environment
    MAILFLAT_API_KEY=mf_live_... uvx mailflat-mcp

Connected to:
  - depends on: mailflat (SDK), mcp (FastMCP)
  - used by:    Claude Desktop / Cursor / any MCP client

⚠️ Failures RAISE (`ToolError`) rather than returning `{"error": ...}`: MCP marks a raised
tool call with `isError`, and a client that only sees a JSON body treats a returned error as a
successful call. The message carries the detail the model needs to recover.

⚠️ ONE documented exception to that rule: `wait_until_sent` RETURNS a dictionary when the
mail is still queued. A timeout there is not a fault — the queue is still retrying — and
raising would flag the call `isError`, which is exactly the signal that makes a model send
the mail a second time. Permanent failure still raises.

⚠️ Tool output goes through `redact_secrets()`: the per-inbox `api_key` in a backend
response must never reach the model's context (and from there prompt logs) — see B-055.

Key exports (MCP tools):
  - create_inbox(prefix?, label?, retention_hours?)
  - list_inboxes()
  - read_messages(address, direction="in")
  - wait_for_otp(address, timeout=30)
  - wait_for_message(address, timeout=30)
  - send_email(address, to, subject?, body?, html?, cc?, bcc?)
  - reply(address, message_id, body?, html?, cc?, bcc?)
  - wait_until_sent(address, message_id, timeout=60)
  - mark_read(address, message_id)
  - burn_inbox(address)
  - delete_inbox(address)
  - delete_message(address, message_id)
"""
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mailflat import (
    EncryptedInboxError,
    MailFlat,
    MailFlatError,
    OTPTimeoutError,
    SendFailedError,
    SendTimeoutError,
    failed_result,
    queued_result,
    redact_secrets,
    sent_result,
)

from ._version import __version__

mcp = FastMCP("mailflat")
# FastMCP has no `version=` parameter, so the underlying server reports the `mcp` LIBRARY
# version in `serverInfo` — a client asking which MailFlat server it is talking to was told
# "1.29.0". Set it on the low-level server the SDK builds for us. Guarded because this reaches
# past the documented surface: a future `mcp` release may move the attribute, and an MCP server
# that refuses to start is far worse than one reporting the wrong version.
try:  # pragma: no cover - depends on the installed mcp version
    mcp._mcp_server.version = __version__
except Exception:  # noqa: BLE001 - never let cosmetics break startup
    pass


def _client() -> MailFlat:
    """SDK client built from MAILFLAT_API_KEY. Monkeypatched in tests."""
    return MailFlat(
        api_key=os.environ.get("MAILFLAT_API_KEY") or "",
        base_url=os.environ.get("MAILFLAT_API_URL", "https://mailflat.net"),
    )



def _forbid_unknown_arguments() -> None:
    """Make every registered tool REFUSE an argument it does not declare.

    Why here and not in the signatures: the first attempt added `**unknown` to each tool
    and raised from the body. FastMCP builds each tool's JSON schema FROM THE SIGNATURE,
    and it does not understand a VAR_KEYWORD parameter — it turned `unknown` into an
    ordinary property with no default, i.e. a REQUIRED one. The result was a condition no
    caller could satisfy: omit it and the server says `Field required`, pass it and the
    body says `does not accept: unknown`. Ten of twelve tools became uncallable, and the
    package shipped that way.

    It shipped because the test called the functions DIRECTLY in Python, never through the
    MCP call path — the same "inspected, not called" mistake that let the original bug
    (unknown fields silently dropped) survive a whole round. A single real `call_tool`
    would have caught it. `tests/test_mcp.py` now goes through that path.

    The correct seam is the model FastMCP already validates against: flipping it to
    `extra="forbid"` makes the refusal happen where arguments arrive, names the offending
    field, and leaves the published schema honest (`additionalProperties: false`) so a
    client can see the rule without calling.
    """
    for tool in mcp._tool_manager._tools.values():
        arg_model = tool.fn_metadata.arg_model
        arg_model.model_config["extra"] = "forbid"
        arg_model.model_rebuild(force=True)
        schema = tool.parameters
        if isinstance(schema, dict):
            schema["additionalProperties"] = False


@mcp.tool()
def create_inbox(prefix: str = "", label: str = "", retention_hours: int = 0) -> dict:
    """Create a real disposable email inbox. Returns the inbox address.
    `label` needs a paid plan; on free it is reported back in `ignored_fields`.
    `retention_hours` is optional (0 = your plan's max); requests above your plan are capped."""
    try:
        with _client() as c:
            inbox = c.create(
                prefix=prefix or None,
                label=label or None,
                retention_hours=retention_hours if retention_hours and retention_hours > 0 else None,
            )
            return redact_secrets(inbox.raw)
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def list_inboxes() -> dict:
    """List all inboxes available to this API key."""
    try:
        with _client() as c:
            return redact_secrets({"ok": True, "inboxes": [i.raw for i in c.list()]})
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def read_messages(address: str, direction: str = "in") -> dict:
    """Read messages in the given inbox address (newest first).

    `direction` is "in" for received mail (default), "out" for mail sent from this
    address, or "all" for both. Received mail is the default so that a reply you are
    waiting for is not confused with a message you just sent."""
    try:
        with _client() as c:
            msgs = c.inbox(address).messages(direction=direction)
            return redact_secrets({"ok": True, "emails": [m.raw for m in msgs]})
    except ValueError as e:      # invalid direction — caught before any network call
        raise ToolError(str(e)) from e
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def wait_for_otp(address: str, timeout: int = 30) -> dict:
    """Poll the inbox until an OTP code arrives (or timeout). Returns {otp_code, email}."""
    try:
        with _client() as c:
            inbox = c.inbox(address)
            otp = inbox.wait_for_otp(timeout=timeout)
            latest = inbox.latest()
            return redact_secrets({"otp_code": otp, "email": latest.raw if latest else None})
    except OTPTimeoutError as e:
        # The SDK's timeout text distinguishes "nothing arrived" from "mail arrived but no code
        # could be extracted" and quotes the message — that sentence is what lets the model
        # recover, so it must reach it as the error itself.
        raise ToolError(str(e)) from e
    except EncryptedInboxError as e:
        raise ToolError(str(e)) from e
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def wait_for_message(address: str, timeout: int = 30) -> dict:
    """Poll the inbox until a new message ARRIVES (or timeout). Returns {email}.

    Only received mail counts, so you can send to a peer and then wait for their reply
    without matching your own outgoing message."""
    try:
        with _client() as c:
            msg = c.inbox(address).wait_for_message(timeout=timeout)
            return redact_secrets({"ok": True, "email": msg.raw})
    except OTPTimeoutError as e:
        raise ToolError(str(e)) from e
    except EncryptedInboxError as e:
        raise ToolError(str(e)) from e
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def send_email(address: str, to: str, subject: str = "", body: str = "", html: str = "",
               cc: list[str] | None = None, bcc: list[str] | None = None) -> dict:
    """Send an email FROM the given inbox address (DKIM-signed via MailFlat's MTA).
    Use for replies or outbound automation. `html` is optional.

    Returns once the mail is ACCEPTED for delivery, not once it is delivered: delivery
    runs on a queue and the result arrives via webhook or by reading the message back.

    `cc` addresses appear in the mail's headers; `bcc` addresses never do — not even in
    their own copy. Attachments are not available here; send those from the SDK."""
    try:
        with _client() as c:
            return redact_secrets(c.inbox(address).send(
                to, subject=subject, body=body, html=html or None,
                cc=cc or None, bcc=bcc or None))
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def reply(address: str, message_id: int, body: str = "", html: str = "",
          cc: list[str] | None = None, bcc: list[str] | None = None) -> dict:
    """Reply to a message so it stays in the SAME conversation.

    Prefer this over send_email when answering: it fills in the recipient, an `Re:` subject
    and the threading headers. A plain send_email starts a new conversation in the
    recipient's client, which does not look like a reply."""
    try:
        with _client() as c:
            inbox = c.inbox(address)
            target = next((m for m in inbox.messages(direction="all") if m.id == message_id), None)
            if target is None:
                raise ToolError(f"Message {message_id} not found in {address}")
            return redact_secrets(target.reply(body, html=html or None,
                                               cc=cc or None, bcc=bcc or None))
    except MailFlatError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def wait_until_sent(address: str, message_id: int, timeout: int = 60) -> dict:
    """Find out whether a mail you sent was actually delivered.

    send_email only means "accepted for delivery"; the mail goes out later on a queue. Call
    this with the message_id send_email returned to learn the outcome.

    Returns `delivered: true` once it went out. If it is still queued when the timeout
    elapses you get `timed_out: true` with `delivered: false` — that is NOT a failure and
    you must NOT send the mail again; the queue is still retrying. `status` tells you
    whether it has been attempted yet (`queued`) or attempted and rescheduled (`retrying`),
    and `error` carries what the last attempt reported. Permanent failure comes back the
    same way, as `status: failed` with the reason in `error` — read `note` before you
    resend anything."""
    try:
        with _client() as c:
            return sent_result(c.inbox(address).wait_until_sent(message_id, timeout=timeout))
    except SendTimeoutError as e:
        # Deliberately NOT raised. Every other tool here signals trouble with ToolError so
        # the client marks the call `isError`, but "still queued" is a legitimate answer to
        # "did it go?" — the answer being "not yet". Raising would tell the model the send
        # went wrong, and the model's reasonable next move would be to send it again.
        return queued_result(e)
    except SendFailedError as e:
        # Also NOT raised — and this branch used to be the exception (B-097). The reasoning
        # for raising was "the reason travels in the message so the model can decide whether
        # a corrected resend makes sense", which is exactly what `failed_result` does
        # better: it carries the reason AND the instruction (`SEND_FAILED_NOTE`: fix the
        # cause before sending again), redacted, under the same keys every other model
        # surface uses. `ToolError(str(e))` threw the instruction away, put the remote MTA's
        # unredacted sentence into model context, and made this the one surface where the
        # cross-surface payload did not exist at all.
        return failed_result(e)
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def mark_read(address: str, message_id: int) -> dict:
    """Mark one message as read so later polls can skip it."""
    try:
        with _client() as c:
            return redact_secrets(c.inbox(address).mark_read(message_id))
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def burn_inbox(address: str) -> dict:
    """Delete every message in an inbox but KEEP the address.

    Use between scenarios: the address stays registered wherever you already used it."""
    try:
        with _client() as c:
            return redact_secrets(c.inbox(address).burn())
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def delete_inbox(address: str) -> dict:
    """Delete an inbox and all its messages by address. Irreversible."""
    try:
        with _client() as c:
            return redact_secrets(c.inbox(address).delete())
    except MailFlatError as e:
        raise ToolError(str(e)) from e

@mcp.tool()
def delete_message(address: str, message_id: int) -> dict:
    """Delete a single message in an inbox by its id (the inbox itself stays)."""
    try:
        with _client() as c:
            return redact_secrets(c.inbox(address).delete_message(message_id))
    except MailFlatError as e:
        raise ToolError(str(e)) from e

def _sentence_for(tool_name: str, exc: Exception) -> str:
    """Turn a pydantic argument error into the sentence style the REST API already uses.

    The REST layer has translated validation errors into sentences since B-069, but the
    TOOL CALL boundary never went through that translation: FastMCP renders pydantic's own
    output and it lands verbatim in model context —
    `1 validation error for read_messagesArguments … input_value={'address': '…'},
    input_type=dict … https://errors.pydantic.dev/2.13/v/missing`.

    That is four leaks in one string: a dict repr, a Python type name, an internal class
    name, and a third-party URL. Same class we closed on HTTP, a different boundary. A
    model reading this learns nothing it can act on, and the values it echoes back are the
    caller's own arguments.
    """
    errors = getattr(exc, "errors", None)
    details: list[str] = []
    if callable(errors):
        for err in errors()[:3]:
            field = ".".join(str(p) for p in err.get("loc", ())) or "input"
            kind = err.get("type", "")
            if kind == "extra_forbidden":
                details.append(f"'{field}' is not an argument {tool_name} accepts")
            elif kind == "missing":
                details.append(f"'{field}' is required")
            else:
                details.append(f"'{field}': {err.get('msg', 'invalid value')}")
    if not details:
        return f"{tool_name}: the arguments are not valid."
    return f"{tool_name}: " + "; ".join(details) + "."


def _clean_tool_errors() -> None:
    """Wrap argument validation so pydantic's raw output never reaches the model."""
    manager = mcp._tool_manager
    original = manager.call_tool

    async def call_tool(name, arguments, *args, **kwargs):
        try:
            return await original(name, arguments, *args, **kwargs)
        except ToolError as exc:
            cause = exc.__cause__ or exc.__context__
            if cause is not None and callable(getattr(cause, "errors", None)):
                raise ToolError(_sentence_for(name, cause)) from None
            raise

    manager.call_tool = call_tool


# The tools are registered above; the locks must be applied AFTER that.
_forbid_unknown_arguments()
_clean_tool_errors()


def main():
    mcp.run()  # stdio transport (MCP clients connect to this)


if __name__ == "__main__":
    main()
