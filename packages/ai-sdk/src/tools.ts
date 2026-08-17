// MailFlat tool suite for the Vercel AI SDK — 12 tools: createInbox, listInboxes,
// readMessages, waitForOtp, waitForMessage, sendEmail, reply, waitUntilSent, markRead,
// burnInbox, deleteInbox, deleteMessage.
//
// (This header used to list ten of them, silently dropping `reply`. A comment that
// miscounts the file it sits on top of is how a stale number survives for months, so the
// count is now compared with the real one by frontend/lib/docs-ai.test.mjs.)
//
// A thin layer over the @mailflat/sdk client; all HTTP and behaviour live there (DRY).
//
// Connected to:
//   - depends on: @mailflat/sdk (MailFlat client + redactSecrets), zod (schemas)
//   - used by:    index.ts, user code (generateText({ tools: { ...mailflatToolSuite(...) } }))
//
// ⚠️ sendEmail / reply take cc and bcc but NOT attachments. An address is a short string;
// file bytes would have to travel through the model's context, which costs tokens and is
// simply impossible for a multi-megabyte file. Attach files from the SDK
// (inbox.send(to, { attachments: [...] })) — the code surface, not the model surface.
//
// ⚠️ sendEmail returns "accepted", not "delivered" — delivery runs on a queue. waitUntilSent
// is how the model asks what happened. Its timeout branch must never read as failure: a
// model told "failed" when the mail is merely queued will send it again, and the recipient
// gets it twice. The payload wording is shared with the Python surfaces (agent_results.py)
// and locked by test.
//
// Key export: mailflatToolSuite(options) → dictionary of tool objects

import {
  EncryptedInboxError,
  MailFlat,
  MailFlatError,
  OTPTimeoutError,
  SendFailedError,
  SendTimeoutError,
  redactSecrets,
} from "@mailflat/sdk";
import { z } from "zod";

/**
 * Never attempted. Must match SEND_QUEUED_NOTE in agent_results.py.
 * Deliberately contains no form of the word "fail" — see the note on the Python constant.
 */
const SEND_QUEUED_NOTE =
  "Still queued when the timeout elapsed. The queue keeps retrying with backoff, so this " +
  "mail is still on its way. Do NOT send it again — a duplicate would reach the recipient " +
  "twice. Ask again later, or subscribe to the message.delivered webhook.";

/**
 * Attempted, not through yet. Must match SEND_RETRYING_NOTE in agent_results.py.
 * Same ban on the word "fail". `queued` and `retrying` are different answers to "should I
 * keep waiting?", and answering both with "Still queued" erased a distinction the queue
 * had gone to the trouble of making (B-099).
 */
const SEND_RETRYING_NOTE =
  "Delivery was attempted and has not got through yet. The queue has already scheduled " +
  "another attempt with backoff, so this mail is still on its way. Read `error` for what " +
  "the last attempt reported: a temporary rejection such as greylisting clears on its " +
  "own. Do NOT send it again — a duplicate would reach the recipient twice. Ask again " +
  "later, or subscribe to the message.delivered webhook.";

/** The queue gave up. Must match SEND_FAILED_NOTE in agent_results.py. */
const SEND_FAILED_NOTE =
  "Delivery permanently failed and the queue will not retry. Read `error` for the " +
  "reason (a rejection from the recipient's mail server, an unknown mailbox, and so on). " +
  "Fix the cause before sending again.";

export interface ToolSuiteOptions {
  /** MailFlat account API key (mf_live_...). Falls back to the MAILFLAT_API_KEY env var. */
  apiKey?: string;
  /** API root (defaults to https://mailflat.net). Override for self-hosted or test setups. */
  baseUrl?: string;
  /** Inject an existing MailFlat client (for tests or a shared client). */
  client?: MailFlat;
}

// Shape of a Vercel AI SDK tool. `parameters` (v3/v4) and `inputSchema` (v5) point at the
// same schema; generateText reads whichever key its version knows and ignores the other.
export interface MailFlatTool {
  description: string;
  parameters: z.ZodTypeAny;
  inputSchema: z.ZodTypeAny;
  execute: (args: any) => Promise<any>;
}

function defineTool(
  description: string,
  schema: z.ZodTypeAny,
  execute: (args: any) => Promise<any>,
): MailFlatTool {
  // TWO things happen here, both in ONE place so a tool added tomorrow cannot miss them.
  //
  // 1. `.strict()`. zod objects default to `strip`: a key the schema does not declare is
  //    silently REMOVED and the call proceeds. That is B-077 all over again, and on the
  //    worst possible surface — here the party inventing the field name is the MODEL, so
  //    this is the one place that must show it the server's "not accepted here" answer.
  //    We fixed the code SDK and left the model surfaces; an external round found them.
  //
  // 2. Validation inside `execute`. The AI SDK runtime validates before calling us, but a
  //    caller (or a different runtime) can invoke `execute` directly — as the reviewer did.
  //    Relying on someone else to validate is how the first version passed its own tests.
  const strict = schema instanceof z.ZodObject ? schema.strict() : schema;

  const checked = async (args: any) => {
    const parsed = strict.safeParse(args ?? {});
    if (!parsed.success) {
      // Returned, not thrown: model surfaces read errors as values (see `guarded`).
      const unknown = parsed.error.issues
        .filter((i) => i.code === "unrecognized_keys")
        .flatMap((i: any) => i.keys ?? []);
      const detail = unknown.length
        ? `Unknown field(s): ${unknown.join(", ")}. This tool does not accept them.`
        : parsed.error.issues.map((i) => `${i.path.join(".") || "input"}: ${i.message}`).join("; ");
      return { error: `Invalid arguments. ${detail}` };
    }
    return execute(parsed.data);
  };

  return { description, parameters: strict, inputSchema: strict, execute: checked };
}

// Turns a MailFlatError into a plain result the model can act on (instead of throwing) AND
// redacts the output. Redaction lives HERE on purpose: every tool passes through this point,
// including the one added tomorrow, so nobody can forget to call `redactSecrets`.
async function guarded<T>(fn: () => Promise<T>): Promise<T | { error: string }> {
  try {
    return redactSecrets(await fn());
  } catch (err) {
    if (err instanceof MailFlatError) return { error: err.message };
    throw err;
  }
}

/**
 * Returns ready-made tool objects to spread into the Vercel AI SDK's `generateText` /
 * `streamText` `tools` field.
 *
 * @example
 * const result = await generateText({
 *   model: openai("gpt-4o"),
 *   tools: { ...mailflatToolSuite({ apiKey: process.env.MAILFLAT_KEY }) },
 *   prompt: "Register on staging.io, get the OTP code and submit it.",
 * });
 */
export function mailflatToolSuite(options: ToolSuiteOptions = {}): Record<string, MailFlatTool> {
  const client =
    options.client ?? new MailFlat({ apiKey: options.apiKey, baseUrl: options.baseUrl });

  return {
    createInbox: defineTool(
      "Create a real, working disposable email inbox and return its address. Use this when you need an email to sign up for a service or receive a verification code. Messages auto-purge after the plan's retention window.",
      z.object({
        prefix: z
          .string()
          .optional()
          .describe("Optional left-hand prefix for the address (e.g. 'signup'). Random if omitted."),
        label: z
          .string()
          .optional()
          .describe(
            "Optional human label for the inbox. Paid plans only: on the free plan the "
            + "inbox is still created and the response lists `label` under `ignored_fields`.",
          ),
        retentionHours: z
          .number()
          .int()
          .positive()
          .optional()
          .describe("Optional retention in hours; capped at your plan's maximum if higher."),
      }),
      ({ prefix, label, retentionHours }) =>
        guarded(async () => {
          const inbox = await client.create({ prefix, label, retentionHours });
          return inbox.raw;
        }),
    ),

    listInboxes: defineTool(
      "List all inboxes currently available to this API key.",
      z.object({}),
      () =>
        guarded(async () => {
          const inboxes = await client.list();
          return { inboxes: inboxes.map((i) => i.raw) };
        }),
    ),

    readMessages: defineTool(
      "Read messages in the given inbox address (newest first). Returns received mail by default.",
      z.object({
        address: z.string().describe("The full inbox address to read, e.g. signup-8f3@mailflat.net."),
        direction: z
          .enum(["in", "out", "all"])
          .optional()
          .describe(
            "'in' (default) for received mail, 'out' for mail sent from this address, 'all' for both.",
          ),
      }),
      ({ address, direction }) =>
        guarded(async () => {
          const messages = await client.inbox(address).messages({ direction });
          return { emails: messages.map((m) => m.raw) };
        }),
    ),

    waitForOtp: defineTool(
      "Poll an inbox until a one-time verification code (OTP) arrives, then return it. Use right after submitting a sign-up form.",
      z.object({
        address: z.string().describe("The inbox address to poll for the OTP."),
        timeout: z
          .number()
          .int()
          // 0 is legal and means \"check once, do not wait\" — `.positive()` here was
          // declared but never enforced (execute did not validate), so the
          // constraint and the behaviour disagreed for as long as it existed.
          .nonnegative()
          .optional()
          .describe("Maximum milliseconds to wait before giving up (default 30000)."),
      }),
      async ({ address, timeout }) => {
        try {
          const otp = await client.inbox(address).waitForOtp({ timeout });
          // This tool handles its own error branches, so it does NOT pass through
          // `guarded()` → redaction is applied by hand. Today it returns only a string; if a
          // message body is added here tomorrow (as in the MCP server), the leak must not
          // silently come back.
          return redactSecrets({ otp });
        } catch (err) {
          if (err instanceof OTPTimeoutError) return { otp: null, error: "timeout" };
          if (err instanceof EncryptedInboxError)
            return { otp: null, encrypted: true, error: err.message };
          if (err instanceof MailFlatError) return { otp: null, error: err.message };
          throw err;
        }
      },
    ),

    waitForMessage: defineTool(
      "Poll an inbox until a NEW message arrives, then return it. Only received mail counts, so you can send to someone and wait for their reply without matching your own message.",
      z.object({
        address: z.string().describe("The inbox address to poll."),
        timeout: z
          .number()
          .int()
          // 0 is legal and means \"check once, do not wait\" — `.positive()` here was
          // declared but never enforced (execute did not validate), so the
          // constraint and the behaviour disagreed for as long as it existed.
          .nonnegative()
          .optional()
          .describe("Maximum milliseconds to wait before giving up (default 30000)."),
      }),
      async ({ address, timeout }) => {
        try {
          const msg = await client.inbox(address).waitForMessage({ timeout });
          // Same reason as waitForOtp: it handles its own error branches, so it does not
          // pass through guarded() and redaction is applied by hand.
          return redactSecrets({ email: msg.raw });
        } catch (err) {
          if (err instanceof OTPTimeoutError) return { email: null, error: "timeout" };
          if (err instanceof EncryptedInboxError)
            return { email: null, encrypted: true, error: err.message };
          if (err instanceof MailFlatError) return { email: null, error: err.message };
          throw err;
        }
      },
    ),

    sendEmail: defineTool(
      "Send an email FROM the given inbox address (DKIM-signed via MailFlat's mail servers). Use for replies or outbound automation. Returns once the mail is ACCEPTED for delivery, not once it is delivered.",
      z.object({
        address: z.string().describe("The inbox address to send from (must belong to this API key)."),
        to: z.string().describe("Recipient email address."),
        subject: z.string().optional().describe("Email subject line."),
        body: z.string().optional().describe("Plain-text body."),
        html: z.string().optional().describe("Optional HTML body."),
        cc: z.array(z.string()).optional().describe("Addresses to copy. They appear in the mail's headers."),
        bcc: z.array(z.string()).optional().describe("Addresses to copy privately. They never appear in the headers."),
      }),
      ({ address, to, subject, body, html, cc, bcc }) =>
        guarded(() => client.inbox(address).send(to, { subject, body, html, cc, bcc })),
    ),

    reply: defineTool(
      "Reply to a message so it stays in the SAME conversation. Prefer this over sendEmail when answering: it fills in the recipient, an 'Re:' subject and the threading headers. A plain sendEmail starts a new conversation in the recipient's client, which does not look like a reply.",
      z.object({
        address: z.string().describe("The inbox address that holds the message."),
        messageId: z.number().describe("The id of the message to answer."),
        body: z.string().optional().describe("Plain-text reply body."),
        html: z.string().optional().describe("Optional HTML body."),
        cc: z.array(z.string()).optional().describe("Addresses to copy. They appear in the mail's headers."),
        bcc: z.array(z.string()).optional().describe("Addresses to copy privately. They never appear in the headers."),
      }),
      ({ address, messageId, body, html, cc, bcc }) =>
        guarded(async () => {
          const inbox = client.inbox(address);
          const messages = await inbox.messages({ direction: "all" });
          const target = messages.find((m) => m.id === messageId);
          if (!target) return { error: `Message ${messageId} not found in ${address}` };
          return target.reply!(body ?? "", { html, cc, bcc });
        }),
    ),

    waitUntilSent: defineTool(
      "Find out whether a mail you sent was actually delivered. sendEmail only means 'accepted for delivery'; the mail goes out later on a queue. Pass the messageId sendEmail returned. Returns delivered:true once it went out. If it is still queued when the timeout elapses you get timedOut:true with delivered:false — that is NOT a failure and you must NOT send the mail again; the queue is still retrying. status tells you whether it has been attempted yet (queued) or attempted and rescheduled (retrying), and error carries what the last attempt reported. Permanent failure comes back the same way, as status:failed with the reason in error — read note before you resend anything.",
      z.object({
        address: z.string().describe("The inbox address the mail was sent from."),
        messageId: z.number().describe("The id sendEmail returned for the mail."),
        timeout: z
          .number()
          .int()
          // 0 is legal and means \"check once, do not wait\" — `.positive()` here was
          // declared but never enforced (execute did not validate), so the
          // constraint and the behaviour disagreed for as long as it existed.
          .nonnegative()
          .optional()
          .describe("Maximum milliseconds to wait for a final state (default 60000)."),
      }),
      async ({ address, messageId, timeout }) => {
        try {
          // `?? 60_000` on purpose: the SDK's own default is 120 s, which is a fine wait for
          // a script but a long stall for an agent turn. Passing `timeout` straight through
          // would have made the documented default a lie.
          const msg = await client.inbox(address).waitUntilSent(messageId, {
            timeout: timeout ?? 60_000,
          });
          // Handles its own error branches, so it does NOT pass through guarded() →
          // redaction is applied by hand (same reason as waitForOtp/waitForMessage).
          return redactSecrets({
            status: msg.sendStatus,
            delivered: true,
            timedOut: false,
            message: msg.raw,
            note: "",
            error: null,
          });
        } catch (err) {
          // Order matters: SendTimeoutError and SendFailedError both extend MailFlatError,
          // so a leading `instanceof MailFlatError` branch would swallow both and answer
          // every outcome with a bare {error} — the exact flattening this tool exists to
          // prevent.
          //
          // All three branches go through redactSecrets. Only the success branch used to,
          // and the unredacted branches were the ones carrying text we do not control (a
          // remote MTA's rejection sentence) — the asymmetry pointed the wrong way, and
          // redact.ts names "an error sentence" among the cases value scanning exists for
          // (B-098).
          if (err instanceof SendTimeoutError)
            return redactSecrets({
              status: err.status ?? "queued",
              delivered: false,
              timedOut: true,
              message: null,
              note: err.status === "retrying" ? SEND_RETRYING_NOTE : SEND_QUEUED_NOTE,
              error: err.lastError ?? null,
            });
          if (err instanceof SendFailedError)
            return redactSecrets({
              status: err.status ?? "failed",
              delivered: false,
              timedOut: false,
              message: null,
              note: SEND_FAILED_NOTE,
              error: err.message,
            });
          if (err instanceof MailFlatError) return { error: err.message };
          throw err;
        }
      },
    ),

    markRead: defineTool(
      "Mark one message as read so later polls can skip it.",
      z.object({
        address: z.string().describe("The inbox address that holds the message."),
        messageId: z.number().describe("The id of the message to mark as read."),
      }),
      ({ address, messageId }) => guarded(() => client.inbox(address).markRead(messageId)),
    ),

    burnInbox: defineTool(
      "Delete every message in an inbox but KEEP the address. Use between scenarios so the address stays registered wherever you already used it.",
      z.object({
        address: z.string().describe("The inbox address to empty."),
      }),
      ({ address }) => guarded(() => client.inbox(address).burn()),
    ),

    deleteInbox: defineTool(
      "Delete an inbox and all its messages by address. Irreversible.",
      z.object({
        address: z.string().describe("The inbox address to delete."),
      }),
      ({ address }) => guarded(() => client.inbox(address).delete()),
    ),

    deleteMessage: defineTool(
      "Delete a single message in an inbox by its id (the inbox itself stays).",
      z.object({
        address: z.string().describe("The inbox address that holds the message."),
        messageId: z.number().describe("The id of the message to delete."),
      }),
      ({ address, messageId }) => guarded(() => client.inbox(address).deleteMessage(messageId)),
    ),
  };
}
