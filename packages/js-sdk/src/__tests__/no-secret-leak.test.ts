// The account key must not fall out of an object anyone would reasonably print (B-079).
//
// How it leaked: `Inbox` held the client as a TypeScript `private` field. That keyword is
// erased at compile time — at runtime it is an ordinary enumerable property — so
// `console.log(inbox)` and `JSON.stringify(inbox)` walked into the client and printed
// `apiKey: "mf_live_…"`. Printing an object to see what you got back is the most ordinary
// debugging move there is; CI logs, issue reports and screenshots are where it ends up.
//
// These tests assert the two runtime surfaces separately, because they fail differently:
// JSON.stringify follows enumerable own properties, util.inspect follows more than that.
//
// Connected to:
//   - tests: inbox.ts (#client), client.ts (non-enumerable apiKey)

import { inspect } from "node:util";
import { describe, expect, it } from "vitest";

import { MailFlat } from "../client";
import { Inbox } from "../inbox";

const SECRET = "mf_live_thisisthesecretaccountkey";

function fixture() {
  const mf = new MailFlat({ apiKey: SECRET, fetch: (async () => new Response("{}")) as any });
  const inbox = new Inbox(mf, "agent@x7k2m.mailflat.net", {
    api_key: "mf_sk_perinboxkey",
    retention_hours: 2,
  });
  return { mf, inbox };
}

describe("the account key never falls out of a printed object", () => {
  it("JSON.stringify(inbox) does not contain it", () => {
    const { inbox } = fixture();
    expect(JSON.stringify(inbox)).not.toContain(SECRET);
  });

  it("util.inspect(inbox) does not contain it — this is what console.log prints", () => {
    const { inbox } = fixture();
    expect(inspect(inbox, { depth: 10 })).not.toContain(SECRET);
  });

  it("a deep walk of the inbox's own properties never reaches it", () => {
    // Broader than the two calls above: whatever a caller uses to serialise the object,
    // the key must not be reachable by walking enumerable properties.
    const { inbox } = fixture();
    const seen = new Set<any>();
    const walk = (value: any): boolean => {
      if (value === SECRET) return true;
      if (value == null || typeof value !== "object" || seen.has(value)) return false;
      seen.add(value);
      return Object.values(value).some(walk);
    };
    expect(walk(inbox)).toBe(false);
  });

  it("the client itself does not print its key either", () => {
    // Second layer: `new MailFlat(...)` is logged directly at least as often as an inbox.
    const { mf } = fixture();
    expect(JSON.stringify(mf)).not.toContain(SECRET);
    expect(inspect(mf, { depth: 10 })).not.toContain(SECRET);
  });

  it("the PER-INBOX key does not print either (round 7 #4)", () => {
    // B-079 looked closed: the ACCOUNT key (mf_live_) no longer leaked. The per-inbox key
    // (mf_sk_) sitting on the same object still printed, because `console.log` calls
    // `util.inspect`, not `toString`, and the fix targeted the path nobody uses. Python's
    // Inbox already kept it out of `repr`, so this was also a parity break: the same
    // protection on one surface and not the other. The risk is not "the owner knows it
    // already" — it is CI output, issue reports and screenshots.
    const { inbox } = fixture();
    const printed = inspect(inbox, { depth: 10 }) + JSON.stringify(inbox);
    expect(printed).not.toContain("mf_sk_perinboxkey");
  });

  it("but the key is still readable on purpose, and the inbox still works", () => {
    // Negative control. Hiding the key by BREAKING it would pass every test above and
    // ship a client that cannot authenticate.
    const { mf, inbox } = fixture();
    expect(mf.apiKey).toBe(SECRET);
    expect(inbox.address).toBe("agent@x7k2m.mailflat.net");
    expect(inbox.apiKey).toBe("mf_sk_perinboxkey"); // per-inbox key is meant to be visible
  });
});
