// @mailflat/ai-sdk tests — a real MailFlat client with an injected mock fetch verifies that
// every tool's execute() makes the right /api/v1 call and returns the result.
//
// Covers: the tool set, parameters/inputSchema symmetry for each, execute behaviour
// (create/list/read/send/delete + waitForOtp success/timeout/encrypted) and the error guard.

import { MailFlat } from "@mailflat/sdk";
import { describe, expect, it, vi } from "vitest";
import { mailflatToolSuite } from "../index";

const ADDR = "signup-test@x7k2m.mailflat.net";

function jsonResponse(status: number, body: any): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Build a client that uses the given handler as its fetch, then a tool suite on top.
function makeSuite(handler: (url: string, init: RequestInit) => Response) {
  const fetchMock = vi.fn(async (url: any, init: any) => handler(String(url), init));
  const client = new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any, maxRetries: 0 });
  return { suite: mailflatToolSuite({ client }), fetchMock };
}

describe("suite shape", () => {
  it("exposes the 19 expected tools with matching parameters/inputSchema", () => {
    const { suite } = makeSuite(() => jsonResponse(200, {}));
    expect(Object.keys(suite).sort()).toEqual(
      [
        "createInbox",
        "listInboxes",
        "readMessages",
        "waitForOtp",
        "waitForMessage",
        "sendEmail",
        "reply",
        "waitUntilSent",
        "markRead",
        "burnInbox",
        "deleteInbox",
        "deleteMessage",
        "listCalendarEvents",
        "rsvpToInvite",
        "createCalendarEvent",
        "updateCalendarEvent",
        "cancelCalendarEvent",
        "getCalendarFeed",
        "rotateCalendarFeed",
      ].sort(),
    );
    for (const tool of Object.values(suite)) {
      expect(tool.description).toBeTruthy();
      // v3/v4 (parameters) and v5 (inputSchema) must point at the same schema.
      expect(tool.parameters).toBe(tool.inputSchema);
      expect(typeof tool.execute).toBe("function");
    }
  });
});

describe("createInbox", () => {
  it("POSTs /api/v1/inboxes and returns inbox.raw", async () => {
    const { suite } = makeSuite((url, init) => {
      expect(init.method).toBe("POST");
      expect(url).toBe("https://mailflat.net/api/v1/inboxes");
      expect(JSON.parse(init.body as string)).toEqual({ label: "deep-research" });
      return jsonResponse(200, { ok: true, address: ADDR, name: "deep-research", retention_hours: 2 });
    });
    const res = await suite.createInbox.execute({ label: "deep-research" });
    expect(res.address).toBe(ADDR);
    expect(res.name).toBe("deep-research");
  });

  it("sends `domain` and `subdomain` to the wire instead of stripping them", async () => {
    // zod's default is `strip`: a field missing from the schema is dropped WITHOUT an error,
    // so the inbox would quietly land on mailflat.net. Only a real call proves it travels.
    const bodies: any[] = [];
    const { suite } = makeSuite((_url, init) => {
      const body = JSON.parse(init.body as string);
      bodies.push(body);
      return jsonResponse(200, { ok: true, address: `${body.prefix}@acme.com` });
    });
    const res = await suite.createInbox.execute({ prefix: "bot", domain: "acme.com" });
    expect(res.address).toBe("bot@acme.com");
    expect(bodies[0]).toEqual({ prefix: "bot", domain: "acme.com" });

    await suite.createInbox.execute({ prefix: "bot", subdomain: "qa" });
    expect(bodies[1]).toEqual({ prefix: "bot", subdomain: "qa" });
  });

  it("reports an unknown domain instead of falling back to mailflat.net", async () => {
    const { suite, fetchMock } = makeSuite(() =>
      jsonResponse(400, { detail: "Domain 'nope.com' not found for this account" }),
    );
    const res = await suite.createInbox.execute({ prefix: "bot", domain: "nope.com" });
    expect(res.error).toContain("not found for this account");
    expect(res.address).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1); // no second, domain-less attempt
  });
});

describe("listInboxes", () => {
  it("GETs /api/v1/inboxes and returns { inboxes }", async () => {
    const { suite } = makeSuite((url, init) => {
      expect(init.method).toBe("GET");
      expect(url).toBe("https://mailflat.net/api/v1/inboxes");
      return jsonResponse(200, { ok: true, inboxes: [{ address: ADDR }] });
    });
    const res = await suite.listInboxes.execute({});
    expect(res.inboxes).toHaveLength(1);
    expect(res.inboxes[0].address).toBe(ADDR);
  });
});

describe("readMessages", () => {
  it("GETs messages and returns { emails }", async () => {
    const { suite } = makeSuite((url) => {
      // The default is incoming mail only, so the URL carries a direction filter.
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/messages?direction=in`);
      return jsonResponse(200, { ok: true, emails: [{ id: 1, subject: "Hi", otp_code: "123456" }] });
    });
    const res = await suite.readMessages.execute({ address: ADDR });
    expect(res.emails).toHaveLength(1);
    expect(res.emails[0].subject).toBe("Hi");
  });
});

describe("waitForOtp", () => {
  it("returns the OTP when it arrives", async () => {
    const { suite } = makeSuite((url) => {
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/latest?direction=in`);
      return jsonResponse(200, { email: { otp_code: "987654" } });
    });
    const res = await suite.waitForOtp.execute({ address: ADDR, timeout: 5000 });
    expect(res.otp).toBe("987654");
  });

  it("returns { error: 'timeout' } when nothing arrives", async () => {
    const { suite } = makeSuite(() => jsonResponse(200, { email: null }));
    const res = await suite.waitForOtp.execute({ address: ADDR, timeout: 0 });
    expect(res.otp).toBeNull();
    expect(res.error).toBe("timeout");
  });

  it("flags encrypted inboxes", async () => {
    const { suite } = makeSuite(() => jsonResponse(200, { encrypted: true, note: "E2E inbox" }));
    const res = await suite.waitForOtp.execute({ address: ADDR, timeout: 5000 });
    expect(res.otp).toBeNull();
    expect(res.encrypted).toBe(true);
  });
});

describe("sendEmail", () => {
  it("POSTs /send with the payload", async () => {
    const { suite } = makeSuite((url, init) => {
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/send`);
      expect(JSON.parse(init.body as string)).toEqual({ to: "x@y.com", subject: "Hi", body: "Yo" });
      return jsonResponse(200, { ok: true, status: "sent" });
    });
    const res = await suite.sendEmail.execute({ address: ADDR, to: "x@y.com", subject: "Hi", body: "Yo" });
    expect(res.status).toBe("sent");
  });

  it("passes cc and bcc through under the wire names", async () => {
    let body: any;
    const { suite } = makeSuite((_url, init) => {
      body = JSON.parse(init.body as string);
      return jsonResponse(202, { ok: true, queued: true, message_id: 3 });
    });
    await suite.sendEmail.execute({
      address: ADDR,
      to: "x@y.com",
      cc: ["w@y.com"],
      bcc: ["s@y.com"],
    });
    expect(body.cc).toEqual(["w@y.com"]);
    expect(body.bcc).toEqual(["s@y.com"]);
  });

  it("treats 202 Accepted as success", async () => {
    // The endpoint answers "accepted for delivery", not "delivered". A suite that
    // surfaced 202 as an error would break every send the model makes.
    const { suite } = makeSuite(() => jsonResponse(202, { ok: true, queued: true }));
    await expect(
      suite.sendEmail.execute({ address: ADDR, to: "x@y.com" }),
    ).resolves.toMatchObject({ queued: true });
  });
});

describe("model surface", () => {
  it("exposes cc/bcc but never attachments", () => {
    // 🔒 K7: file bytes must not cross the MODEL surface. An address is a short string a
    // model can reasonably choose; an attachment is bytes that would travel through the
    // model's context — costly, and plainly impossible for a multi-megabyte file. Files
    // are attached from the SDK (inbox.send(to, { attachments })), which is code.
    const { suite } = makeSuite(() => jsonResponse(200, {}));
    for (const name of ["sendEmail", "reply"] as const) {
      const shape = (suite[name].parameters as any).shape;
      expect(Object.keys(shape)).not.toContain("attachments");
      expect(Object.keys(shape)).toEqual(expect.arrayContaining(["cc", "bcc"]));
    }
  });
});

describe("deleteInbox", () => {
  it("DELETEs the inbox", async () => {
    const { suite } = makeSuite((url, init) => {
      expect(init.method).toBe("DELETE");
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}`);
      return jsonResponse(200, { ok: true });
    });
    const res = await suite.deleteInbox.execute({ address: ADDR });
    expect(res.ok).toBe(true);
  });
});

describe("error guard", () => {
  it("returns { error } instead of throwing on 401", async () => {
    const { suite } = makeSuite(() => jsonResponse(401, { detail: "Invalid API key" }));
    const res = await suite.createInbox.execute({ label: "x" });
    expect(res.error).toContain("Invalid API key");
  });
});

// The tools the leak test actually calls — the coverage check derives from HERE, not from a
// hand-written list. A new tool that is not added here turns the coverage test red.
// ================================================== waitUntilSent
// sendEmail answers "accepted", not "delivered". These cover the three ways the follow-up
// question can end; the timeout one is why the tool exists at all.
describe("waitUntilSent", () => {
  // A backend whose single message reports the given send_status.
  function statusSuite(status: string, sendError: string | null = null) {
    return makeSuite((url) => {
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/messages/5`);
      return jsonResponse(200, {
        ok: true,
        email: { id: 5, direction: "out", send_status: status, send_error: sendError, subject: "hi" },
      });
    });
  }

  it("reports delivery once the mail went out", async () => {
    const { suite } = statusSuite("sent");
    const res: any = await suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 5000 });
    expect(res.delivered).toBe(true);
    expect(res.status).toBe("sent");
    expect(res.timedOut).toBe(false);
    expect(res.message.id).toBe(5);
  });

  it("counts `unsigned` as delivered — it went out, just without a DKIM signature", async () => {
    const { suite } = statusSuite("unsigned");
    const res: any = await suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 5000 });
    expect(res.delivered).toBe(true);
    expect(res.status).toBe("unsigned");
  });

  it("reports permanent failure with the reason", async () => {
    const { suite } = statusSuite("failed", "550 unknown mailbox");
    const res: any = await suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 5000 });
    expect(res.delivered).toBe(false);
    expect(res.timedOut).toBe(false);
    expect(res.status).toBe("failed");
    expect(res.error).toContain("550 unknown mailbox");
  });

  it("🔒 a timeout must not read as failure", async () => {
    // A queued mail is not a lost mail. If this answer says "failed", or drops the
    // instruction not to resend, the model's reasonable next move is to send again and the
    // recipient gets the mail twice. Duplicate delivery — not silent loss — is the failure
    // mode this tool defends against.
    const { suite } = statusSuite("queued");
    const res: any = await suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 1 });
    expect(res.timedOut).toBe(true);
    expect(res.delivered).toBe(false);
    expect(res.status).toBe("queued");
    expect(JSON.stringify(res).toLowerCase()).not.toContain("fail");
    expect(res.note.toLowerCase()).toContain("do not send it again");
  });

  it("🔒 `retrying` does not read as `queued`", async () => {
    // The queue separates "never attempted" from "attempted, scheduled again", and the
    // status field carried that faithfully while the sentence said "Still queued" for both
    // — erasing the distinction exactly where it is used, in the model's decision to keep
    // waiting or not (B-099). Two external rounds reported it independently.
    const { suite } = statusSuite("retrying", "Could not reach example.invalid: timed out");
    const res: any = await suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 1 });
    expect(res.status).toBe("retrying");
    expect(res.note).not.toContain("Still queued");
    expect(res.note.toLowerCase()).toContain("attempted");
    expect(res.note.toLowerCase()).toContain("do not send it again");
    expect(JSON.stringify(res).toLowerCase()).not.toContain("fail");
    // The reason the last attempt gave is the evidence behind "keep waiting". It sat on the
    // exception and never reached the payload.
    expect(res.error).toContain("Could not reach example.invalid");
  });

  it("🔒 every branch answers with the same keys", async () => {
    // No hand-written key list here any more. The previous version of this test held a
    // third copy of the tuple, agreed with itself forever, and checked only the queued
    // branch — so the branch that had actually drifted (permanent failure, on MCP) was
    // invisible to it. Cross-LANGUAGE parity is measured by QA/sdk-parity, which calls all
    // four model surfaces and reads the key set from SEND_RESULT_KEYS itself; what belongs
    // here is the invariant this surface can check alone: the three branches agree.
    const branches = await Promise.all(
      [statusSuite("sent"), statusSuite("queued"), statusSuite("failed", "550 unknown mailbox")]
        .map(({ suite }) =>
          suite.waitUntilSent.execute({ address: ADDR, messageId: 5, timeout: 1 })),
    );
    const shapes = branches.map((res: any) => Object.keys(res).sort().join(","));
    expect(new Set(shapes).size).toBe(1);
    expect(shapes[0].split(",")).toContain("error");
  });
});

function leakRuns(suite: Record<string, any>): Array<[string, Promise<any>]> {
  return [
    ["createInbox", suite.createInbox.execute({ label: "leak" })],
    ["listInboxes", suite.listInboxes.execute({})],
    ["readMessages", suite.readMessages.execute({ address: ADDR })],
    ["waitForOtp", suite.waitForOtp.execute({ address: ADDR, timeout: 1 })],
    ["waitForMessage", suite.waitForMessage.execute({ address: ADDR, timeout: 1 })],
    ["sendEmail", suite.sendEmail.execute({ address: ADDR, to: "x@example.com", subject: "s", body: "b" })],
    ["reply", suite.reply.execute({ address: ADDR, messageId: 1, body: "ok" })],
    ["waitUntilSent", suite.waitUntilSent.execute({ address: ADDR, messageId: 1, timeout: 1 })],
    ["markRead", suite.markRead.execute({ address: ADDR, messageId: 1 })],
    ["burnInbox", suite.burnInbox.execute({ address: ADDR })],
    ["deleteMessage", suite.deleteMessage.execute({ address: ADDR, messageId: 1 })],
    ["deleteInbox", suite.deleteInbox.execute({ address: ADDR })],
    ["listCalendarEvents", suite.listCalendarEvents.execute({ address: ADDR })],
    ["rsvpToInvite", suite.rsvpToInvite.execute({ address: ADDR, eventId: 1, response: "accepted" })],
    ["createCalendarEvent", suite.createCalendarEvent.execute({ address: ADDR, title: "t",
      start: "2026-10-06T18:00:00Z", attendees: ["a@example.org"] })],
    ["updateCalendarEvent", suite.updateCalendarEvent.execute({ address: ADDR, eventId: 1, title: "u" })],
    ["cancelCalendarEvent", suite.cancelCalendarEvent.execute({ address: ADDR, eventId: 1 })],
    ["getCalendarFeed", suite.getCalendarFeed.execute({ address: ADDR })],
    ["rotateCalendarFeed", suite.rotateCalendarFeed.execute({ address: ADDR })],
  ];
}

// ================================================== secret redaction (B-055)
describe("secret redaction", () => {
  // Tool output goes into the MODEL's context, and from there into prompt logs and AI SDK
  // telemetry. createInbox used to return the backend payload as-is, and listInboxes dumped
  // every inbox key on the account in a single call.

  it("🔒 no tool output carries an inbox api key", async () => {
    const { suite } = makeSuite((url) => {
      if (url.endsWith("/api/v1/inboxes")) {
        return jsonResponse(200, {
          ok: true, address: ADDR, name: "leak", retention_hours: 2,
          api_key: "mf_sk_should_never_reach_the_model",
          inboxes: [{ address: ADDR, api_key: "mf_sk_one" }, { address: "b@x.net", api_key: "mf_sk_two" }],
        });
      }
      if (url.includes("/messages")) {
        return jsonResponse(200, { ok: true, emails: [{ id: 1, subject: "Verify", otp_code: "424242" }] });
      }
      if (url.includes("/latest")) {
        return jsonResponse(200, { ok: true, email: { id: 1, subject: "Verify", otp_code: "424242" } });
      }
      if (url.includes("/calendar/events?")) {
        return jsonResponse(200, { events: [{ id: 1, uid: "u1", status: "confirmed", api_key: "mf_sk_in_an_event" }] });
      }
      return jsonResponse(200, { ok: true, api_key: "mf_sk_from_a_write_endpoint" });
    });

    for (const [name, run] of leakRuns(suite)) {
      const blob = JSON.stringify(await run);
      expect(blob, `${name} leaked an inbox key`).not.toContain("mf_sk_");
      expect(blob, `${name} kept an api_key field`).not.toContain("api_key");
    }
  });

  it("🔒 every tool in the suite is covered by the leak test above", () => {
    // Coverage is compared against the tools the leak test REALLY calls, not a hand-written
    // list. The previous version used a literal: updating that list turned the test green
    // while the new tool was never executed.
    const { suite } = makeSuite(() => jsonResponse(200, {}));
    const covered = leakRuns(suite).map(([name]) => name);
    expect(covered.sort()).toEqual(Object.keys(suite).sort());
  });

  it("keeps what the agent needs (address, retention, otp)", async () => {
    const { suite } = makeSuite((url) => {
      if (url.includes("/latest")) {
        return jsonResponse(200, { ok: true, email: { id: 1, subject: "Verify", otp_code: "424242" } });
      }
      return jsonResponse(200, { ok: true, address: ADDR, retention_hours: 2, api_key: "mf_sk_x" });
    });

    const created: any = await suite.createInbox.execute({ label: "keeps" });
    expect(created.address).toBe(ADDR);
    expect(created.retention_hours).toBe(2);

    const otp: any = await suite.waitForOtp.execute({ address: ADDR, timeout: 1 });
    expect(otp.otp).toBe("424242");   // the field is `otp` here (`otp_code` in MCP)
  });

  it("the SDK itself still exposes the key to code", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { ok: true, address: ADDR, api_key: "mf_sk_visible" }));
    const client = new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any, maxRetries: 0 });
    const inbox = await client.create({ label: "sdk-side" });
    expect(inbox.apiKey ?? inbox.raw.api_key).toBe("mf_sk_visible");
  });
});

describe("unknown fields reach the server or are refused — never dropped", () => {
  // Round 7 #1. The code SDK was fixed (B-077) and the MODEL surfaces were not: zod objects
  // default to `strip`, so a field the model invents is deleted before the request and the
  // server's "not accepted here" answer never happens. That is the worst surface to leave
  // open, because here the party inventing the field name IS the model.
  it("🔒 refuses an unknown field instead of silently dropping it", async () => {
    const { suite, fetchMock } = makeSuite(() => jsonResponse(200, { address: ADDR }));
    const res = await suite.createInbox.execute({ public_key: "PGP", label: "x" } as any);

    expect(res.error, "unknown field was accepted").toBeTruthy();
    expect(String(res.error)).toContain("public_key");
    expect(fetchMock.mock.calls.length, "a refused call must not reach the server").toBe(0);
  });

  it("🔒 the same holds for send", async () => {
    const { suite } = makeSuite(() => jsonResponse(202, { queued: true, message_id: 1 }));
    const res = await suite.sendEmail.execute({
      address: ADDR, to: "a@b.com", subject: "s", body: "b", priority: "high",
    } as any);
    expect(res.error).toBeTruthy();
    expect(String(res.error)).toContain("priority");
  });

  it("every tool refuses unknown fields — coverage, not examples", async () => {
    // Enumerating the suite is the point: a tool added tomorrow is covered without anyone
    // remembering to add a case here.
    const { suite } = makeSuite(() => jsonResponse(200, {}));
    for (const [name, tool] of Object.entries(suite)) {
      const res: any = await (tool as any).execute({ zzz_unknown_field: 1 });
      expect(res?.error, `${name} accepted an unknown field`).toBeTruthy();
      expect(String(res.error), `${name} did not name the field`).toContain("zzz_unknown_field");
    }
  });

  it("valid arguments still work", async () => {
    // Negative control: refusing everything would pass all three tests above.
    const { suite } = makeSuite(() => jsonResponse(200, { address: ADDR }));
    const res: any = await suite.createInbox.execute({ prefix: "ok", label: "fine" });
    expect(res.error).toBeFalsy();
  });
});

// ---------------------------------------------------------------- calendar (plan 379)
describe("calendar tools", () => {
  const EVENT = { id: 7, uid: "abc@google.com", title: "Onboarding call", status: "confirmed",
                  my_status: "needs-action", start: "2026-10-01T14:00:00Z", all_day: false };

  it("listCalendarEvents returns the raw events and sends the flag", async () => {
    const urls: string[] = [];
    const { suite } = makeSuite((url) => {
      urls.push(url);
      return jsonResponse(200, { events: [EVENT] });
    });
    const out = await suite.listCalendarEvents.execute({ address: ADDR, includeCancelled: true });
    expect(out).toEqual({ events: [EVENT] });
    expect(urls).toEqual([
      `https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/events?include_cancelled=true`]);
  });

  it("rsvpToInvite posts the answer once and returns the server result", async () => {
    const bodies: any[] = [];
    const { suite, fetchMock } = makeSuite((url, init) => {
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/events/7/rsvp`);
      bodies.push(JSON.parse(init.body as string));
      return jsonResponse(202, { ok: true, event: { ...EVENT, my_status: "accepted" },
                                 message_id: 99, send_status: "queued" });
    });
    const out = await suite.rsvpToInvite.execute({ address: ADDR, eventId: 7, response: "accepted" });
    expect(out.message_id).toBe(99);
    expect(out.event.my_status).toBe("accepted");
    expect(bodies).toEqual([{ response: "accepted" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rsvpToInvite refuses an unknown answer before any request", async () => {
    const { suite, fetchMock } = makeSuite(() => jsonResponse(202, {}));
    const out = await suite.rsvpToInvite.execute({ address: ADDR, eventId: 7, response: "maybe" });
    expect(out.error).toMatch(/Invalid arguments/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a cancelled event comes back as an error value the model can read", async () => {
    const { suite } = makeSuite(() =>
      jsonResponse(400, { detail: "This event was cancelled by the organizer" }));
    const out = await suite.rsvpToInvite.execute({ address: ADDR, eventId: 7, response: "accepted" });
    expect(out.error).toMatch(/cancelled by the organizer/);
  });

  it("an event carrying a key is redacted before it reaches the model", async () => {
    const { suite } = makeSuite(() =>
      jsonResponse(200, { events: [{ ...EVENT, api_key: "mf_sk_leak" }] }));
    const out = await suite.listCalendarEvents.execute({ address: ADDR });
    expect(JSON.stringify(out)).not.toContain("mf_sk_");
  });
});

// ------------------------------------------------ calendar phase 2 (plan 379)
describe("calendar tools: meetings this inbox organizes", () => {
  const OUT = { id: 8, uid: "n@mailflat.net", title: "Intro", status: "confirmed",
                my_status: "accepted", source: "outbound", start: "2026-10-06T18:00:00Z" };

  it("createCalendarEvent sends optional attendees as objects and returns the server result", async () => {
    const bodies: any[] = [];
    const { suite, fetchMock } = makeSuite((url, init) => {
      expect(url).toBe(`https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/events`);
      bodies.push(JSON.parse(init.body as string));
      return jsonResponse(202, { ok: true, event: OUT, message_id: 12, send_status: "queued" });
    });
    const out = await suite.createCalendarEvent.execute({ address: ADDR, title: "Intro",
      start: "2026-10-06T18:00:00Z", attendees: ["ali@example.org"],
      optionalAttendees: ["bea@example.org"], durationMinutes: 45 });
    expect(out.message_id).toBe(12);
    expect(bodies).toEqual([{ title: "Intro", start: "2026-10-06T18:00:00Z", duration_minutes: 45,
      attendees: ["ali@example.org", { email: "bea@example.org", optional: true }] }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("updateCalendarEvent patches only the changes; an empty call is answered, not thrown", async () => {
    const seen: any[] = [];
    const { suite, fetchMock } = makeSuite((url, init) => {
      seen.push([init.method, url, JSON.parse(init.body as string)]);
      return jsonResponse(202, { ok: true, event: { ...OUT, sequence: 1 }, message_id: 13 });
    });
    await suite.updateCalendarEvent.execute({ address: ADDR, eventId: 8, timezone: "Europe/Istanbul" });
    expect(seen).toEqual([["PATCH", `https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/events/8`,
      { timezone: "Europe/Istanbul" }]]);
    const empty = await suite.updateCalendarEvent.execute({ address: ADDR, eventId: 8 });
    expect(empty.error).toMatch(/Nothing to change/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("getCalendarFeed / rotateCalendarFeed hand the link to the model, keys still masked", async () => {
    // The link is MEANT for the model (the agent passes it to a human), so it must survive
    // redaction; a stray key in the same payload must not.
    const seen: string[] = [];
    const feed = "https://mailflat.net/api/cal/mfcal_abc.ics";
    const { suite } = makeSuite((url, init) => {
      seen.push(`${init.method ?? "GET"} ${url}`);
      return jsonResponse(200, { enabled: true, feed_url: feed, api_key: "mf_sk_leak" });
    });
    const out = await suite.getCalendarFeed.execute({ address: ADDR });
    const rot = await suite.rotateCalendarFeed.execute({ address: ADDR });
    expect(out.feed_url).toBe(feed);
    expect(rot.feed_url).toBe(feed);
    expect(JSON.stringify([out, rot])).not.toContain("mf_sk_");
    const base = `https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/feed`;
    expect(seen).toEqual([`GET ${base}`, `POST ${base}/rotate`]);
  });

  it("cancelCalendarEvent posts to /cancel", async () => {
    const urls: string[] = [];
    const { suite } = makeSuite((url) => {
      urls.push(url);
      return jsonResponse(202, { ok: true, event: { ...OUT, status: "cancelled" }, message_id: 14 });
    });
    const out = await suite.cancelCalendarEvent.execute({ address: ADDR, eventId: 8 });
    expect(out.event.status).toBe("cancelled");
    expect(urls).toEqual([`https://mailflat.net/api/v1/inboxes/${ADDR}/calendar/events/8/cancel`]);
  });
});

