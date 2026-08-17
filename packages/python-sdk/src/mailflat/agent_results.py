"""The shape a MODEL sees when it asks "did my mail actually go out?".

`send()` is asynchronous (the endpoint answers 202 Accepted), so every model surface needs
a way to ask about delivery afterwards. Code surfaces raise exceptions for that; a model
surface hands the model a dictionary and the model decides what to do next. Those
dictionaries are built HERE, once, so the MCP server and both LangChain toolkits cannot
drift apart — the same reason `should_retry`/`backoff_delay` live in `_http.py`.

⚠️ The dangerous branch is the TIMEOUT, not the failure. A timeout means "still queued,
the queue is still retrying" — it does NOT mean the mail was lost. A model that reads a
timeout as failure will send the mail a second time, so the payload says `delivered: false`
without ever saying "failed", and states outright that resending is wrong.

Connected to:
  - imports from: mailflat.redact
  - imported by:  mailflat.langchain (both toolkits), mailflat_mcp.server

Key exports:
  - `SEND_RESULT_KEYS` — the keys every surface must return (cross-surface parity lock)
  - `SEND_QUEUED_NOTE` / `SEND_RETRYING_NOTE` / `SEND_FAILED_NOTE` — the sentences read
  - `sent_result()`, `queued_result()`, `failed_result()` — the three outcomes
"""
from __future__ import annotations

from typing import Any

from .redact import redact_secrets

#: Every `wait_until_sent` result carries exactly these keys, on every surface — including
#: the branches that end badly, which is where parity used to break. A model that learned
#: to read one surface can read the others.
#:
#: 🔒 Locked by `QA/sdk-parity/`, which CALLS all four model surfaces (MCP over the real
#: protocol, both LangChain toolkits, the AI SDK tool) on all three branches and reads the
#: expected key set from THIS tuple. The previous claim in this comment — "a test compares
#: this tuple with what the TypeScript surface returns" — was not true: nothing referenced
#: `SEND_RESULT_KEYS`, one TS test hard-coded a third copy of the list, it covered only the
#: queued branch, and the branch that had actually drifted (failed, on MCP) was the one
#: nobody looked at. An external round found it there (B-097).
SEND_RESULT_KEYS = ("status", "delivered", "timed_out", "message", "note", "error")

#: Shown when the mail was never even attempted. The last sentence is the point of the
#: whole module.
#:
#: Written WITHOUT the word "fail" on purpose, and locked that way by test. The first draft
#: opened with "This is NOT a failure" — accurate, but it puts the very word we are trying
#: to keep out of the model's head into the payload, and negations are the first thing a
#: model skims past. Saying what is true ("still on its way") beats denying what is false.
SEND_QUEUED_NOTE = (
    "Still queued when the timeout elapsed. The queue keeps retrying with backoff, so this "
    "mail is still on its way. Do NOT send it again — a duplicate would reach the recipient "
    "twice. Ask again later, or subscribe to the message.delivered webhook."
)

#: Shown when delivery HAS been attempted and has not got through yet.
#:
#: Same ban on the word "fail", same instruction not to resend — but a different fact. The
#: queue separates `queued` (never attempted) from `retrying` (attempted, scheduled again)
#: and the structural field carried that distinction correctly, while this sentence said
#: "Still queued" for both and erased it again (B-099). Two independent external rounds
#: reported it the same day. The field and the prose come from different layers; only the
#: prose reaches the model's reasoning.
SEND_RETRYING_NOTE = (
    "Delivery was attempted and has not got through yet. The queue has already scheduled "
    "another attempt with backoff, so this mail is still on its way. Read `error` for what "
    "the last attempt reported: a temporary rejection such as greylisting clears on its "
    "own. Do NOT send it again — a duplicate would reach the recipient twice. Ask again "
    "later, or subscribe to the message.delivered webhook."
)

#: Shown when the queue gave up. Here resending after fixing the cause IS the right move.
SEND_FAILED_NOTE = (
    "Delivery permanently failed and the queue will not retry. Read `error` for the "
    "reason (a rejection from the recipient's mail server, an unknown mailbox, and so on). "
    "Fix the cause before sending again."
)


def sent_result(message: Any) -> dict[str, Any]:
    """The mail reached a final, successful state.

    `unsigned` counts as delivered: it means the mail went out without a DKIM signature,
    not that it stayed behind. Reporting it as undelivered would make an agent resend a
    mail the recipient already has.
    """
    return redact_secrets({
        "status": message.send_status,
        "delivered": True,
        "timed_out": False,
        "message": message.raw,
        "note": "",
        "error": None,
    })


def queued_result(error: Any) -> dict[str, Any]:
    """We stopped waiting; the queue did not. Built from a `SendTimeoutError`.

    The note follows the STATUS, because the two are not the same answer: `queued` means
    nothing has been tried, `retrying` means something was tried and did not get through.
    A model deciding "keep waiting or give up?" needs that difference, and `last_error` is
    the evidence behind it — `errors.py` calls it the difference between "greylisted, try
    again in two minutes" and a problem no amount of waiting will fix. It was sitting on
    the exception and never reached the payload (B-099).
    """
    status = getattr(error, "status", None) or "queued"
    return redact_secrets({
        "status": status,
        "delivered": False,
        "timed_out": True,
        "message": None,
        "note": SEND_RETRYING_NOTE if status == "retrying" else SEND_QUEUED_NOTE,
        "error": getattr(error, "last_error", None),
    })


def failed_result(error: Any) -> dict[str, Any]:
    """Delivery is over and it did not work. Built from a `SendFailedError`.

    `error` carries the reason: the only thing that lets the caller decide whether a
    corrected resend makes sense.
    """
    return redact_secrets({
        "status": getattr(error, "status", None) or "failed",
        "delivered": False,
        "timed_out": False,
        "message": None,
        "note": SEND_FAILED_NOTE,
        "error": str(error),
    })
