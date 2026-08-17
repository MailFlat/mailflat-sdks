// MailFlat SDK types — Message, Attachment and option interfaces.
//
// Connected to:
//   - used by:    client.ts, inbox.ts, index.ts
//
// Key export: Message, Attachment, OutgoingAttachment, CreateInboxOptions, WaitOptions,
//             SendOptions, ReplyOptions, ReadOptions

/** Which messages to read: received (default), sent, or both. */
export type Direction = "in" | "out" | "all";

export const DIRECTIONS: Direction[] = ["in", "out", "all"];

/** Attachment metadata. Bytes are fetched on demand with `inbox.downloadAttachment()`. */
export interface Attachment {
  id?: number;
  filename?: string;
  contentType?: string;
  sizeBytes?: number;
  isEncrypted?: boolean;
  /** true → the file exceeded the storage cap and its bytes were never stored. */
  truncated?: boolean;
  raw: Record<string, any>;
  /** Download this attachment's bytes. Attached when the message comes from an Inbox. */
  download?: () => Promise<Uint8Array>;
}

/** Spam report. `null` means NOT SCANNED, which is not the same as a score of 0. */
export interface SpamReport {
  score?: number;
  required?: number | null;
  is_spam?: boolean | null;
  rules?: any[];
  scanner?: string | null;
}

export interface Message {
  id?: number;
  sender?: string;
  subject?: string;
  text?: string; // body_text
  html?: string; // body_html
  otp?: string; // otp_code (when the server extracted one)
  tag?: string;
  toAddress?: string; // to_address
  isEncrypted?: boolean;
  direction?: string; // "in" | "out"
  isRead?: boolean;
  sendStatus?: string;
  sendError?: string;
  receivedAt?: string;
  /** URLs found in the body (HTML hrefs first, then plain text). */
  links: string[];
  attachments: Attachment[];
  /**
   * Spam report, or null when the message was never scanned.
   * NOTE: spam scanning is currently disabled on mailflat.net, so today this is null for
   * every message. The field is wired end to end and fills in once scanning is re-enabled.
   */
  spam?: SpamReport | null;
  /** Raw headers — Message-ID lives here (threading, dedup). Null on encrypted inboxes. */
  headers?: Record<string, any> | null;
  raw: Record<string, any>; // the full backend payload
  /**
   * Look up a header case-insensitively, e.g. `msg.header("message-id")`.
   * Header names are case-insensitive per RFC 5322, but `headers` is a plain object whose
   * keys come straight off the wire — the real key is `Message-ID`, so
   * `headers["Message-Id"]` is undefined. Use this instead of indexing.
   */
  header: (name: string) => any;
  /** This message's `Message-ID`, or undefined when headers were not stored. */
  messageId?: string;
  /**
   * Where a reply should actually go: `Reply-To`, else `From`, else the envelope sender.
   * `.sender` is the SMTP envelope sender (MAIL FROM); for transactional mail that is usually
   * a bounce address, so replying to it reaches a bounce processor rather than a person.
   */
  replyToAddress?: string;
  /** Delete this message (the inbox stays). Attached when it comes from an Inbox. */
  delete?: () => Promise<Record<string, any>>;
  /** Mark this message read. Attached when it comes from an Inbox. */
  markRead?: () => Promise<Record<string, any>>;
  /**
   * Reply to this message, keeping it in the same conversation.
   * Fills in the recipient, a non-doubled `Re:` subject and the `In-Reply-To` /
   * `References` headers Gmail and Outlook use to thread.
   */
  reply?: (body?: string, opts?: ReplyOptions) => Promise<Record<string, any>>;
}

export interface CreateInboxOptions {
  prefix?: string;
  /** Display name. Paid plans only — on Free the response reports it in `ignored_fields`. */
  label?: string;
  subdomain?: string; // mailflat.net subdomain (random when omitted)
  domain?: string; // one of your verified BYOD domains
  retentionHours?: number;
}

export interface ReadOptions {
  /** "in" (default) = received mail only, "out" = mail you sent, "all" = both. */
  direction?: Direction;
}

export interface WaitOptions {
  timeout?: number; // ms (default 30000)
  pollInterval?: number; // ms (default 1000)
  /** Which messages count as an arrival. Defaults to "in". */
  direction?: Direction;
}

/**
 * One outgoing attachment.
 *
 * Give it `content` (bytes) or `contentBase64` (already encoded) — the SDK does the base64
 * so an agent never has to.
 *
 * ⚠️ No file *paths* here, unlike the Python SDK: this package also runs in a browser,
 * where there is no filesystem. Read the file yourself and pass the bytes.
 */
export interface OutgoingAttachment {
  filename: string;
  /** Raw bytes. */
  content?: Uint8Array | ArrayBuffer;
  /** Standard base64 of the file bytes (no `data:` prefix). */
  contentBase64?: string;
  /** Defaults to application/octet-stream when omitted. */
  contentType?: string;
}

export interface SendOptions {
  subject?: string;
  body?: string;
  html?: string;
  /** Message-ID of the mail being answered — keeps the reply in the same thread. */
  inReplyTo?: string;
  /** Copied recipients. They appear in the mail's headers. */
  cc?: string[];
  /** Blind-copied recipients. They never appear in the headers — not even in their copy. */
  bcc?: string[];
  /** Files to attach. Size and count limits depend on the plan. */
  attachments?: OutgoingAttachment[];
}

export interface ReplyOptions {
  html?: string;
  /** Override the auto-generated `Re: …` subject. */
  subject?: string;
  /** Copied recipients. They appear in the mail's headers. */
  cc?: string[];
  /** Blind-copied recipients. They never appear in the headers. */
  bcc?: string[];
  /** Files to attach — answering with a file is the normal case, not an edge case. */
  attachments?: OutgoingAttachment[];
}

/** `Re:` prefix without doubling it up (clients also thread on the subject). */
/** The server's `subject` column limit. A reply built from a long inbound subject must fit. */
export const MAX_SUBJECT_CHARS = 200;

/**
 * A reply subject the SDK BUILDS ITSELF must fit the server's limit.
 *
 * A stored message can legitimately have a 198-character subject — inbound mail is not
 * bound by our API limit — and `"Re: " + subject` then exceeds 200, so `reply()` refused to
 * answer a message the service itself had accepted, with no way out offered. Truncating a
 * value WE derived is not the same as truncating the caller's input: an explicit
 * `subject:` that is too long is still rejected, because the caller can fix that.
 *
 * The same reasoning covers CONTROL CHARACTERS, and the rule had only been applied along
 * the length axis. A stranger can send a subject whose RFC 2047 encoded-word decodes to a
 * real CRLF; the server stored it, this helper prefixed `Re: `, and the server's header
 * guard then rejected it \u2014 so that message could never be answered again, permanently.
 * Same test: can the caller fix the value? They did not write it, so no.
 */
export function replySubject(subject?: string | null): string {
  const text = oneLine(subject || "").trim();
  if (!text) return "Re:";
  const reply = text.toLowerCase().startsWith("re:") ? text : `Re: ${text}`;
  return reply.length <= MAX_SUBJECT_CHARS
    ? reply
    : reply.slice(0, MAX_SUBJECT_CHARS - 1) + "\u2026";
}

/**
 * Control characters out of a value about to become a mail header.
 *
 * A space, not deletion: `"a\r\nb"` becoming `"ab"` would silently join two words. Tab
 * survives \u2014 it is horizontal space and does not split a header line.
 */
function oneLine(text: string): string {
  // \x09 (tab) is deliberately outside the ranges; \x0A (LF) is deliberately inside.
  // A RUN collapses to one space — CRLF is two characters and "a\r\nb" should read
  // "a b", not "a  b".
  // eslint-disable-next-line no-control-regex
  return text.replace(/[\x00-\x08\x0A-\x1F\x7F]+/g, " ");
}

/** Convert an /api/v1 attachment payload (snake_case) to `Attachment` (camelCase). */
export function toAttachment(d: Record<string, any>): Attachment {
  return {
    id: d.id,
    filename: d.filename,
    contentType: d.content_type,
    sizeBytes: d.size_bytes,
    isEncrypted: Boolean(d.is_encrypted),
    truncated: Boolean(d.truncated),
    raw: d,
  };
}

/** Pull a bare address out of `Name <addr@host>` / `addr@host`. */
function parseAddress(value: unknown): string | undefined {
  const first = Array.isArray(value) ? value[0] : value;
  if (!first) return undefined;
  const text = String(first);
  const angled = text.match(/<([^>]+)>/);
  const addr = (angled ? angled[1] : text).trim();
  return addr.includes("@") ? addr : undefined;
}

/** Convert an /api/v1 email payload (snake_case) to `Message` (camelCase). */
export function toMessage(d: Record<string, any>): Message {
  const headers = (d.headers as Record<string, any> | null) ?? null;
  const header = (name: string) => {
    if (!headers) return undefined;
    const target = name.replace(/_/g, "-").toLowerCase();
    const hit = Object.keys(headers).find((k) => k.toLowerCase() === target);
    return hit === undefined ? undefined : headers[hit];
  };
  return {
    id: d.id,
    sender: d.sender,
    subject: d.subject,
    text: d.body_text,
    html: d.body_html,
    otp: d.otp_code,
    tag: d.tag,
    toAddress: d.to_address,
    isEncrypted: Boolean(d.is_encrypted),
    direction: d.direction,
    isRead: Boolean(d.is_read),
    sendStatus: d.send_status,
    sendError: d.send_error,
    receivedAt: d.received_at,
    links: (d.links as string[]) ?? [],
    attachments: ((d.attachments as any[]) ?? []).map(toAttachment),
    spam: d.spam ?? null,
    headers,
    header,
    messageId: header("message-id"),
    replyToAddress:
      parseAddress(header("reply-to")) ?? parseAddress(header("from")) ?? d.sender,
    raw: d,
  };
}
