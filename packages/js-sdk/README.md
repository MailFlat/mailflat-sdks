# @mailflat/sdk

Official TypeScript/JavaScript client for [MailFlat](https://mailflat.net): real email
inboxes for AI agents and test suites, with **one-line OTP retrieval**. The address is
permanent and stays until you delete it; only the messages inside expire, on a retention
window you choose. Zero dependencies, uses the platform `fetch` (Node 18+, modern browsers,
edge runtimes).

```bash
npm i @mailflat/sdk
```

## Quickstart

```ts
import { MailFlat } from "@mailflat/sdk";

const mailflat = new MailFlat({ apiKey: process.env.MAILFLAT_API_KEY });

// 1 · open an inbox
const inbox = await mailflat.create({ prefix: "signup-test", label: "checkout flow" });
console.log(inbox.address); // → signup-test@x7k2m.mailflat.net

// 2 · your app/browser submits the form using inbox.address ...

// 3 · grab the OTP (polls until it arrives or times out)
const otp = await inbox.waitForOtp({ timeout: 30000 });
console.log(otp); // → "123456"

// inbox auto-clears in 2h — no cleanup needed (or call inbox.delete())
```

## For AI agents

```ts
const inbox = await mailflat.create({ prefix: "deep-research" });
await browser.fill("#email", inbox.address);
await browser.click("Sign up");

const otp = await inbox.waitForOtp({ timeout: 30000 });
await browser.fill("#code", otp);
```

## API

### `new MailFlat(options)`
`{ apiKey?, baseUrl?, timeout?, maxRetries? }`. `apiKey` falls back to `process.env.MAILFLAT_API_KEY`.
Use `baseUrl` for self-hosted / BYOD deployments (default `https://mailflat.net`).

- `create(opts) => Promise<Inbox>` — open a new inbox. **`prefix` shapes the address, `label`
  does not**: `prefix: "signup-test"` gives `signup-test@…`, while `label` is only a dashboard
  name. Without `prefix` you get a random `agent-…` address. `createInbox(opts)` is an alias.
  `opts`: `{ prefix?, label?, subdomain?, domain?, retentionHours? }`.
- `list() => Promise<Inbox[]>` — inboxes opened with this key.
- `inbox(address) => Inbox` — attach to an existing address without a network call.

### `Inbox`
- `.address` — the email address.
- `.messages({ direction? }) => Promise<Message[]>` — messages, newest first.
- `.latest({ direction? }) => Promise<Message | null>` — most recent message.
- `.waitForOtp({ timeout?, pollInterval? }) => Promise<string>` — poll until an OTP arrives; returns the code. `timeout` is in **ms** (default 30000).
- `.waitForMessage({ timeout?, pollInterval?, direction? }) => Promise<Message>` — poll until any message arrives.
- `.send(to, { subject?, body?, html?, cc?, bcc?, attachments? }) => Promise<...>` — send a
  DKIM-signed email from this inbox. Resolves once the mail is **accepted for delivery**,
  not once it is delivered (see below).
- `.message(messageId) => Promise<Message>` — fetch one message; how you check what happened to a send.
- `.waitUntilSent(messageId, { timeout?, pollInterval? }) => Promise<Message>` — wait for
  delivery to finish. Throws `SendFailedError` if it permanently failed, `SendTimeoutError`
  if it is still queued at the deadline.
- `.markRead(messageId) => Promise<...>` — mark a message read so later polls can skip it.
- `.burn() => Promise<...>` — delete every message but keep the address.
- `.downloadAttachment(messageId, attachmentId) => Promise<Uint8Array>` — fetch attachment bytes.
- `.delete() => Promise<...>` — delete the inbox and all its messages.

> **Reads return received mail by default** (`direction: "in"`). Use `"out"` for mail you
> sent from this address, `"all"` for both. Without this, `send()` followed by
> `waitForMessage()` matches your own outgoing message — which breaks agent-to-agent flows.

### Attachments, cc and bcc

```ts
await inbox.send("customer@example.com", {
  subject: "Your invoice",
  body: "Attached.",
  cc: ["billing@example.com"],
  bcc: ["audit@example.com"],
  attachments: [{ filename: "invoice.pdf", content: bytes, contentType: "application/pdf" }],
});
```

Give an attachment `content` (a `Uint8Array`) or `contentBase64` — the encoding happens in
the SDK. ⚠️ Unlike the Python SDK there is no file *path* option: this package also runs in
a browser, where there is no filesystem. Read the file yourself and pass the bytes.

Size and count limits depend on your plan; going over throws with the limit spelled out
rather than silently dropping the file. `bcc` recipients receive the mail but never appear
in its headers — not even in their own copy. `reply()` accepts all three too.

### Did it actually go out?

`send()` resolves as soon as the mail is **accepted**; delivery runs on a queue:

```ts
const res = await inbox.send("customer@example.com", { subject: "Your invoice" });
await inbox.waitUntilSent(res.message_id, { timeout: 120_000 });   // throws if it failed
```

For anything long-running, subscribe to the `message.delivered` / `message.failed`
webhooks instead of polling.

### `Message`
`.otp`, `.subject`, `.sender`, `.text`, `.html`, `.toAddress`, `.direction`, `.receivedAt`,
`.links`, `.attachments`, `.spam`, `.headers`, `.isRead`, `.raw`.

- `.links` — URLs found in the body (HTML hrefs first). For "click the verification link" flows.
- `.attachments` — metadata; `msg.attachments[0].download()` fetches the bytes.
- `.spam` — `{score, required, is_spam, rules, scanner}`, or `null` when never scanned.
  ⚠️ Spam scanning is currently **disabled** on mailflat.net, so today this is `null` for
  every message.
- `.headers` — raw headers, exactly as they arrived. Names are case-insensitive per
  RFC 5322 but this is a plain object, so **do not index it**: the real key is `Message-ID`,
  and `headers["Message-Id"]` is `undefined`. Use `.header("message-id")` or `.messageId`.
- `.reply(body, { html?, subject? })` — answer this message **in the same conversation**
  (recipient, `Re:` subject and `In-Reply-To` / `References` are filled in). A hand-rolled
  `send()` starts a new thread instead.
- ⚠️ `.sender` is the **SMTP envelope sender** (MAIL FROM) — usually a bounce address on
  transactional mail. Use `.replyToAddress` (or `.reply()`), which resolves
  `Reply-To` → `From` → envelope sender.
- `.markRead()` / `.delete()` — act on this message directly.

## Errors

All errors extend `MailFlatError`: `AuthenticationError` (401), `PermissionError` (403),
`NotFoundError` (404), `RateLimitError` (429), `APIError` (other), `OTPTimeoutError`
(no OTP before timeout), `EncryptedInboxError` (the inbox is end-to-end encrypted, so the
server cannot read its contents — use a non-encrypted inbox for agent automation).

A message or attachment id that is not in an inbox you own throws `NotFoundError`. An inbox you
do not own always throws `PermissionError`, whether or not it exists — the API will not confirm
other people's addresses. So the two answer different questions: "wrong id" versus "wrong key".

## License

MIT
