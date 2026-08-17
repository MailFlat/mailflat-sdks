"""Strips secret fields from agent tool output (B-055).

Why it exists: `create_inbox` and `list_inboxes` used to return the backend payload as-is,
and that payload carried a per-inbox `api_key` (`mf_sk_...`). Tool output goes into the
MODEL's context, and from there into prompt logs, tracing tools (LangSmith, AI SDK
telemetry), error reports and the model's own later answers. `list_inboxes` leaked every
inbox key on the account in a single call.

The split is deliberate:
  - **SDK = code surface.** `Inbox.api_key` stays; code a human wrote may use it.
  - **Tool = model surface.** Everything passing through here is redacted.

Redaction is name-based **and** value-based, but the two apply to different places:

  - **Name-based, everywhere.** A field called `api_key` never belongs in model context.
  - **Value-based, everywhere EXCEPT message content.** A key can reach the model through a
    field nobody thought to name (`account`, an error sentence, a nested payload), and the
    `mf_live_` / `mf_sk_` prefixes are ours and unmistakable. Message BODIES are excluded on
    purpose and that exclusion is the original reason value scanning was rejected: a real
    email discussing a test key would be corrupted, and mangling the mail an agent is
    waiting for is a worse failure than the one being prevented.

Connected to:
  - imports from: —
  - imported by:  mailflat.langchain and this package's tests. Within the repo,
    `packages/mcp` (mailflat-mcp, a separate PyPI package) also imports it — that package
    is not bundled here, so the reference is repo-level.

Key exports:
  - `redact_secrets(value)` — copy of the input with secret fields removed
  - `SECRET_NAME_PARTS` — which field names count as secrets
"""
from __future__ import annotations

from typing import Any

#: A field is dropped when its normalised name CONTAINS one of these.
#: Kept deliberately narrow: `token` is absent on purpose — fields like a domain
#: `verify_token` are harmless and sometimes useful, and `otp` is the very thing the agent
#: is after. None of the three below is ever useful to a model.
SECRET_NAME_PARTS = ("apikey", "secret", "password")

#: Our own key prefixes. They cannot occur by accident in user data — we mint them. An
#: external review showed a real `mf_live_` key passing through untouched in a field whose
#: name was not on the list (`account`): a name-based defence cannot see a name nobody
#: thought of, and no list of names is ever complete.
SECRET_VALUE_PREFIXES = ("mf_live_", "mf_sk_")

#: Where value scanning does NOT apply: the mail's own content. An email may legitimately
#: discuss a test key, and mangling it breaks the very message the agent is waiting for —
#: a worse failure than the one being prevented. This exception is the original reason
#: value scanning was rejected. It was not removed; it was NARROWED to content fields.
CONTENT_FIELDS = ("body", "bodytext", "bodyhtml", "text", "html", "ciphertext", "raw")


def _is_content_field(key: Any) -> bool:
    return _normalise(key) in CONTENT_FIELDS


def _redact_value(value: Any) -> Any:
    """Mask the value when it is (or contains) one of our keys."""
    if not isinstance(value, str):
        return value
    for prefix in SECRET_VALUE_PREFIXES:
        if prefix in value:
            return _mask(value)
    return value


def _mask(text: str) -> str:
    import re as _re
    pattern = "|".join(_re.escape(p) for p in SECRET_VALUE_PREFIXES)
    return _re.sub(rf"({pattern})[A-Za-z0-9_\-]*", r"\1[redacted]", text)


def _normalise(key: Any) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


def _is_secret(key: Any) -> bool:
    name = _normalise(key)
    return any(part in name for part in SECRET_NAME_PARTS)


def redact_secrets(value: Any) -> Any:
    """Return a copy of `value` with secret fields removed. The input is not modified.

    dict → secret keys dropped, remaining values cleaned recursively
    list/tuple → every element cleaned (returned as a list; JSON carries a list anyway)
    anything else → returned unchanged
    """
    if isinstance(value, dict):
        return {k: (v if _is_content_field(k) else redact_secrets(v))
                for k, v in value.items() if not _is_secret(k)}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(v) for v in value]
    return _redact_value(value)
