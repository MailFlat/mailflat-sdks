# MailFlat — Python SDK

Official Python client for [MailFlat](https://mailflat.net): real email inboxes for AI
agents and test suites, with **one-line OTP retrieval**. Open an inbox, read the verification
code your app just sent, and move on. One call instead of a hand-rolled poll loop, no shared
mailbox state. The address is permanent and stays until you delete it; only the messages
inside expire, on a retention window you choose.

```bash
pip install mailflat
```

## Quickstart

```python
from mailflat import MailFlat

mf = MailFlat(api_key="mf_live_...")  # or set MAILFLAT_API_KEY

# 1 · open an inbox
inbox = mf.create(prefix="signup-test", label="checkout flow")
print(inbox.address)            # → signup-test@x7k2m.mailflat.net

# 2 · your app/browser submits the form using inbox.address ...

# 3 · grab the OTP (polls until it arrives or times out)
otp = inbox.wait_for_otp(timeout=30)
print(otp)                      # → "123456"

# 4 · answering a human keeps the same email thread
msg = inbox.wait_for_message(timeout=60)
msg.reply("thanks, received!")

# inbox auto-clears in 2h — no cleanup needed (or call inbox.delete())
```

## For AI agents

Hand an agent one API key and it spins up real inboxes on demand:

```python
mf = MailFlat()  # reads MAILFLAT_API_KEY

inbox = mf.create(prefix="deep-research")
browser.fill("#email", inbox.address)
browser.click("Sign up")

otp = inbox.wait_for_otp(timeout=30)
browser.fill("#code", otp)
```

## API

### `MailFlat(api_key=None, *, base_url="https://mailflat.net", timeout=30.0, max_retries=2, http_client=None)`
Client. `api_key` falls back to the `MAILFLAT_API_KEY` environment variable. Use `base_url`
for self-hosted / BYOD deployments. Supports use as a context manager (`with MailFlat() as mf:`).
Pass `http_client=` an `httpx.Client` to inject your own transport — this is how you drive the
SDK in tests without a network (e.g. `httpx.MockTransport`).

- `create(*, prefix=None, label=None, subdomain=None, domain=None, retention_hours=None) -> Inbox`
  — open a new inbox. `create_inbox(...)` is an alias.
  **`prefix` shapes the address; `label` does not.** `prefix="signup-test"` gives you
  `signup-test@…`, while `label` is only a name shown in the dashboard so you can tell your
  inboxes apart. Omitting `prefix` yields a random `agent-…` address.
- `list() -> list[Inbox]` — inboxes opened with this key.
- `inbox(address) -> Inbox` — attach to an existing address without a network call.

### `Inbox`
- `.address` — the email address.
- `.messages(*, direction="in") -> list[Message]` — messages, newest first.
- `.latest(*, direction="in") -> Message | None` — most recent message.
- `.wait_for_otp(*, timeout=30, poll_interval=1.0) -> str` — poll until an OTP arrives; returns the code.
- `.wait_for_message(*, timeout=30, poll_interval=1.0, direction="in") -> Message` — poll until a message arrives.
- `.send(to, *, subject="", body="", html=None, in_reply_to=None, cc=None, bcc=None, attachments=None) -> dict` —
  send a DKIM-signed email from this inbox. Returns once the mail is **accepted for
  delivery**, not once it is delivered (see below). Pass `in_reply_to` (a `Message-ID`) to
  stay in a thread.
- `.message(message_id) -> Message` — fetch one message; the way to check what happened to a send.
- `.wait_until_sent(message_id, *, timeout=120, poll_interval=2.0) -> Message` — block until
  delivery finishes. Raises `SendFailedError` if it permanently failed, `SendTimeoutError`
  if it is still queued at the deadline.
- `.mark_read(message_id) -> dict` — mark a message read so later polls can skip it.
- `.burn() -> dict` — delete every message but keep the address.
- `.download_attachment(message_id, attachment_id) -> bytes` — fetch an attachment's bytes.
- `.delete() -> dict` — delete the inbox and all its messages.

> **Reads return received mail by default.** `direction="out"` returns mail you sent from
> this address, `"all"` returns both. This matters for agent-to-agent flows: without it,
> `send()` followed by `wait_for_message()` returns your own outgoing message.

### `Message`
`.otp`, `.subject`, `.sender`, `.text`, `.html`, `.to_address`, `.direction`, `.received_at`,
`.links`, `.attachments`, `.spam`, `.headers`, `.is_read`, `.message_id`, `.reply_to_address`, `.raw`.

- ⚠️ `.sender` is the **SMTP envelope sender** (MAIL FROM). On transactional mail that is
  usually a bounce address such as `bounces+abc@sendgrid.net`, so `send(to=msg.sender, …)`
  delivers your reply to a bounce processor. Use `.reply_to_address` (or just `.reply()`),
  which resolves `Reply-To` → `From` → envelope sender.

- `.links` — URLs found in the body (HTML hrefs first). For "click the verification link" flows.
- `.attachments` — metadata; `msg.attachments[0].download()` fetches the bytes.
- `.spam` — `{score, required, is_spam, rules, scanner}`, or `None` when never scanned
  (which is not the same as a score of 0). ⚠️ Spam scanning is currently **disabled** on
  mailflat.net, so today this is `None` for every message.
- `.headers` — raw headers, exactly as they arrived. Names are case-insensitive per
  RFC 5322 but this is a plain dict, so **do not index it**: the real key is `Message-ID`,
  and `headers["Message-Id"]` raises `KeyError`. Use `.header("message-id")` or `.message_id`.
- `.reply(body, *, html=None, subject=None)` — answer this message **in the same
  conversation**. Fills in the recipient, an `Re:` subject and the `In-Reply-To` /
  `References` headers Gmail and Outlook thread on. A hand-rolled `send()` starts a new
  conversation instead, which does not look like a reply.
- `.mark_read()` / `.delete()` — act on this message directly.

## Async

Everything above has an `asyncio` twin. Real async I/O, not a thread wrapper — an agent
watching a fleet of inboxes runs them on one event loop instead of holding a thread per
`wait_for_otp`:

```python
import asyncio
from mailflat.aio import AsyncMailFlat

async def main():
    async with AsyncMailFlat() as mf:
        inboxes = await asyncio.gather(*(mf.create(label=f"user-{i}") for i in range(20)))
        codes = await asyncio.gather(*(i.wait_for_otp(timeout=60) for i in inboxes))
        return codes

asyncio.run(main())
```

`AsyncMailFlat` mirrors `MailFlat` method for method, with the same keyword arguments and
the same rules — retries, `Retry-After`, error types and timeout wording are shared code,
not two copies. LangChain agents get `AsyncMailFlatToolkit`, which binds `coroutine=` on
every tool so `ainvoke` awaits instead of parking a thread.

## Attachments, cc and bcc

```python
inbox.send(
    "customer@example.com",
    subject="Your invoice",
    body="Attached.",
    cc=["billing@example.com"],
    bcc=["audit@example.com"],
    attachments=["/tmp/invoice.pdf"],          # a path — the SDK reads and encodes it
)
```

An attachment can be a file path, `{"filename": ..., "content": b"..."}`, or
`{"filename": ..., "content_b64": "..."}`. Size and count limits depend on your plan
(the free plan is deliberately small); going over raises with the limit spelled out
rather than silently dropping the file.

`bcc` recipients receive the mail but never appear in its headers — not even in their own
copy. `reply()` accepts all three too, so you can answer a thread with a file attached.

## Did it actually go out?

`send()` returns as soon as the mail is **accepted**; delivery runs on a queue, so nothing
is delivered yet when it returns:

```python
res = inbox.send("customer@example.com", subject="Your invoice",
                 attachments=["/tmp/invoice.pdf"])
inbox.wait_until_sent(res["message_id"], timeout=120)   # raises if it failed
```

For anything long-running, subscribe to the `message.delivered` / `message.failed`
webhooks instead of polling.

## Errors

All errors subclass `MailFlatError`: `AuthenticationError` (401), `MailFlatPermissionError` (403,
still exported as `PermissionError` for compatibility — the new name no longer shadows the built-in),
`NotFoundError` (404), `RateLimitError` (429, carries `.retry_after` when the server sent one),
`APIError` (other), `OTPTimeoutError`
(no OTP before timeout), `EncryptedInboxError` (the inbox is end-to-end encrypted, so the
server cannot read its contents — use a non-encrypted inbox for agent automation).

A message or attachment id that is not in an inbox you own raises `NotFoundError`. An inbox you
do not own always raises `MailFlatPermissionError`, whether or not it exists — the API will not
confirm other people's addresses. So the two exceptions answer different questions: "wrong id"
versus "wrong key".

## License

MIT
