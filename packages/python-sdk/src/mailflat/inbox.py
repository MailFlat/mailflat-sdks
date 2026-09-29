"""Inbox + Message — high-level operations on one MailFlat inbox and its messages.

`Inbox` is returned by the `MailFlat` client and carries the read, wait, send and delete
methods. `Message` is the typed form of an /api/v1 email payload.

Connected to:
  - imports from: mailflat.errors
  - imported by:  mailflat.client, mailflat.__init__

Key exports:
  - `Inbox` — `.address`, `.messages()`, `.latest()`, `.wait_for_message()`,
    `.wait_for_otp()`, `.send()`, `.wait_until_sent()`, `.mark_read()`, `.burn()`,
    `.download_attachment()`
  - `Message` — `.otp`, `.subject`, `.text`, `.html`, `.links`, `.attachments`, `.spam`, ...
  - `Attachment` — `.filename`, `.size_bytes`, `.download()`
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from email.utils import parseaddr

from ._http import send_timeout_message
from .errors import (EncryptedInboxError, MailFlatError, OTPTimeoutError, SendFailedError,
                     SendTimeoutError)

if TYPE_CHECKING:  # avoid a circular import (type hint only)
    from .client import MailFlat

# Direction filters (server contract). "in" is the default: for an agent, "did a message
# arrive?" cannot be answered by mail the agent itself sent.
DIRECTIONS = ("in", "out", "all")


#: The server's `subject` column limit. A reply built from a long inbound subject must fit.
MAX_SUBJECT_CHARS = 200


def _reply_subject(subject: str | None) -> str:
    """`Re:` prefix without doubling it up.

    Mail clients also use the subject when threading, so "Re: Re: Re: x" and a bare subject
    both risk breaking the conversation apart.

    A reply subject the SDK BUILDS ITSELF must fit the server's limit. A stored message can
    legitimately have a 198-character subject — inbound mail is not bound by our API limit —
    and `"Re: " + subject` then exceeds 200, so `reply()` refused to answer a message the
    service itself had accepted. The agent had no way out: the helper neither trimmed nor
    explained. Truncating a value WE derived is different from truncating the caller's
    input; the caller's own `subject=` is still rejected when too long, because they can fix it.

    The same reasoning covers CONTROL CHARACTERS, and it took a second external round to
    notice the rule had only been applied along the length axis. A stranger can send a
    subject whose RFC 2047 encoded-word decodes to a real CRLF; the server stored it, this
    helper prefixed `Re: ` and handed it back, and the server's own header guard then
    rejected it — so that message could never be answered again, by anyone, permanently.
    The test is the same one: can the caller fix the value? They did not write it, so no.
    """
    text = _one_line(subject or "").strip()
    if not text:
        return "Re:"
    reply = text if text.lower().startswith("re:") else f"Re: {text}"
    return reply if len(reply) <= MAX_SUBJECT_CHARS else reply[:MAX_SUBJECT_CHARS - 1] + "…"


#: Control characters that must not reach a mail header. Tab is absent on purpose: it is
#: horizontal space and does not split a header line. A RUN collapses to one space — CRLF is
#: two characters and "a\r\nb" should read "a b", not "a  b".
_CONTROL_RUN = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]+")


def _one_line(text: str) -> str:
    """Control characters out of a value that is about to become a mail header.

    A space, not deletion: `"a\\r\\nb"` becoming `"ab"` would silently join two words.
    """
    return _CONTROL_RUN.sub(" ", text)


def _encode_attachment(item: Any) -> dict[str, str]:
    """One attachment → the wire shape `{filename, content_type, content_b64}`.

    Accepts a filesystem path or a dict, because an agent should never have to base64 a
    file by hand — hiding that is most of what an SDK is for. A path is the common case
    (the file is on disk next to the agent) and the type is guessed from the extension so
    the recipient's client shows a PDF as a PDF rather than a nameless blob.

    The JS SDK deliberately does NOT accept paths: it can run in a browser, where there is
    no filesystem. Same feature, different runtimes — see `docs`.
    """
    if isinstance(item, (str, os.PathLike)):
        path = Path(item)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Could not read attachment {path}: {exc}") from exc
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {"filename": path.name, "content_type": ctype,
                "content_b64": base64.b64encode(data).decode("ascii")}

    if not isinstance(item, dict):
        raise TypeError(
            "Each attachment must be a file path or a dict with 'filename' and "
            f"'content' (bytes) or 'content_b64' (str), got {type(item).__name__}.")

    filename = str(item.get("filename") or "").strip()
    if not filename:
        raise ValueError("Attachment dicts need a 'filename'.")

    if "content_b64" in item:
        content_b64 = item["content_b64"]
        if not isinstance(content_b64, str):
            raise TypeError("'content_b64' must be a base64 string.")
    else:
        content = item.get("content")
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"Attachment '{filename}' needs 'content' as bytes (or 'content_b64' as "
                "a base64 string).")
        content_b64 = base64.b64encode(bytes(content)).decode("ascii")

    ctype = item.get("content_type") or mimetypes.guess_type(filename)[0] \
        or "application/octet-stream"
    return {"filename": filename, "content_type": ctype, "content_b64": content_b64}


def _send_payload(to: str, subject: str, body: str, html: str | None,
                  in_reply_to: str | None, cc: list[str] | None, bcc: list[str] | None,
                  attachments: list[Any] | None) -> dict[str, Any]:
    """Shared body builder for `send()` and `reply()`.

    One builder because `reply()` must accept exactly what `send()` accepts (K6): an agent
    answering a mail with an invoice attached is the normal case, not an edge case. Two
    builders would drift the first time a field is added to one of them.
    """
    payload: dict[str, Any] = {"to": to, "subject": subject, "body": body}
    if html is not None:
        payload["html"] = html
    if in_reply_to is not None:
        payload["in_reply_to"] = in_reply_to
    if cc:
        payload["cc"] = list(cc)
    if bcc:
        payload["bcc"] = list(bcc)
    if attachments:
        payload["attachments"] = [_encode_attachment(a) for a in attachments]
    return payload


def otp_timeout_message(address: str, timeout: float, seen: dict[str, Any] | None) -> str:
    """Timeout wording that distinguishes "nothing arrived" from "no code in it".

    These are completely different problems and used to produce the same sentence: the user
    went looking for a delivery failure while the message was sitting in the inbox with a
    code the parser did not recognise (B-062).

    Module level, not a method, because BOTH clients build this sentence. As a method the
    async client would have to borrow it unbound — or, far more likely, grow its own copy
    that slowly says something slightly different.
    """
    if not seen:
        return f"No message arrived for {address} within {timeout}s"
    subject = (seen.get("subject") or "(no subject)").strip()
    snippet = " ".join((seen.get("body_text") or "").split())[:120]
    return (
        f"Mail arrived for {address} but no OTP could be extracted from it "
        f"within {timeout}s. Newest message: {subject!r}"
        + (f" — {snippet!r}" if snippet else "")
        + ". Read inbox.latest().text and parse the code yourself, "
        "and please report the format so we can support it."
    )


# Fields `update_calendar_event` accepts; anything else is a typo and must not be dropped silently.
_EVENT_UPDATE_FIELDS = ("title", "start", "end", "duration_minutes", "timezone", "all_day",
                        "location", "description", "message")


def _event_body(**fields: Any) -> dict[str, Any]:
    """Request body for creating a calendar event. `None` means "not given" and is omitted."""
    body = {k: v for k, v in fields.items() if v is not None}
    if not fields.get("all_day"):
        body.pop("all_day", None)
    return body


def _update_body(changes: dict[str, Any]) -> dict[str, Any]:
    """Request body for `update_calendar_event`. Unknown keys raise instead of vanishing."""
    unknown = sorted(set(changes) - set(_EVENT_UPDATE_FIELDS))
    if unknown:
        raise TypeError(f"update_calendar_event() got unexpected field(s): {', '.join(unknown)}. "
                        f"Allowed: {', '.join(_EVENT_UPDATE_FIELDS)}")
    body = {k: v for k, v in changes.items() if v is not None}
    if not body:
        raise TypeError("update_calendar_event() needs at least one field to change")
    return body


def _check_direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS!r}, got {direction!r}")
    return direction


@dataclass
class Attachment:
    """Attachment metadata. The bytes are fetched on demand with `.download()`.

    Contents are never included in a message listing: a single 5 MB attachment would make
    the list unusable, so the bytes live behind a separate request.
    """

    id: int | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    is_encrypted: bool = False
    truncated: bool = False  # True → file exceeded the storage cap; bytes were never stored
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    _message: "Message | None" = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Attachment":
        return cls(
            id=data.get("id"),
            filename=data.get("filename"),
            content_type=data.get("content_type"),
            size_bytes=data.get("size_bytes"),
            is_encrypted=bool(data.get("is_encrypted")),
            truncated=bool(data.get("truncated")),
            raw=data,
        )

    def download(self) -> bytes:
        """Download this attachment's bytes.

        Raises `MailFlatError` when the attachment is not attached to a message, when the
        file was too large to store (`truncated`), or when the inbox is end-to-end encrypted
        (the server cannot decrypt it).
        """
        if self._message is None or self._message._inbox is None or self.id is None:
            raise MailFlatError(
                "This attachment isn't attached to a message. "
                "Use inbox.download_attachment(message_id, attachment_id) instead."
            )
        if self.truncated:
            raise MailFlatError(f"Attachment {self.filename!r} was too large to store")
        return self._message._inbox.download_attachment(self._message.id, self.id)


@dataclass
class Message:
    """A single email — the typed form of the /api/v1 payload."""

    id: int | None = None
    sender: str | None = None
    subject: str | None = None
    text: str | None = None            # body_text
    html: str | None = None            # body_html
    otp: str | None = None             # otp_code (when the server extracted one)
    tag: str | None = None
    to_address: str | None = None
    is_encrypted: bool = False
    direction: str | None = None       # "in" | "out"
    is_read: bool = False
    send_status: str | None = None
    send_error: str | None = None
    received_at: str | None = None
    # URLs found in the body (HTML hrefs first, then plain text). Verification-link flows
    # read this instead of parsing the body themselves.
    links: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    # {score, required, is_spam, rules, scanner}, or None when the message was NOT SCANNED
    # (not the same as a score of 0.0).
    # NOTE: spam scanning is currently disabled on mailflat.net, so in practice this is
    # None for every message today. The field is wired end to end and will start carrying
    # data as soon as scanning is switched back on.
    spam: dict[str, Any] | None = None
    # Raw message headers (Message-ID lives here — needed for threading and dedup).
    # None on encrypted inboxes: headers carry the Subject, so they go inside the envelope.
    headers: dict[str, Any] | None = None
    # Calendar invitation carried by this message, parsed ({uid, title, start, end, organizer,
    # attendees, method, action, event_id, ...}), or None. `action` says what the invitation
    # did to the inbox calendar: created / updated / cancelled / replied / unchanged.
    # The event's CURRENT state lives in `Inbox.calendar_event(event_id)`.
    calendar_event: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    # The Inbox this message came from (powers msg.delete()). Filled in by
    # Inbox.messages() / latest() / wait_*.
    _inbox: "Inbox | None" = field(default=None, repr=False, compare=False)

    def delete(self) -> dict[str, Any]:
        """Delete this message (the inbox itself stays)."""
        if self._inbox is None or self.id is None:
            raise ValueError(
                "This message isn't attached to an inbox (or has no id). "
                "Use inbox.delete_message(message_id) instead."
            )
        return self._inbox.delete_message(self.id)

    def header(self, name: str, default: Any = None) -> Any:
        """Look up a header case-insensitively, e.g. `msg.header("message-id")`.

        Header names are case-insensitive per RFC 5322, but `headers` is a plain dict whose
        keys come straight off the wire — the real key is `Message-ID`, so
        `headers["Message-Id"]` raises KeyError. Use this instead of indexing.
        """
        if not self.headers:
            return default
        target = name.replace("_", "-").lower()
        for key, value in self.headers.items():
            if str(key).lower() == target:
                return value
        return default

    @property
    def message_id(self) -> str | None:
        """This message's `Message-ID`, or None when headers were not stored."""
        return self.header("message-id")

    @property
    def reply_to_address(self) -> str | None:
        """Where a reply should actually go: `Reply-To`, else `From`, else the envelope sender.

        `.sender` is the SMTP envelope sender (MAIL FROM). For transactional mail that is
        usually a bounce address such as `bounces+abc@sendgrid.net`, so replying to it reaches
        a bounce processor rather than a person. RFC 5322 says a reply goes to `Reply-To` when
        present and to `From` otherwise; the envelope address is only a last resort.
        """
        for name in ("reply-to", "from"):
            value = self.header(name)
            if isinstance(value, list):
                value = value[0] if value else None
            if value:
                addr = parseaddr(str(value))[1]
                if addr and "@" in addr:
                    return addr
        return self.sender

    def reply(self, body: str = "", *, html: str | None = None,
              subject: str | None = None, cc: list[str] | None = None,
              bcc: list[str] | None = None,
              attachments: list[Any] | None = None) -> dict[str, Any]:
        """Reply to this message, keeping it in the same conversation.

        Fills in what a reply needs and is easy to get wrong by hand: the recipient, an
        `Re:` subject that is not doubled up, and the `In-Reply-To` / `References` headers
        Gmail and Outlook use to thread. Without those headers a reply shows up as a separate
        conversation, which is what a hand-rolled `send()` produces.

        The recipient comes from `reply_to_address`, not from `.sender`: the envelope sender of
        transactional mail is usually a bounce address, so `send(to=msg.sender, ...)` quietly
        delivers the reply to a machine.

        Takes everything `send()` takes, attachments included — answering a mail with a
        file attached is the normal case, and an agent should not have to drop back to
        `send()` (losing the threading headers) just to attach one.
        """
        if self._inbox is None:
            raise ValueError(
                "This message isn't attached to an inbox. Use inbox.send(...) instead."
            )
        target = self.reply_to_address
        if not target:
            raise ValueError("This message has no sender to reply to.")
        return self._inbox.send(
            target,
            subject=subject if subject is not None else _reply_subject(self.subject),
            body=body,
            html=html,
            in_reply_to=self.message_id,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
        )

    def mark_read(self) -> dict[str, Any]:
        """Mark this message as read, so the next poll can skip it."""
        if self._inbox is None or self.id is None:
            raise ValueError(
                "This message isn't attached to an inbox (or has no id). "
                "Use inbox.mark_read(message_id) instead."
            )
        return self._inbox.mark_read(self.id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        msg = cls(
            id=data.get("id"),
            sender=data.get("sender"),
            subject=data.get("subject"),
            text=data.get("body_text"),
            html=data.get("body_html"),
            otp=data.get("otp_code"),
            tag=data.get("tag"),
            to_address=data.get("to_address"),
            is_encrypted=bool(data.get("is_encrypted")),
            direction=data.get("direction"),
            is_read=bool(data.get("is_read")),
            send_status=data.get("send_status"),
            send_error=data.get("send_error"),
            received_at=data.get("received_at"),
            links=list(data.get("links") or []),
            spam=data.get("spam"),
            headers=data.get("headers"),
            calendar_event=data.get("calendar_event"),
            raw=data,
        )
        msg.attachments = [Attachment.from_dict(a) for a in (data.get("attachments") or [])]
        for att in msg.attachments:
            att._message = msg
        return msg


class Inbox:
    """Operations on a single MailFlat inbox.

    Obtained from `MailFlat.create()` / `.list()` / `.inbox(address)` rather than
    constructed directly.
    """

    def __init__(self, client: "MailFlat", address: str, **meta: Any) -> None:
        self._client = client
        self.address: str = address
        # Extra fields carried by the create/list response, when present.
        self.name: str | None = meta.get("name")
        self.api_key: str | None = meta.get("api_key")  # per-inbox key (mf_sk_...)
        self.retention_hours: int | None = meta.get("retention_hours")
        self.encrypted: bool = bool(meta.get("encrypted"))
        self.via_api: bool | None = meta.get("via_api")
        self.created_at: str | None = meta.get("created_at")
        # The full backend payload (symmetric with Message.raw) — used by the MCP/agent layer.
        self.raw: dict[str, Any] = {"address": address, **meta}

    def __repr__(self) -> str:
        return f"<Inbox {self.address!r}>"

    # ----------------------------------------------------------------- okuma
    def _wrap(self, data: dict[str, Any]) -> Message:
        """Convert an API payload into a Message and attach this inbox (for msg.delete())."""
        m = Message.from_dict(data)
        m._inbox = self
        return m

    def messages(self, *, direction: str = "in") -> list[Message]:
        """Return this inbox's messages, newest first.

        `direction` defaults to `"in"`: received mail only. Pass `"out"` for mail you sent
        from this address, or `"all"` for both.
        """
        _check_direction(direction)
        res = self._client._get(
            f"/api/v1/inboxes/{self.address}/messages?direction={direction}")
        return [self._wrap(e) for e in (res.get("emails") or [])]

    def message(self, message_id: int) -> Message:
        """Fetch one message by id — the way to ask "what happened to this send?".

        Without it the only way to check a sent mail's status was to pull the whole
        outbound list and find the id by hand, which for an agent that sent 100 mails
        meant 100 messages per check.
        """
        res = self._client._get(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}")
        return self._wrap(res.get("email") or {})

    def latest(self, *, direction: str = "in") -> Message | None:
        """Return the most recent message, or None when the inbox is empty.

        Defaults to received mail. Before 0.4.0 this also returned mail you had just sent,
        which made `send()` followed by `wait_for_message()` return your own message.
        """
        _check_direction(direction)
        res = self._client._get(
            f"/api/v1/inboxes/{self.address}/latest?direction={direction}")
        email = res.get("email")
        return self._wrap(email) if email else None

    def wait_for_message(
        self, *, timeout: float = 30, poll_interval: float = 1.0, direction: str = "in"
    ) -> Message:
        """Poll until a message arrives and return it.

        Only received mail counts by default, so an agent can `send()` to a peer and then
        wait for the reply without immediately matching its own outgoing message.

        Raises `OTPTimeoutError` on timeout, `EncryptedInboxError` on an E2E inbox.
        """
        _check_direction(direction)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            res = self._client._get(
                f"/api/v1/inboxes/{self.address}/latest?direction={direction}")
            if res.get("encrypted"):
                raise EncryptedInboxError(
                    res.get("note") or "This inbox is end-to-end encrypted; "
                    "use a non-encrypted inbox for agent automation."
                )
            email = res.get("email")
            if email:
                return self._wrap(email)
            if time.monotonic() >= deadline:
                raise OTPTimeoutError(
                    f"No message arrived for {self.address} within {timeout}s"
                )
            time.sleep(poll_interval)

    def wait_for_otp(self, *, timeout: float = 30, poll_interval: float = 1.0) -> str:
        """Poll until an OTP arrives and return the code.

        Raises `OTPTimeoutError` on timeout, `EncryptedInboxError` on an E2E inbox.

        If mail did arrive but no code could be extracted from it, the timeout error says so
        and quotes the newest message, so you can read `inbox.latest().text` yourself instead
        of hunting for a delivery problem that does not exist.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        seen: dict[str, Any] | None = None      # newest INCOMING mail, code or not
        while True:
            res = self._client._get(f"/api/v1/inboxes/{self.address}/latest?direction=in")
            if res.get("encrypted"):
                raise EncryptedInboxError(
                    res.get("note") or "This inbox is end-to-end encrypted; "
                    "OTP cannot be read via the API."
                )
            email = res.get("email") or {}
            otp = email.get("otp_code")
            if otp:
                return otp
            if email:
                seen = email
            if time.monotonic() >= deadline:
                raise OTPTimeoutError(self._otp_timeout_message(timeout, seen))
            time.sleep(poll_interval)

    def _otp_timeout_message(self, timeout: float, seen: dict[str, Any] | None) -> str:
        """Kept as a method for compatibility; the wording itself is shared (see below)."""
        return otp_timeout_message(self.address, timeout, seen)

    def download_attachment(self, message_id: int, attachment_id: int) -> bytes:
        """Download one attachment's bytes.

        Metadata already ships with each message (`msg.attachments`); this fetches the file
        itself. Prefer `msg.attachments[0].download()`.
        """
        resp = self._client._get_bytes(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}/attachments/{attachment_id}"
        )
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            # On an encrypted inbox the server cannot decrypt: it returns the envelope
            # JSON rather than the bytes.
            raise EncryptedInboxError(
                "This inbox is end-to-end encrypted; the server cannot decrypt this attachment."
            )
        return resp.content

    # ----------------------------------------------------------------- yazma
    def send(
        self, to: str, *, subject: str = "", body: str = "", html: str | None = None,
        in_reply_to: str | None = None, cc: list[str] | None = None,
        bcc: list[str] | None = None, attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Send mail from this inbox's address (DKIM-signed, through our own MTA).

        Returns as soon as the mail is **accepted for delivery** — not once it is
        delivered. Delivery happens on a queue, so the response carries `message_id` and
        `queued: true`; use `wait_until_sent(message_id)` (or a `message.delivered`
        webhook) to find out how it ended. Blocking here would put back the exact stall
        the queue was built to remove.

        `attachments` takes file paths or dicts — the base64 encoding is done here:

            inbox.send(to, attachments=["/tmp/invoice.pdf"])
            inbox.send(to, attachments=[{"filename": "a.pdf", "content": raw_bytes}])

        Size and count limits depend on the plan (free is deliberately small); going over
        raises with the limit spelled out rather than silently dropping the file.

        `bcc` recipients receive the mail but never appear in its headers — not even in
        their own copy.

        Pass `in_reply_to` (a `Message-ID`) to keep the mail in an existing conversation;
        without it the recipient's client starts a new thread. `Message.reply()` fills this
        in for you.

        Not retried on gateway errors: a retried send can deliver the same mail twice.
        """
        payload = _send_payload(to, subject, body, html, in_reply_to, cc, bcc, attachments)
        return self._client._post(f"/api/v1/inboxes/{self.address}/send", payload)

    def wait_until_sent(self, message_id: int, *, timeout: float = 120.0,
                        poll_interval: float = 2.0) -> Message:
        """Block until a sent mail reaches a final delivery state.

        `send()` is asynchronous, so "did it actually go out?" has no synchronous answer.
        This polls the message until the server reports one, which is the pull half of the
        contract (the push half is the `message.delivered` / `message.failed` webhook —
        prefer that when you can receive one).

        Returns the message on success. Raises `SendFailedError` if delivery permanently
        failed and `SendTimeoutError` if it is still queued when the timeout elapses.
        It does NOT return quietly on failure: a helper called `wait_until_sent` that
        hands back a failed mail as if nothing happened is how mail goes missing.
        """
        deadline = time.monotonic() + timeout
        while True:
            message = self.message(message_id)
            status = message.send_status
            if status in ("sent", "unsigned"):
                return message
            if status == "failed":
                raise SendFailedError(
                    message.send_error or "Delivery failed",
                    message_id=message_id, status=status)
            if time.monotonic() >= deadline:
                raise SendTimeoutError(
                    send_timeout_message(message_id, status, timeout, message.send_error),
                    message_id=message_id, status=status,
                    last_error=message.send_error)
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def mark_read(self, message_id: int) -> dict[str, Any]:
        """Mark one message as read.

        Lets an agent poll for *new* mail instead of re-reading the whole inbox and
        tracking state on its own.
        """
        return self._client._post(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}/read", idempotent=True)

    # ------------------------------------------------------------ calendar
    def calendar_events(self, *, include_cancelled: bool = False) -> list[dict[str, Any]]:
        """Events on this inbox's calendar, built from the invitations it received.

        Sorted by start time. An event outlives the email that carried it: retention can
        delete the invitation, the meeting stays. Cancelled events are hidden unless
        `include_cancelled=True`.
        """
        flag = "true" if include_cancelled else "false"
        res = self._client._get(
            f"/api/v1/inboxes/{self.address}/calendar/events?include_cancelled={flag}")
        return list(res.get("events") or [])

    def calendar_event(self, event_id: int) -> dict[str, Any]:
        """One event, current state (if the organizer moved it, the new time is here)."""
        return self._client._get(f"/api/v1/inboxes/{self.address}/calendar/events/{event_id}")

    def rsvp(self, event_id: int, response: str, *, comment: str | None = None) -> dict[str, Any]:
        """Answer an invitation: `"accepted"`, `"declined"` or `"tentative"`.

        Sends a standard iCalendar REPLY email to the organizer, so their Google or Outlook
        calendar shows this inbox's answer. Returns `{ok, event, message_id, send_status}`;
        the reply is queued like any send (use `wait_until_sent(message_id)` to confirm).
        Not retried automatically: a retry after a lost response would answer twice.
        """
        body: dict[str, Any] = {"response": response}
        if comment is not None:
            body["comment"] = comment
        return self._client._post(
            f"/api/v1/inboxes/{self.address}/calendar/events/{event_id}/rsvp", json=body)

    def create_calendar_event(self, title: str, start: str, *,
                              attendees: Sequence[str | dict[str, Any]],
                              end: str | None = None, duration_minutes: int | None = None,
                              timezone: str | None = None, all_day: bool = False,
                              location: str | None = None, description: str | None = None,
                              message: str | None = None,
                              in_reply_to: str | None = None) -> dict[str, Any]:
        """Schedule a meeting and email the invitations. This inbox is the organizer.

        Attendees get a normal invitation with Yes / No / Maybe buttons in Gmail, Outlook or
        Apple Calendar; their answers update this event (`attendees[].status`) and fire the
        `calendar.attendee.responded` webhook.

        `start` is ISO 8601 with an offset (`"2026-10-06T14:00:00-04:00"`), or without one plus
        `timezone` (`"America/New_York"`). Give `end` or `duration_minutes` (default 30).
        All-day: `all_day=True`, dates only, `end` exclusive. An attendee is an email or
        `{"email", "name", "optional"}`. `in_reply_to` threads the invitation under an email.

        Returns `{ok, event, message_id, send_status}`; the invitation is queued like any send.
        Not retried automatically: a retry after a lost response would invite everyone twice.
        """
        return self._client._post(
            f"/api/v1/inboxes/{self.address}/calendar/events",
            json=_event_body(title=title, start=start, attendees=list(attendees), end=end,
                             duration_minutes=duration_minutes, timezone=timezone,
                             all_day=all_day, location=location, description=description,
                             message=message, in_reply_to=in_reply_to))

    def update_calendar_event(self, event_id: int, **changes: Any) -> dict[str, Any]:
        """Change a meeting this inbox organized; attendees get the updated invitation.

        Pass only what changes: `title`, `start`, `end`, `duration_minutes`, `timezone`,
        `all_day`, `location`, `description`, `message`. The duration is kept when only `start`
        moves. A new time resets every attendee's answer to `needs-action`. The same event
        is updated in their calendars (no duplicate).
        """
        return self._client._patch(
            f"/api/v1/inboxes/{self.address}/calendar/events/{event_id}",
            json=_update_body(changes))

    def cancel_calendar_event(self, event_id: int, *, message: str | None = None) -> dict[str, Any]:
        """Cancel a meeting this inbox organized; it disappears from attendees' calendars."""
        body = {"message": message} if message is not None else {}
        return self._client._post(
            f"/api/v1/inboxes/{self.address}/calendar/events/{event_id}/cancel", json=body)

    def burn(self) -> dict[str, Any]:
        """Delete every message in this inbox and keep the address.

        Useful between test scenarios: the address stays registered wherever you used it.
        """
        return self._client._post(f"/api/v1/inboxes/{self.address}/burn", idempotent=True)

    def delete(self) -> dict[str, Any]:
        """Delete this inbox and all of its messages. Cannot be undone."""
        return self._client._delete(f"/api/v1/inboxes/{self.address}")

    def delete_message(self, message_id: int) -> dict[str, Any]:
        """Delete a single message; the inbox stays. Backs `Message.delete()`."""
        return self._client._delete(
            f"/api/v1/inboxes/{self.address}/messages/{message_id}")
