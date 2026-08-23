"""MailFlat LangChain integration — `MailFlatToolkit` plus a 12-tool LangChain tool set.

A thin LangChain shell over the `mailflat` SDK; all HTTP and behaviour live in the client.
`langchain-core` is an optional dependency → `pip install mailflat[langchain]`.

Usage:
    from mailflat.langchain import MailFlatToolkit
    toolkit = MailFlatToolkit(api_key=env("MAILFLAT_KEY"))
    tools = toolkit.get_tools()   # create_inbox, list_inboxes, read_messages, wait_for_otp,
                                  # wait_for_message, send_email, reply, wait_until_sent,
                                  # mark_read, burn_inbox, delete_inbox, delete_message

⚠️ Tool output goes through `redact_secrets()`: a per-inbox `api_key` must never reach the
model's context (and from there LangSmith traces) — see B-055.

⚠️ `send_email` / `reply` take `cc` and `bcc` but NOT attachments. An address is a short
string; file bytes would have to travel through the model's context, which costs tokens
and is simply impossible for a multi-megabyte file. Attach files from the SDK
(`inbox.send(..., attachments=[...])`) — the code surface, not the model surface.

⚠️ `send_email` returns "accepted", not "delivered" — delivery happens on a queue. That is
what `wait_until_sent` is for. Without it a model that gets no answer does the reasonable
thing and sends again, so the missing tool produced DUPLICATE mail, not silent loss.

Connected to:
  - imports from: mailflat.client (MailFlat), mailflat.redact, mailflat.agent_results,
    langchain_core.tools
  - imported by:  user code (LangChain agents)

Key exports:
  - `MailFlatToolkit(api_key=..., base_url=...)` — `.get_tools()` → list[BaseTool]
  - `AsyncMailFlatToolkit(...)` — the same tools with `coroutine=` bound (real async I/O)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from langchain_core.tools import BaseTool, StructuredTool
except ImportError as exc:  # langchain-core is not installed → explain how to get it
    raise ImportError(
        "MailFlat's LangChain integration requires 'langchain-core'. "
        "Install it with:  pip install mailflat[langchain]"
    ) from exc

from .agent_results import failed_result, queued_result, sent_result
from .client import DEFAULT_BASE_URL, MailFlat
from .errors import (
    EncryptedInboxError,
    MailFlatError,
    OTPTimeoutError,
    SendFailedError,
    SendTimeoutError,
)
from .redact import redact_secrets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .aio import AsyncMailFlat


class MailFlatToolkit:
    """Toolkit that plugs MailFlat's tools into a LangChain agent.

    >>> from mailflat.langchain import MailFlatToolkit
    >>> toolkit = MailFlatToolkit(api_key="mf_live_...")
    >>> tools = toolkit.get_tools()
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: MailFlat | None = None,
    ) -> None:
        # Injecting a client keeps tests simple; otherwise one is built from api_key.
        self._client = client or MailFlat(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------ tools
    def get_tools(self) -> "Sequence[BaseTool]":
        """Return every MailFlat tool as a list of LangChain `BaseTool`s."""
        client = self._client

        def create_inbox(prefix: str = "", label: str = "", retention_hours: int = 0) -> dict:
            """Open an email inbox and return its address. The address is permanent and
            stays until you delete it; only the messages inside expire, on the retention
            window you choose. Use it to sign up for a service or receive a one-time code.

            Args:
                prefix: Optional left-hand prefix (e.g. 'signup'); random if empty.
                label: Optional human label to remember what this inbox is for.
                retention_hours: Optional retention in hours; 0 = your plan's max,
                    values above your plan are capped.
            """
            try:
                inbox = client.create(
                    prefix=prefix or None,
                    label=label or None,
                    retention_hours=retention_hours if retention_hours and retention_hours > 0 else None,
                )
                return redact_secrets(inbox.raw)
            except MailFlatError as e:
                return {"error": str(e)}

        def list_inboxes() -> dict:
            """List all inboxes available to this API key."""
            try:
                return redact_secrets({"ok": True, "inboxes": [i.raw for i in client.list()]})
            except MailFlatError as e:
                return {"error": str(e)}

        def read_messages(address: str, direction: str = "in") -> dict:
            """Read messages in the given inbox address (newest first).

            Args:
                address: The full inbox address, e.g. signup-8f3@mailflat.net.
                direction: 'in' for received mail (default), 'out' for mail you sent from
                    this address, 'all' for both.
            """
            try:
                msgs = client.inbox(address).messages(direction=direction)
                return redact_secrets({"ok": True, "emails": [m.raw for m in msgs]})
            except ValueError as e:      # invalid direction — caught before any network call
                return {"error": str(e)}
            except MailFlatError as e:
                return {"error": str(e)}

        def wait_for_otp(address: str, timeout: int = 30) -> dict:
            """Poll the inbox until a one-time verification code (OTP) arrives.

            Args:
                address: The inbox address to poll.
                timeout: Maximum seconds to wait before giving up (default 30).
            """
            try:
                inbox = client.inbox(address)
                otp = inbox.wait_for_otp(timeout=timeout)
                latest = inbox.latest()
                return redact_secrets({"otp_code": otp, "email": latest.raw if latest else None})
            except OTPTimeoutError:
                return {"otp_code": None, "error": "timeout"}
            except EncryptedInboxError as e:
                return {"otp_code": None, "encrypted": True, "error": str(e)}
            except MailFlatError as e:
                return {"otp_code": None, "error": str(e)}

        def wait_for_message(address: str, timeout: int = 30) -> dict:
            """Poll the inbox until a NEW message arrives, then return it.

            Only received mail counts, so you can send to a peer and wait for their reply
            without matching your own outgoing message.

            Args:
                address: The inbox address to poll.
                timeout: Maximum seconds to wait before giving up (default 30).
            """
            try:
                msg = client.inbox(address).wait_for_message(timeout=timeout)
                return redact_secrets({"ok": True, "email": msg.raw})
            except OTPTimeoutError:
                return {"email": None, "error": "timeout"}
            except EncryptedInboxError as e:
                return {"email": None, "encrypted": True, "error": str(e)}
            except MailFlatError as e:
                return {"email": None, "error": str(e)}

        def send_email(
            address: str, to: str, subject: str = "", body: str = "", html: str = "",
            cc: list[str] | None = None, bcc: list[str] | None = None,
        ) -> dict:
            """Send an email FROM the given inbox address (DKIM-signed via MailFlat's MTA).

            Returns once the mail is ACCEPTED for delivery, not once it is delivered.

            Args:
                address: The inbox address to send from (must belong to this API key).
                to: Recipient email address.
                subject: Email subject line.
                body: Plain-text body.
                html: Optional HTML body.
                cc: Addresses to copy. They appear in the mail's headers.
                bcc: Addresses to copy privately. They never appear in the headers.
            """
            try:
                return redact_secrets(client.inbox(address).send(
                    to, subject=subject, body=body, html=html or None,
                    cc=cc or None, bcc=bcc or None))
            except MailFlatError as e:
                return {"error": str(e)}

        def reply(address: str, message_id: int, body: str = "", html: str = "",
                  cc: list[str] | None = None, bcc: list[str] | None = None) -> dict:
            """Reply to a message so it stays in the SAME conversation.

            Prefer this over send_email when answering: it fills in the recipient, an `Re:`
            subject and the threading headers. A plain send_email opens a new conversation in
            the recipient's client, which does not look like a reply.

            Args:
                address: The inbox address that holds the message.
                message_id: The id of the message to answer, as returned by read_messages.
                body: Plain-text reply body.
                html: Optional HTML body.
                cc: Addresses to copy. They appear in the mail's headers.
                bcc: Addresses to copy privately. They never appear in the headers.
            """
            try:
                inbox = client.inbox(address)
                target = next((m for m in inbox.messages(direction="all") if m.id == message_id), None)
                if target is None:
                    return {"error": f"Message {message_id} not found in {address}"}
                return redact_secrets(target.reply(body, html=html or None,
                                                   cc=cc or None, bcc=bcc or None))
            except (MailFlatError, ValueError) as e:
                return {"error": str(e)}

        def wait_until_sent(address: str, message_id: int, timeout: int = 60) -> dict:
            """Find out whether a mail you sent was actually delivered.

            send_email only means "accepted for delivery"; the mail is delivered later by a
            queue. Call this with the message_id send_email returned to learn the outcome.

            Returns `delivered: true` once it went out. If it is still queued when the
            timeout elapses you get `timed_out: true` and `delivered: false` — that is NOT a
            failure and you must NOT send the mail again; the queue is still retrying.

            Args:
                address: The inbox address the mail was sent from.
                message_id: The id send_email returned for the mail.
                timeout: Maximum seconds to wait for a final state (default 60).
            """
            try:
                return sent_result(
                    client.inbox(address).wait_until_sent(message_id, timeout=timeout))
            except SendTimeoutError as e:
                return queued_result(e)
            except SendFailedError as e:
                return failed_result(e)
            except MailFlatError as e:
                return {"error": str(e)}

        def mark_read(address: str, message_id: int) -> dict:
            """Mark one message as read so later polls can skip it.

            Args:
                address: The inbox address that holds the message.
                message_id: The message id, as returned by read_messages.
            """
            try:
                return redact_secrets(client.inbox(address).mark_read(message_id))
            except MailFlatError as e:
                return {"error": str(e)}

        def burn_inbox(address: str) -> dict:
            """Delete every message in an inbox but keep the address itself.

            Use this to start a clean scenario without losing the address you already
            registered somewhere.

            Args:
                address: The inbox address to empty.
            """
            try:
                return redact_secrets(client.inbox(address).burn())
            except MailFlatError as e:
                return {"error": str(e)}

        def delete_inbox(address: str) -> dict:
            """Delete an inbox and all its messages by address. Irreversible.

            Args:
                address: The inbox address to delete.
            """
            try:
                return redact_secrets(client.inbox(address).delete())
            except MailFlatError as e:
                return {"error": str(e)}

        def delete_message(address: str, message_id: int) -> dict:
            """Delete a single message in an inbox by its id (the inbox itself stays).

            Args:
                address: The inbox address that holds the message.
                message_id: The message id, as returned by read_messages.
            """
            try:
                return redact_secrets(client.inbox(address).delete_message(message_id))
            except MailFlatError as e:
                return {"error": str(e)}

        funcs: list[Any] = [
            create_inbox,
            list_inboxes,
            read_messages,
            wait_for_otp,
            wait_for_message,
            send_email,
            reply,
            wait_until_sent,
            mark_read,
            burn_inbox,
            delete_inbox,
            # delete_message used to be withheld here on the grounds that "a model which can
            # delete single messages can destroy evidence of what it did". That reasoning
            # does not survive contact with the list above it: delete_inbox is already
            # exposed and destroys EVERY message plus the address. Withholding the smaller
            # capability while granting the larger one protected nothing, and it left this
            # toolkit as the only model surface missing a tool that MCP and the Vercel AI
            # SDK have shipped since day 42.
            delete_message,
        ]
        # from_function derives args_schema from the type hints and docstring itself,
        # which side-steps the pydantic v1/v2 difference.
        return [StructuredTool.from_function(func=f) for f in funcs]


class AsyncMailFlatToolkit:
    """The same toolkit over real async I/O — binds `coroutine=` on every tool.

    >>> from mailflat.langchain import AsyncMailFlatToolkit
    >>> tools = AsyncMailFlatToolkit(api_key="mf_live_...").get_tools()
    >>> # agent.ainvoke(...) now awaits instead of parking a thread

    Why it matters: a tool with no `coroutine=` is run by LangChain in a thread pool. An
    agent awaiting an OTP for 30 seconds therefore holds a thread for 30 seconds, and a
    fleet of inboxes holds one each. With a real coroutine they share one event loop.

    ⚠️ The tool NAMES, descriptions and argument schemas must stay identical to
    `MailFlatToolkit` — the model must not see two different MailFlats. The docstrings are
    written out twice (they ARE the schema), so that equality is locked by
    `tests/test_langchain_async.py` rather than by remembering. The same discipline as the
    English-only rule for `packages/`: a test, not a habit.
    """

    def __init__(self, api_key: str | None = None, *, base_url: str = DEFAULT_BASE_URL,
                 client: "AsyncMailFlat | None" = None) -> None:
        from .aio import AsyncMailFlat

        self._client = client or AsyncMailFlat(api_key=api_key, base_url=base_url)

    def get_tools(self) -> "Sequence[BaseTool]":
        """Return every MailFlat tool with an async implementation bound."""
        client = self._client

        async def create_inbox(prefix: str = "", label: str = "",
                               retention_hours: int = 0) -> dict:
            """Open an email inbox and return its address. The address is permanent and
            stays until you delete it; only the messages inside expire, on the retention
            window you choose. Use it to sign up for a service or receive a one-time code.

            Args:
                prefix: Optional left-hand prefix (e.g. 'signup'); random if empty.
                label: Optional human label to remember what this inbox is for.
                retention_hours: Optional retention in hours; 0 = your plan's max,
                    values above your plan are capped.
            """
            try:
                inbox = await client.create(
                    prefix=prefix or None, label=label or None,
                    retention_hours=retention_hours if retention_hours and retention_hours > 0 else None,
                )
                return redact_secrets(inbox.raw)
            except MailFlatError as e:
                return {"error": str(e)}

        async def list_inboxes() -> dict:
            """List all inboxes available to this API key."""
            try:
                inboxes = await client.list()
                return redact_secrets({"ok": True, "inboxes": [i.raw for i in inboxes]})
            except MailFlatError as e:
                return {"error": str(e)}

        async def read_messages(address: str, direction: str = "in") -> dict:
            """Read messages in the given inbox address (newest first).

            Args:
                address: The full inbox address, e.g. signup-8f3@mailflat.net.
                direction: 'in' for received mail (default), 'out' for mail you sent from
                    this address, 'all' for both.
            """
            try:
                msgs = await client.inbox(address).messages(direction=direction)
                return redact_secrets({"ok": True, "emails": [m.raw for m in msgs]})
            except ValueError as e:      # invalid direction — caught before any network call
                return {"error": str(e)}
            except MailFlatError as e:
                return {"error": str(e)}

        async def wait_for_otp(address: str, timeout: int = 30) -> dict:
            """Poll the inbox until a one-time verification code (OTP) arrives.

            Args:
                address: The inbox address to poll.
                timeout: Maximum seconds to wait before giving up (default 30).
            """
            try:
                inbox = client.inbox(address)
                otp = await inbox.wait_for_otp(timeout=timeout)
                latest = await inbox.latest()
                return redact_secrets({"otp_code": otp, "email": latest.raw if latest else None})
            except OTPTimeoutError:
                return {"otp_code": None, "error": "timeout"}
            except EncryptedInboxError as e:
                return {"otp_code": None, "encrypted": True, "error": str(e)}
            except MailFlatError as e:
                return {"otp_code": None, "error": str(e)}

        async def wait_for_message(address: str, timeout: int = 30) -> dict:
            """Poll the inbox until a NEW message arrives, then return it.

            Only received mail counts, so you can send to a peer and wait for their reply
            without matching your own outgoing message.

            Args:
                address: The inbox address to poll.
                timeout: Maximum seconds to wait before giving up (default 30).
            """
            try:
                msg = await client.inbox(address).wait_for_message(timeout=timeout)
                return redact_secrets({"ok": True, "email": msg.raw})
            except OTPTimeoutError:
                # Same shape as the sync toolkit: `"timeout"`, not the exception text. The
                # first draft here returned str(e) and the two toolkits would have answered
                # differently for the same fault — locked by test_langchain_async.py.
                return {"email": None, "error": "timeout"}
            except EncryptedInboxError as e:
                return {"email": None, "encrypted": True, "error": str(e)}
            except MailFlatError as e:
                return {"email": None, "error": str(e)}

        async def send_email(address: str, to: str, subject: str = "", body: str = "",
                             html: str = "", cc: list[str] | None = None,
                             bcc: list[str] | None = None) -> dict:
            """Send an email FROM the given inbox address (DKIM-signed via MailFlat's MTA).

            Returns once the mail is ACCEPTED for delivery, not once it is delivered.

            Args:
                address: The inbox address to send from (must belong to this API key).
                to: Recipient email address.
                subject: Email subject line.
                body: Plain-text body.
                html: Optional HTML body.
                cc: Addresses to copy. They appear in the mail's headers.
                bcc: Addresses to copy privately. They never appear in the headers.
            """
            try:
                return redact_secrets(await client.inbox(address).send(
                    to, subject=subject, body=body, html=html or None,
                    cc=cc or None, bcc=bcc or None))
            except MailFlatError as e:
                return {"error": str(e)}

        async def reply(address: str, message_id: int, body: str = "", html: str = "",
                        cc: list[str] | None = None, bcc: list[str] | None = None) -> dict:
            """Reply to a message so it stays in the SAME conversation.

            Prefer this over send_email when answering: it fills in the recipient, an `Re:`
            subject and the threading headers. A plain send_email opens a new conversation in
            the recipient's client, which does not look like a reply.

            Args:
                address: The inbox address that holds the message.
                message_id: The id of the message to answer, as returned by read_messages.
                body: Plain-text reply body.
                html: Optional HTML body.
                cc: Addresses to copy. They appear in the mail's headers.
                bcc: Addresses to copy privately. They never appear in the headers.
            """
            try:
                inbox = client.inbox(address)
                messages = await inbox.messages(direction="all")
                target = next((m for m in messages if m.id == message_id), None)
                if target is None:
                    return {"error": f"Message {message_id} not found in {address}"}
                return redact_secrets(await target.reply(body, html=html or None,
                                                         cc=cc or None, bcc=bcc or None))
            except (MailFlatError, ValueError) as e:
                return {"error": str(e)}

        async def wait_until_sent(address: str, message_id: int, timeout: int = 60) -> dict:
            """Find out whether a mail you sent was actually delivered.

            send_email only means "accepted for delivery"; the mail is delivered later by a
            queue. Call this with the message_id send_email returned to learn the outcome.

            Returns `delivered: true` once it went out. If it is still queued when the
            timeout elapses you get `timed_out: true` and `delivered: false` — that is NOT a
            failure and you must NOT send the mail again; the queue is still retrying.

            Args:
                address: The inbox address the mail was sent from.
                message_id: The id send_email returned for the mail.
                timeout: Maximum seconds to wait for a final state (default 60).
            """
            try:
                return sent_result(
                    await client.inbox(address).wait_until_sent(message_id, timeout=timeout))
            except SendTimeoutError as e:
                return queued_result(e)
            except SendFailedError as e:
                return failed_result(e)
            except MailFlatError as e:
                return {"error": str(e)}

        async def mark_read(address: str, message_id: int) -> dict:
            """Mark one message as read so later polls can skip it.

            Args:
                address: The inbox address that holds the message.
                message_id: The message id, as returned by read_messages.
            """
            try:
                return redact_secrets(await client.inbox(address).mark_read(message_id))
            except MailFlatError as e:
                return {"error": str(e)}

        async def burn_inbox(address: str) -> dict:
            """Delete every message in an inbox but keep the address itself.

            Use this to start a clean scenario without losing the address you already
            registered somewhere.

            Args:
                address: The inbox address to empty.
            """
            try:
                return redact_secrets(await client.inbox(address).burn())
            except MailFlatError as e:
                return {"error": str(e)}

        async def delete_inbox(address: str) -> dict:
            """Delete an inbox and all its messages by address. Irreversible.

            Args:
                address: The inbox address to delete.
            """
            try:
                return redact_secrets(await client.inbox(address).delete())
            except MailFlatError as e:
                return {"error": str(e)}

        async def delete_message(address: str, message_id: int) -> dict:
            """Delete a single message in an inbox by its id (the inbox itself stays).

            Args:
                address: The inbox address that holds the message.
                message_id: The message id, as returned by read_messages.
            """
            try:
                return redact_secrets(await client.inbox(address).delete_message(message_id))
            except MailFlatError as e:
                return {"error": str(e)}

        coroutines: list[Any] = [
            create_inbox, list_inboxes, read_messages, wait_for_otp, wait_for_message,
            send_email, reply, wait_until_sent, mark_read, burn_inbox, delete_inbox,
            delete_message,
        ]
        # `func=` is what StructuredTool derives the ARGUMENT SCHEMA from, even when the
        # async path is the real implementation. The stub therefore has to carry the
        # coroutine's signature and docstring — the first version took (*args, **kwargs)
        # and the schema came out EMPTY, so the model's arguments were silently dropped.
        return [
            StructuredTool.from_function(coroutine=f, func=_sync_unavailable(f))
            for f in coroutines
        ]


def _sync_unavailable(coro: Any):
    """A sync stub that explains itself instead of failing obscurely.

    An async toolkit invoked synchronously is a real mistake (an agent wired to `.invoke`
    instead of `.ainvoke`). LangChain's own failure is a bare NotImplementedError that says
    nothing about which toolkit to use.

    `functools.wraps` is load-bearing, not cosmetic: it carries the coroutine's signature,
    annotations and docstring onto the stub, and those are exactly what the tool schema is
    built from.
    """
    import functools

    @functools.wraps(coro)
    def _stub(*args: Any, **kwargs: Any) -> dict:
        return {"error": f"{coro.__name__} is async-only in AsyncMailFlatToolkit. "
                         "Use ainvoke(), or MailFlatToolkit for synchronous agents."}

    return _stub
