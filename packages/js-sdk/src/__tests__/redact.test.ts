// redactSecrets birim testleri (B-055).
//
// Protection runs both ways: secrets MUST go and nothing the agent needs may go with them.
// Over-redaction is a bug too — dropping `otp_code` would quietly make the tools useless.
//
// Connected to:
//   - exercises: ../redact.ts
//
// Running it: cd packages/js-sdk && npm test

import { describe, expect, it } from "vitest";
import { SECRET_NAME_PARTS, redactSecrets } from "../redact";

describe("redactSecrets", () => {
  it("strips api_key at the top level", () => {
    expect(redactSecrets({ ok: true, address: "a@x.net", api_key: "mf_sk_secret" }))
      .toEqual({ ok: true, address: "a@x.net" });
  });

  it("strips inside arrays and nested objects", () => {
    const out = redactSecrets({
      ok: true,
      inboxes: [
        { address: "a@x.net", api_key: "mf_sk_1" },
        { address: "b@x.net", apiKey: "mf_sk_2", meta: { "API-KEY": "mf_sk_3" } },
      ],
    });
    expect(JSON.stringify(out)).not.toContain("mf_sk_");
    expect(out.inboxes.map((i: any) => i.address)).toEqual(["a@x.net", "b@x.net"]);
    expect(out.inboxes[1].meta).toEqual({});
  });

  it("matches the field name in any shape", () => {
    for (const name of ["api_key", "apiKey", "APIKEY", "API-KEY", "inbox_api_key"]) {
      expect(redactSecrets({ [name]: "x", keep: 1 })).toEqual({ keep: 1 });
    }
  });

  it("drops secret and password fields too", () => {
    expect(redactSecrets({ webhook_secret: "s", password: "p", address: "a@x.net" }))
      .toEqual({ address: "a@x.net" });
    expect([...SECRET_NAME_PARTS]).toEqual(["apikey", "secret", "password"]);
  });

  it("🔒 keeps everything the agent actually needs", () => {
    const msg = {
      id: 7, subject: "Verify", body_text: "your code is 123456", otp_code: "123456",
      sender: "no-reply@stripe.com", to_address: "a@x.net", is_read: false, direction: "in",
    };
    expect(redactSecrets(msg)).toEqual(msg);
  });

  it("leaves `token` alone on purpose", () => {
    expect(redactSecrets({ verify_token: "mf-verify-abc" })).toEqual({ verify_token: "mf-verify-abc" });
  });

  it("matches names, never values — a mail that talks about a key stays intact", () => {
    const msg = { subject: "Your key", body_text: "we rotated mf_sk_oldkey for you" };
    expect(redactSecrets(msg)).toEqual(msg);
  });

  it("does not mutate its input", () => {
    const original: any = { api_key: "mf_sk_1", address: "a@x.net" };
    redactSecrets(original);
    expect(original.api_key).toBe("mf_sk_1");
  });

  it("passes scalars, null and empty containers through", () => {
    for (const v of ["plain", 7, 1.5, true, null, undefined]) expect(redactSecrets(v)).toBe(v);
    expect(redactSecrets([])).toEqual([]);
    expect(redactSecrets({})).toEqual({});
  });

  it("leaves non-plain objects alone (Date, Error) instead of shredding them", () => {
    const d = new Date(0);
    expect(redactSecrets(d)).toBe(d);
    const e = new Error("boom");
    expect(redactSecrets(e)).toBe(e);
  });

  // ------------------------------------------------------- value scanning (B-098)
  // A name-based defence cannot see a name nobody thought of. The Python side gained value
  // scanning; this file kept a comment saying the idea had been rejected, and the two
  // surfaces drifted for three releases. The cross-surface parity matrix found it by
  // planting a key in a rejection sentence.

  it("🔒 masks our own key prefixes in a field nobody thought to name", () => {
    const out: any = redactSecrets({
      account: "mf_live_REAL", free: "mf_sk_REAL", nested: { whatever: "mf_live_REAL" },
    });
    const dumped = JSON.stringify(out);
    expect(dumped).not.toContain("mf_live_REAL");
    expect(dumped).not.toContain("mf_sk_REAL");
    expect(dumped).toContain("[redacted]");
  });

  it("🔒 masks a key inside an error sentence", () => {
    // The branch carrying text we do not author is the branch that was unredacted.
    const out: any = redactSecrets({
      error: "host would not accept mf_live_ABC123@x.test: 550 No such user",
    });
    expect(out.error).not.toContain("mf_live_ABC123");
    expect(out.error).toContain("550 No such user");
  });

  it("NEGATIVE CONTROL — message content is not touched", () => {
    // An email may legitimately discuss a test key. Mangling it breaks the very message the
    // agent is waiting for: a worse failure than the one being prevented. Same narrowing as
    // redact.py, and the reason value scanning is not applied everywhere.
    const body = "Your API key is mf_sk_example123 — paste it into the form.";
    for (const field of ["body", "body_text", "bodyHtml", "html", "text", "raw"]) {
      const out: any = redactSecrets({ [field]: body });
      expect(out[field]).toBe(body);
    }
  });
});
