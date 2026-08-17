"""MailFlat SDK error types — maps HTTP status codes to meaningful Python exceptions.

Connected to:
  - imported by:  mailflat.client, mailflat.inbox, mailflat.__init__

Key exports:
  - `MailFlatError` — base class for every SDK error
  - `AuthenticationError` — 401 (missing/invalid API key)
  - `MailFlatPermissionError` — 403 (key does not own this inbox)
  - `NotFoundError` — 404
  - `RateLimitError` — 429 (carries `retry_after` when the server sent it)
  - `APIError` — any other 4xx/5xx
  - `OTPTimeoutError` — wait_for_otp / wait_for_message timed out
  - `SendFailedError` — a sent mail permanently failed to be delivered
  - `SendTimeoutError` — wait_until_sent gave up while the mail was still queued
  - `EncryptedInboxError` — end-to-end encrypted inbox cannot be read through the API
"""
from __future__ import annotations


class MailFlatError(Exception):
    """Base class for every MailFlat SDK error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthenticationError(MailFlatError):
    """401 — the API key is missing or invalid."""


class MailFlatPermissionError(MailFlatError):
    """403 — this API key does not own that inbox."""


# Backwards compatibility: up to 0.3.3 this class was named `PermissionError` and SHADOWED
# the built-in. Someone writing `except PermissionError` in a module that never imported the
# SDK would catch the built-in and miss ours (or the reverse) — a silent, hard-to-diagnose
# bug. The old name stays as an alias so existing code keeps working.
PermissionError = MailFlatPermissionError  # noqa: A001 - deprecated alias, kept for compatibility


class NotFoundError(MailFlatError):
    """404 — the resource does not exist."""


class RateLimitError(MailFlatError):
    """429 — rate limit exceeded.

    `retry_after` holds the server's `Retry-After` value in seconds when it sent one,
    so callers can wait exactly as long as the server asked instead of guessing.
    """

    def __init__(self, message: str, *, status_code: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class APIError(MailFlatError):
    """An unexpected 4xx/5xx response."""


class OTPTimeoutError(MailFlatError):
    """No OTP (or message) arrived before the timeout elapsed."""


class SendError(MailFlatError):
    """Base for the two ways `wait_until_sent()` can end badly.

    Separate from `OTPTimeoutError` on purpose. That one means *"the mail I was waiting
    for never arrived"*; these mean *"the mail I sent did not go out"*. Reusing one name
    for both would let a single `except` clause hide two unrelated faults.
    """

    def __init__(self, message: str, *, message_id: int | None = None,
                 status: str | None = None, last_error: str | None = None) -> None:
        super().__init__(message)
        self.message_id = message_id
        #: Last delivery status seen — `"queued"` (never attempted) or `"retrying"`
        #: (attempted, not through yet) on timeout, `"failed"` on failure.
        self.status = status
        #: What the most recent delivery attempt reported, when there has been one.
        #: On a timeout this is the difference between "greylisted, try again in two
        #: minutes" and a problem no amount of waiting will fix — the caller could not
        #: tell those apart before, because the timeout sentence described neither.
        self.last_error = last_error


class SendFailedError(SendError):
    """Delivery permanently failed; the queue will not retry it.

    `.message` carries the server's reason (a `5xx` from the recipient's MX, an unknown
    mailbox, and so on).
    """


class SendTimeoutError(SendError):
    """Still queued when `wait_until_sent()` gave up.

    NOT the same as failure: the queue keeps retrying with exponential backoff, so the
    mail may still be delivered. Greylisting alone can hold a mail for several minutes.
    """


class EncryptedInboxError(MailFlatError):
    """The inbox is end-to-end encrypted, so the server cannot read its contents."""


def raise_for_status(status_code: int, detail: str, *, retry_after: float | None = None) -> None:
    """Raise the matching `MailFlatError` subclass for an HTTP status code."""
    if status_code == 401:
        raise AuthenticationError(detail or "Invalid or missing API key", status_code=status_code)
    if status_code == 403:
        raise MailFlatPermissionError(
            detail or "This API key does not own that inbox", status_code=status_code)
    if status_code == 404:
        raise NotFoundError(detail or "Not found", status_code=status_code)
    if status_code == 429:
        raise RateLimitError(detail or "Rate limit exceeded", status_code=status_code,
                             retry_after=retry_after)
    raise APIError(detail or f"Request failed with status {status_code}", status_code=status_code)
