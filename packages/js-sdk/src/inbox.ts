// Inbox — high-level operations on a single MailFlat inbox.
//
// Returned by the MailFlat client: read messages, wait for OTP, send, mark read, burn, delete.
//
// Connected to:
//   - used by:    client.ts (creates it), user code
//   - depends on: types.ts, errors.ts, client.ts (type only)
//
// Key export: Inbox

import {
  EncryptedInboxError,
  MailFlatError,
  OTPTimeoutError,
  SendFailedError,
  SendTimeoutError,
} from "./errors";
import type { MailFlat } from "./client";
import { buildPayload, SEND_FIELDS } from "./payload";
import {
  DIRECTIONS,
  type Direction,
  type Message,
  type OutgoingAttachment,
  type ReadOptions,
  type ReplyOptions,
  type SendOptions,
  type WaitOptions,
  replySubject,
  toMessage,
} from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Bytes → standard base64, without Node's Buffer.
 *
 * Hand-rolled because this package must work in a browser too, where `Buffer` does not
 * exist; and `btoa(String.fromCharCode(...bytes))` blows the argument limit (and the
 * stack) on a file of any real size. Chunked so a 10 MB attachment encodes fine.
 */
function toBase64(bytes: Uint8Array): string {
  if (typeof Buffer !== "undefined") return Buffer.from(bytes).toString("base64");
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** One attachment → the wire shape the API expects. */
function encodeAttachment(att: OutgoingAttachment): Record<string, string> {
  const filename = (att.filename ?? "").trim();
  if (!filename) throw new MailFlatError("Each attachment needs a filename.");

  let contentBase64 = att.contentBase64;
  if (contentBase64 == null) {
    const raw = att.content;
    if (raw == null) {
      throw new MailFlatError(
        `Attachment "${filename}" needs content (bytes) or contentBase64 (a base64 string).`,
      );
    }
    contentBase64 = toBase64(raw instanceof Uint8Array ? raw : new Uint8Array(raw));
  }
  return {
    filename,
    content_type: att.contentType ?? "application/octet-stream",
    content_b64: contentBase64,
  };
}

function checkDirection(direction: Direction): Direction {
  if (!DIRECTIONS.includes(direction)) {
    throw new MailFlatError(
      `direction must be one of ${DIRECTIONS.join(", ")} — got "${direction}"`,
    );
  }
  return direction;
}

export class Inbox {
  readonly address: string;
  readonly name?: string;
  readonly apiKey?: string; // per-inbox key (mf_sk_...)
  readonly retentionHours?: number;
  readonly encrypted: boolean;
  readonly raw: Record<string, any>;

  // ES private field, NOT TypeScript `private`. The difference is what B-079 turned on:
  // `private` is erased at compile time, so the client object stayed an ordinary
  // enumerable property at runtime and `console.log(inbox)` / `JSON.stringify(inbox)`
  // printed the ACCOUNT key (mf_live_…) sitting inside it. A `#` field is invisible to
  // both. An agent logging an inbox to debug is normal; leaking the account key is not.
  #client: MailFlat;

  constructor(
    client: MailFlat,
    address: string,
    meta: Record<string, any> = {},
  ) {
    this.#client = client;
    this.address = address;
    this.name = meta.name;
    this.apiKey = meta.api_key;
    this.retentionHours = meta.retention_hours;
    this.encrypted = Boolean(meta.encrypted);
    this.raw = { address, ...meta };
  }

  /**
   * What `console.log(inbox)` prints. Node calls `util.inspect`, NOT `toString`, so a
   * class that only cleans up `toString` is clean in a place nobody looks.
   *
   * The per-inbox key (`mf_sk_…`) used to print here. The account key was fixed in B-079;
   * this one stayed, and an external round found it. It is a value the owner already
   * knows — but "already known to the owner" is not where the risk lives: the risk is CI
   * output, issue reports and screenshots, which is the same risk the account key had.
   * Python's `Inbox` already excluded it from `repr`, so this was also a parity break:
   * the same protection existed on one surface and not the other.
   *
   * `inbox.apiKey` still reads the value — hiding it from a printout is not the same as
   * taking it away from code that needs it.
   */
  [Symbol.for("nodejs.util.inspect.custom")]() {
    return `Inbox { address: ${JSON.stringify(this.address)}, `
      + `retentionHours: ${this.retentionHours}, encrypted: ${this.encrypted}, `
      + `apiKey: [hidden — read inbox.apiKey] }`;
  }

  /** Same rule for `JSON.stringify(inbox)`: the key does not travel in serialised output. */
  toJSON() {
    const { apiKey, raw, ...rest } = this as any;
    const safeRaw = { ...(raw || {}) };
    delete safeRaw.api_key;
    return { ...rest, raw: safeRaw };
  }

  // Wraps an API payload into a Message and attaches the delete/markRead/download helpers.
  // (header() is attached here too — see types.ts for why indexing headers is unsafe.)
  private _wrap(d: Record<string, any>): Message {
    const m = toMessage(d);
    m.delete = () => this.deleteMessage(m.id as number);
    m.markRead = () => this.markRead(m.id as number);
    m.reply = (body = "", opts: ReplyOptions = {}) => {
      // Reply-To / From, not the envelope sender: MAIL FROM is a bounce address on most
      // transactional mail, so replying there never reaches a person.
      const target = m.replyToAddress;
      if (!target) throw new MailFlatError("This message has no sender to reply to.");
      return this.send(target, {
        subject: opts.subject ?? replySubject(m.subject),
        body,
        html: opts.html,
        inReplyTo: m.messageId,
        // Everything send() accepts, reply() accepts too (K6): answering a mail with a
        // file attached is normal, and dropping back to send() to do it would lose the
        // threading headers that make it look like a reply.
        cc: opts.cc,
        bcc: opts.bcc,
        attachments: opts.attachments,
      });
    };
    for (const att of m.attachments) {
      att.download = () => {
        if (att.truncated) {
          throw new MailFlatError(`Attachment "${att.filename}" was too large to store`);
        }
        return this.downloadAttachment(m.id as number, att.id as number);
      };
    }
    return m;
  }

  /**
   * Return this inbox's messages, newest first.
   * Defaults to received mail; pass `{ direction: "out" }` or `"all"` for the rest.
   */
  async messages(opts: ReadOptions = {}): Promise<Message[]> {
    const direction = checkDirection(opts.direction ?? "in");
    const res = await this.#client._get(
      `/api/v1/inboxes/${this.address}/messages?direction=${direction}`,
    );
    return ((res.emails as any[]) || []).map((e) => this._wrap(e));
  }

  /**
   * Return the most recent message, or null when there is none.
   * Before 0.3.0 this also returned mail you had just sent, which made `send()` followed
   * by `waitForMessage()` match your own outgoing message.
   */
  async latest(opts: ReadOptions = {}): Promise<Message | null> {
    const direction = checkDirection(opts.direction ?? "in");
    const res = await this.#client._get(
      `/api/v1/inboxes/${this.address}/latest?direction=${direction}`,
    );
    return res.email ? this._wrap(res.email) : null;
  }

  /**
   * Poll until a message arrives and return it. Only received mail counts by default,
   * so an agent can send to a peer and wait for the reply without matching its own message.
   */
  async waitForMessage(opts: WaitOptions = {}): Promise<Message> {
    const timeout = opts.timeout ?? 30000;
    const pollInterval = opts.pollInterval ?? 1000;
    const direction = checkDirection(opts.direction ?? "in");
    const deadline = Date.now() + Math.max(0, timeout);
    for (;;) {
      const res = await this.#client._get(
        `/api/v1/inboxes/${this.address}/latest?direction=${direction}`,
      );
      if (res.encrypted) {
        throw new EncryptedInboxError(
          res.note ||
            "This inbox is end-to-end encrypted; use a non-encrypted inbox for agent automation.",
        );
      }
      if (res.email) return this._wrap(res.email);
      if (Date.now() >= deadline) {
        throw new OTPTimeoutError(`No message arrived for ${this.address} within ${timeout}ms`);
      }
      await sleep(pollInterval);
    }
  }

  /**
   * Poll until a one-time code arrives and return it.
   *
   * If mail did arrive but no code could be extracted from it, the timeout error says so and
   * quotes the newest message, so you can read `inbox.latest()` yourself instead of hunting
   * for a delivery problem that does not exist.
   */
  async waitForOtp(opts: WaitOptions = {}): Promise<string> {
    const timeout = opts.timeout ?? 30000;
    const pollInterval = opts.pollInterval ?? 1000;
    const deadline = Date.now() + Math.max(0, timeout);
    let seen: Record<string, any> | null = null; // newest incoming mail, code or not
    for (;;) {
      const res = await this.#client._get(`/api/v1/inboxes/${this.address}/latest?direction=in`);
      if (res.encrypted) {
        throw new EncryptedInboxError(
          res.note || "This inbox is end-to-end encrypted; OTP cannot be read via the API.",
        );
      }
      const otp = res.email?.otp_code;
      if (otp) return otp as string;
      if (res.email) seen = res.email;
      if (Date.now() >= deadline) {
        throw new OTPTimeoutError(this._otpTimeoutMessage(timeout, seen));
      }
      await sleep(pollInterval);
    }
  }

  /**
   * Distinguishes "nothing arrived" from "no code in what arrived". These are completely
   * different problems and used to produce the same sentence.
   */
  private _otpTimeoutMessage(timeout: number, seen: Record<string, any> | null): string {
    if (!seen) return `No message arrived for ${this.address} within ${timeout}ms`;
    const subject = (seen.subject || "(no subject)").trim();
    const snippet = String(seen.body_text || "").split(/\s+/).join(" ").slice(0, 120);
    return (
      `Mail arrived for ${this.address} but no OTP could be extracted from it within ` +
      `${timeout}ms. Newest message: ${JSON.stringify(subject)}` +
      (snippet ? ` — ${JSON.stringify(snippet)}` : "") +
      ". Read inbox.latest() and parse the code yourself, and please report the format so we " +
      "can support it."
    );
  }

  /**
   * Download one attachment's bytes. Metadata already ships with each message
   * (`msg.attachments`); prefer `msg.attachments[0].download()`.
   */
  async downloadAttachment(messageId: number, attachmentId: number): Promise<Uint8Array> {
    return this.#client._getBytes(
      `/api/v1/inboxes/${this.address}/messages/${messageId}/attachments/${attachmentId}`,
    );
  }

  /**
   * Send mail from this inbox's address (DKIM-signed, through MailFlat's own MTA).
   *
   * Resolves as soon as the mail is ACCEPTED for delivery — not once it is delivered.
   * Delivery runs on a queue, so the response carries `message_id` and `queued: true`;
   * use `waitUntilSent(messageId)` (or a `message.delivered` webhook) to learn how it
   * ended. Blocking here would put back the exact stall the queue removed.
   *
   * `attachments` takes bytes or base64 — the encoding happens here:
   *
   *     inbox.send(to, { attachments: [{ filename: "a.pdf", content: bytes }] });
   *
   * `bcc` recipients receive the mail but never appear in its headers — not even in their
   * own copy.
   *
   * Pass `inReplyTo` (a Message-ID) to keep the mail in an existing conversation; without it
   * the recipient's client starts a new thread. `message.reply()` fills this in for you.
   *
   * Not retried on gateway errors: a retried send can deliver the same mail twice.
   */
  async send(to: string, opts: SendOptions = {}): Promise<Record<string, any>> {
    // `attachments` is handled apart because its VALUE is transformed (bytes → base64);
    // everything else is name mapping, and unknown fields reach the server (see payload.ts).
    const payload: Record<string, any> = {
      to,
      subject: opts.subject ?? "",
      body: opts.body ?? "",
      ...buildPayload(opts, SEND_FIELDS, ["attachments"]),
    };
    if (opts.attachments?.length) payload.attachments = opts.attachments.map(encodeAttachment);
    return this.#client._post(`/api/v1/inboxes/${this.address}/send`, payload);
  }

  /**
   * Fetch one message by id — the way to ask "what happened to this send?".
   *
   * Without it the only way to check a sent mail's status was to pull the whole outbound
   * list and find the id by hand, which for an agent that sent 100 mails meant 100
   * messages per check.
   */
  async message(messageId: number): Promise<Message> {
    const res = await this.#client._get(
      `/api/v1/inboxes/${this.address}/messages/${messageId}`,
    );
    return this._wrap(res.email ?? {});
  }

  /**
   * Wait until a sent mail reaches a final delivery state.
   *
   * `send()` is asynchronous, so "did it actually go out?" has no synchronous answer. This
   * polls the message until the server reports one — the pull half of the contract (the
   * push half is the `message.delivered` / `message.failed` webhook; prefer that when you
   * can receive one).
   *
   * Resolves with the message on success. Throws `SendFailedError` when delivery
   * permanently failed and `SendTimeoutError` when it is still queued at the deadline. It
   * does NOT resolve quietly on failure: a helper called `waitUntilSent` that hands back a
   * failed mail as if nothing happened is how mail goes missing.
   */
  async waitUntilSent(
    messageId: number,
    opts: { timeout?: number; pollInterval?: number } = {},
  ): Promise<Message> {
    const timeout = opts.timeout ?? 120_000;
    const pollInterval = opts.pollInterval ?? 2_000;
    const deadline = Date.now() + timeout;
    for (;;) {
      const message = await this.message(messageId);
      const status = message.sendStatus;
      if (status === "sent" || status === "unsigned") return message;
      if (status === "failed") {
        throw new SendFailedError(
          message.sendError || "Delivery failed",
          messageId,
          status,
        );
      }
      if (Date.now() >= deadline) {
        // The last attempt's reason goes in the sentence when there has been one: a mail
        // held by greylisting and a mail nobody will ever accept used to read identically.
        // Timing out is still NOT a failure, so the wording keeps saying the queue works on.
        const lastError = message.sendError || undefined;
        throw new SendTimeoutError(
          `Message ${messageId} was still ${status ?? "queued"} after ${timeout}ms. ` +
            "It may still be delivered; the queue keeps retrying." +
            (lastError ? ` The last attempt reported: ${lastError}` : ""),
          messageId,
          status,
          lastError,
        );
      }
      await sleep(Math.min(pollInterval, Math.max(0, deadline - Date.now())));
    }
  }

  /** Mark one message as read, so the next poll can skip it. */
  async markRead(messageId: number): Promise<Record<string, any>> {
    return this.#client._post(`/api/v1/inboxes/${this.address}/messages/${messageId}/read`, {}, true);
  }

  /** Delete every message in this inbox and keep the address. */
  async burn(): Promise<Record<string, any>> {
    return this.#client._post(`/api/v1/inboxes/${this.address}/burn`, {}, true);
  }

  /** Delete this inbox and all of its messages. Cannot be undone. */
  async delete(): Promise<Record<string, any>> {
    return this.#client._delete(`/api/v1/inboxes/${this.address}`);
  }

  /** Delete a single message; the inbox stays. Backs `message.delete()`. */
  async deleteMessage(messageId: number): Promise<Record<string, any>> {
    return this.#client._delete(`/api/v1/inboxes/${this.address}/messages/${messageId}`);
  }
}
