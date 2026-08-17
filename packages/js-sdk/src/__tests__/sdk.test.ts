// @mailflat/sdk tests — real /api/v1 responses faked through an injected mock fetch.
//
// Covers: create/list/messages/latest/send/delete + waitForOtp (success/timeout/encrypted)
// + error mapping (401/403/404/429) + the env API key.

import { describe, expect, it, vi } from "vitest";
import {
  AuthenticationError,
  EncryptedInboxError,
  MailFlat,
  MailFlatError,
  NotFoundError,
  OTPTimeoutError,
  PermissionError,
  RateLimitError,
} from "../index";

const ADDR = "signup-test@x7k2m.mailflat.net";

function jsonResponse(status: number, body: any): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Build a client that uses the given handler as its fetch.
function makeClient(handler: (url: string, init: RequestInit) => Response) {
  const fetchMock = vi.fn(async (url: any, init: any) => handler(String(url), init));
  return new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any, maxRetries: 0 });
}

describe("create", () => {
  it("returns an Inbox", async () => {
    const mf = makeClient((url, init) => {
      expect(init.method).toBe("POST");
      expect(url).toBe("https://mailflat.net/api/v1/inboxes");
      expect((init.headers as any)["X-API-Key"]).toBe("mf_test_x");
      expect(JSON.parse(init.body as string)).toEqual({ label: "signup-test" });
      return jsonResponse(200, {
        ok: true,
        address: ADDR,
        api_key: "mf_sk_1",
        name: "signup-test",
        retention_hours: 2,
      });
    });
    const inbox = await mf.create({ label: "signup-test" });
    expect(inbox.address).toBe(ADDR);
    expect(inbox.name).toBe("signup-test");
    expect(inbox.retentionHours).toBe(2);
    expect(inbox.apiKey).toBe("mf_sk_1");
  });

  it("createInbox alias + full payload", async () => {
    let seen: any;
    const mf = makeClient((_url, init) => {
      seen = JSON.parse(init.body as string);
      return jsonResponse(200, { ok: true, address: ADDR });
    });
    await mf.createInbox({ prefix: "bob", subdomain: "acme", retentionHours: 6 });
    expect(seen).toEqual({ prefix: "bob", subdomain: "acme", retention_hours: 6 });
  });
});

describe("list / messages / latest", () => {
  it("lists inboxes", async () => {
    const mf = makeClient((url) => {
      expect(url).toBe("https://mailflat.net/api/v1/inboxes");
      return jsonResponse(200, {
        ok: true,
        inboxes: [
          { address: ADDR, name: "a", via_api: true },
          { address: "b@x.mailflat.net", name: "b" },
        ],
      });
    });
    const inboxes = await mf.list();
    expect(inboxes.map((i) => i.address)).toEqual([ADDR, "b@x.mailflat.net"]);
  });

  it("messages + latest", async () => {
    const email = {
      id: 1,
      sender: "no-reply@figma.com",
      subject: "Your code",
      body_text: "Code: 123456",
      otp_code: "123456",
      direction: "in",
      is_encrypted: false,
    };
    // The URL now carries `?direction=in`, so match with includes rather than endsWith.
    const mf = makeClient((url) =>
      url.includes("/messages")
        ? jsonResponse(200, { ok: true, emails: [email] })
        : jsonResponse(200, { ok: true, email }),
    );
    const inbox = mf.inbox(ADDR);
    const msgs = await inbox.messages();
    expect(msgs).toHaveLength(1);
    expect(msgs[0].otp).toBe("123456");
    expect(msgs[0].text).toBe("Code: 123456");
    expect((await inbox.latest())?.subject).toBe("Your code");
  });

  it("latest is null when empty", async () => {
    const mf = makeClient(() => jsonResponse(200, { ok: true, email: null }));
    expect(await mf.inbox(ADDR).latest()).toBeNull();
  });
});

describe("waitForOtp", () => {
  it("returns the code", async () => {
    let n = 0;
    const mf = makeClient(() => {
      n += 1;
      return n < 3
        ? jsonResponse(200, { ok: true, email: null })
        : jsonResponse(200, { ok: true, email: { otp_code: "987654" } });
    });
    const otp = await mf.inbox(ADDR).waitForOtp({ timeout: 10000, pollInterval: 0 });
    expect(otp).toBe("987654");
    expect(n).toBe(3);
  });

  it("times out", async () => {
    const mf = makeClient(() => jsonResponse(200, { ok: true, email: null }));
    await expect(
      mf.inbox(ADDR).waitForOtp({ timeout: 0, pollInterval: 0 }),
    ).rejects.toBeInstanceOf(OTPTimeoutError);
  });

  it("throws on encrypted inbox", async () => {
    const mf = makeClient(() =>
      jsonResponse(200, { ok: true, encrypted: true, note: "e2e", email: { is_encrypted: true } }),
    );
    await expect(
      mf.inbox(ADDR).waitForOtp({ timeout: 5000, pollInterval: 0 }),
    ).rejects.toBeInstanceOf(EncryptedInboxError);
  });
});

describe("send / delete", () => {
  it("send", async () => {
    const mf = makeClient((url, init) => {
      expect(init.method).toBe("POST");
      expect(url.endsWith("/send")).toBe(true);
      expect(JSON.parse(init.body as string)).toEqual({
        to: "x@y.com",
        subject: "Hi",
        body: "Hello",
        html: "<b>Hello</b>",
      });
      return jsonResponse(200, { ok: true, message: "Sent to x@y.com" });
    });
    const res = await mf.inbox(ADDR).send("x@y.com", {
      subject: "Hi",
      body: "Hello",
      html: "<b>Hello</b>",
    });
    expect(res.ok).toBe(true);
  });

  it("delete", async () => {
    const mf = makeClient((_url, init) => {
      expect(init.method).toBe("DELETE");
      return jsonResponse(200, { ok: true, message: "Inbox deleted" });
    });
    expect((await mf.inbox(ADDR).delete()).message).toBe("Inbox deleted");
  });

  it("deleteMessage", async () => {
    const mf = makeClient((url, init) => {
      expect(init.method).toBe("DELETE");
      expect(url.endsWith(`/inboxes/${ADDR}/messages/42`)).toBe(true);
      return jsonResponse(200, { ok: true, message: "Email deleted" });
    });
    expect((await mf.inbox(ADDR).deleteMessage(42)).message).toBe("Email deleted");
  });

  it("message.delete() sugar", async () => {
    const mf = makeClient((url, init) => {
      if (init.method === "GET") {
        return jsonResponse(200, { ok: true, email: { id: 7, subject: "Hi" } });
      }
      expect(init.method).toBe("DELETE");
      expect(url.endsWith("/messages/7")).toBe(true);
      return jsonResponse(200, { ok: true, message: "Email deleted" });
    });
    const msg = await mf.inbox(ADDR).latest();
    expect(msg?.id).toBe(7);
    expect((await msg!.delete!()).message).toBe("Email deleted");
  });
});

describe("errors", () => {
  it.each([
    [401, AuthenticationError],
    [403, PermissionError],
    [404, NotFoundError],
    [429, RateLimitError],
  ])("maps %i", async (status, ExpectedError) => {
    const mf = makeClient(() => jsonResponse(status as number, { detail: "nope" }));
    await expect(mf.inbox(ADDR).messages()).rejects.toBeInstanceOf(ExpectedError as any);
  });

  it("200 with error field throws", async () => {
    const mf = makeClient(() => jsonResponse(200, { error: "boom" }));
    await expect(mf.inbox(ADDR).messages()).rejects.toBeInstanceOf(MailFlatError);
  });
});

describe("api key", () => {
  it("reads from env", () => {
    process.env.MAILFLAT_API_KEY = "mf_env";
    const mf = new MailFlat({ fetch: (async () => new Response("{}")) as any });
    expect(mf.apiKey).toBe("mf_env");
    delete process.env.MAILFLAT_API_KEY;
  });

  it("throws when missing", () => {
    delete process.env.MAILFLAT_API_KEY;
    expect(() => new MailFlat({ fetch: (async () => new Response("{}")) as any })).toThrow(
      MailFlatError,
    );
  });
});
