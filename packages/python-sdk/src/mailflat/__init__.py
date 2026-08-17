"""MailFlat Python SDK — the official client for automation-friendly email inboxes.

`pip install mailflat` → `from mailflat import MailFlat`.

Connected to:
  - imports from: mailflat.client, mailflat.inbox, mailflat.errors
  - imported by:  user code, mailflat-mcp, mailflat.langchain

Key exports:
  - `MailFlat` — client · `mailflat.aio.AsyncMailFlat` — the same API over asyncio
  - `Inbox`, `Message`, `Attachment` — domain types
  - errors: `MailFlatError`, `AuthenticationError`, `MailFlatPermissionError`,
    `NotFoundError`, `RateLimitError`, `APIError`, `OTPTimeoutError`,
    `SendFailedError`, `SendTimeoutError`, `EncryptedInboxError`
  - `redact_secrets()` — strips secret fields from agent tool output (used by MCP/LangChain)
  - `mailflat.agent_results` — the delivery-status payload every model surface returns
"""
from __future__ import annotations

from .agent_results import (
    SEND_FAILED_NOTE,
    SEND_QUEUED_NOTE,
    SEND_RESULT_KEYS,
    SEND_RETRYING_NOTE,
    failed_result,
    queued_result,
    sent_result,
)
from .client import MailFlat
from .errors import (
    APIError,
    AuthenticationError,
    EncryptedInboxError,
    MailFlatError,
    MailFlatPermissionError,
    NotFoundError,
    OTPTimeoutError,
    PermissionError,
    RateLimitError,
    SendError,
    SendFailedError,
    SendTimeoutError,
)
from .inbox import Attachment, Inbox, Message
from .redact import redact_secrets

# The package's ONLY version source — pyproject.toml reads it via [tool.hatch.version].
# Releasing = bump this line and nothing else.
__version__ = "0.11.0"

__all__ = [
    "MailFlat",
    "Inbox",
    "Message",
    "Attachment",
    "MailFlatError",
    "AuthenticationError",
    "MailFlatPermissionError",
    "PermissionError",  # deprecated alias of MailFlatPermissionError
    "NotFoundError",
    "RateLimitError",
    "APIError",
    "OTPTimeoutError",
    "SendError",
    "SendFailedError",
    "SendTimeoutError",
    "EncryptedInboxError",
    "redact_secrets",
    "SEND_RESULT_KEYS",
    "SEND_QUEUED_NOTE",
    "SEND_RETRYING_NOTE",
    "SEND_FAILED_NOTE",
    "sent_result",
    "queued_result",
    "failed_result",
    "__version__",
]
