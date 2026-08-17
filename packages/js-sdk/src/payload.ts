// Turns caller options into a request body: known names are translated camelCase →
// snake_case, and anything else is passed through UNCHANGED so the server can answer.
//
// Why pass unknown fields through instead of dropping them (B-077):
//   The client used to build bodies from a fixed allowlist. A field the SDK did not know
//   about simply vanished, and the request succeeded with 200. That turned a server-side
//   rejection into silence — `create({ public_key })` came back 200 with a plaintext
//   inbox, which is the exact false belief the API started rejecting with 422 in B-069.
//   The API fix never fired, because the field never reached the API.
//   Passing it through costs nothing when the field is valid and restores a real error
//   when it is not. The SDK is not the place to decide which fields exist.
//
// The second, quieter bug the allowlist caused: `retention_hours` (the snake_case name,
// the one the REST docs and every curl example use) was ignored, because only
// `retentionHours` was read. Options that come from a config file or JSON are normally
// written in the wire format — those callers got the plan default and no warning.
//
// Connected to:
//   - used by:    client.ts (create), inbox.ts (send/reply)
//   - depends on: nothing
//
// Key export: buildPayload(options, nameMap)

/** camelCase → snake_case for the fields each endpoint documents. */
export type NameMap = Record<string, string>;

export const CREATE_INBOX_FIELDS: NameMap = {
  prefix: "prefix",
  label: "label",
  subdomain: "subdomain",
  domain: "domain",
  retentionHours: "retention_hours",
};

export const SEND_FIELDS: NameMap = {
  subject: "subject",
  body: "body",
  html: "html",
  inReplyTo: "in_reply_to",
  cc: "cc",
  bcc: "bcc",
};

/**
 * Build a request body from caller options.
 *
 * - `undefined` and `null` values are left out entirely — "not specified" must not become
 *   an explicit null, which some fields treat differently from absence.
 * - Empty arrays are left out for the same reason: sending `cc: []` says "no copies" just
 *   as loudly as omitting it, and omitting keeps the body honest about what was asked for.
 * - Every other key is forwarded. Unknown ones reach the server verbatim and come back as
 *   a 422 that names them — silence is the one outcome this function will not produce.
 */
export function buildPayload(
  options: Record<string, any>,
  nameMap: NameMap,
  skip: string[] = [],
): Record<string, any> {
  const payload: Record<string, any> = {};
  for (const [key, value] of Object.entries(options ?? {})) {
    if (value == null || skip.includes(key)) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    payload[nameMap[key] ?? key] = value;
  }
  return payload;
}
