// Strips secret fields from agent tool output (B-055).
//
// Why it exists: `createInbox` and `listInboxes` returned the backend payload as-is, and that
// payload carried a per-inbox `api_key` (`mf_sk_…`). Tool output goes into the MODEL's
// context, and from there into prompt logs, AI SDK telemetry, error reports and the model's
// own later answers. `listInboxes` leaked every inbox key on the account in a single call.
//
// The split is deliberate:
//   - SDK = code surface. `Inbox.apiKey` stays; code a human wrote may use it.
//   - Tool = model surface. Everything passing through here is redacted.
//
// Redaction is name-based AND value-based, and the two apply to different places:
//
//   - Name-based, everywhere. A field called `apiKey` never belongs in model context.
//   - Value-based, everywhere EXCEPT message content. A key can reach the model through a
//     field nobody thought to name (`account`, an error sentence, a nested payload), and the
//     `mf_live_` / `mf_sk_` prefixes are ours and unmistakable. Message BODIES are excluded
//     on purpose: a real email discussing a test key would be corrupted, and mangling the
//     mail an agent is waiting for is a worse failure than the one being prevented.
//
// ⚠️ The value half is a PORT of `redact.py`, added late (B-098). This file said in its own
// comment that value scanning had been rejected — true when written, and still sitting here
// after the Python side reversed the decision and gained it. The cross-surface parity matrix
// caught the drift by planting a key in a rejection sentence and finding it unmasked on the
// TypeScript surfaces only. Keep the two files in step; the rule they encode is one rule.
//
// Connected to:
//   - used by:    @mailflat/ai-sdk tools, user code
//   - depends on: nothing (pure)
//
// Key exports:
//   - `redactSecrets(value)` — deep copy with secret fields removed
//   - `SECRET_NAME_PARTS` — which field names count as secrets
//   - `SECRET_VALUE_PREFIXES` — which value prefixes are ours

export const SECRET_NAME_PARTS = ["apikey", "secret", "password"] as const;

/** Our own key prefixes. They cannot occur by accident in user data — we mint them. */
export const SECRET_VALUE_PREFIXES = ["mf_live_", "mf_sk_"] as const;

/** Where value scanning does NOT apply: the mail's own content. */
const CONTENT_FIELDS = ["body", "bodytext", "bodyhtml", "text", "html", "ciphertext", "raw"];

const normalise = (key: string): string => key.replace(/[_-]/g, "").toLowerCase();

const isSecret = (key: string): boolean => {
  const name = normalise(key);
  return SECRET_NAME_PARTS.some((part) => name.includes(part));
};

const isContentField = (key: string): boolean => CONTENT_FIELDS.includes(normalise(key));

const MASK = new RegExp(`(${SECRET_VALUE_PREFIXES.join("|")})[A-Za-z0-9_-]*`, "g");

const maskValue = <T>(value: T): T => {
  if (typeof value !== "string") return value;
  if (!SECRET_VALUE_PREFIXES.some((prefix) => value.includes(prefix))) return value;
  return value.replace(MASK, "$1[redacted]") as unknown as T;
};

/** A copy of `value` with secret fields removed. The input is never modified. */
export function redactSecrets<T>(value: T): T {
  if (Array.isArray(value)) return value.map((v) => redactSecrets(v)) as unknown as T;

  // `null` is also typeof "object" — and we do not want to clone classes like Date/Error.
  if (value !== null && typeof value === "object" && (value as object).constructor === Object) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (!isSecret(k)) out[k] = isContentField(k) ? v : redactSecrets(v);
    }
    return out as unknown as T;
  }

  return maskValue(value);
}
