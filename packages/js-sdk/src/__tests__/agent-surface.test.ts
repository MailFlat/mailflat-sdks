// Tests for the plan-203 fixes — retry safety, direction filter, new Message fields.
//
// The JS mirror of test_agent_surface.py: the same findings existed on both surfaces, so both
// are locked down.
//
// Connected to:
//   - exercises: ../client.ts, ../inbox.ts, ../types.ts, ../errors.ts

import { MAX_SUBJECT_CHARS, replySubject } from "../types";
import { describe, expect, it, vi } from "vitest";
import { MailFlat, MailFlatError, RateLimitError, VERSION } from "../index";

const ADDR = "agent@x7k2m.mailflat.net";

function jsonResponse(status: number, body: any, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function makeClient(
  handler: (url: string, init: RequestInit) => Response,
  opts: Record<string, any> = {},
) {
  const fetchMock = vi.fn(async (url: any, init: any) => handler(String(url), init));
  return {
    mf: new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any, maxRetries: 2, ...opts }),
    fetchMock,
  };
}

// ------------------------------------------------------------------ retry safety
describe("retry safety", () => {
  it("🔒 send is NOT retried — a retried send can deliver the same mail twice", async () => {
    const { mf, fetchMock } = makeClient(() => jsonResponse(504, { detail: "gateway timeout" }));
    await expect(mf.inbox(ADDR).send("peer@example.com", { subject: "hi" })).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("GET is still retried (resilience is not the thing we removed)", async () => {
    let n = 0;
    const { mf, fetchMock } = makeClient(() => {
      n += 1;
      return n < 3 ? jsonResponse(503, {}) : jsonResponse(200, { ok: true, emails: [] });
    });
    await mf.inbox(ADDR).messages();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("DELETE is retried — deleting the same message twice has the same result", async () => {
    let n = 0;
    const { mf, fetchMock } = makeClient(() => {
      n += 1;
      return n === 1 ? jsonResponse(502, {}) : jsonResponse(200, { ok: true });
    });
    await mf.inbox(ADDR).deleteMessage(5);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("honours Retry-After instead of its own backoff", async () => {
    const slept: number[] = [];
    vi.spyOn(globalThis, "setTimeout").mockImplementation(((fn: any, ms?: number) => {
      if (ms && ms > 0) slept.push(ms);
      fn();
      return 0 as any;
    }) as any);
    let n = 0;
    const { mf } = makeClient(() => {
      n += 1;
      return n === 1
        ? jsonResponse(429, {}, { "Retry-After": "7" })
        : jsonResponse(200, { ok: true, emails: [] });
    });
    await mf.inbox(ADDR).messages();
    vi.restoreAllMocks();
    expect(slept).toContain(7000);
  });

  it("RateLimitError carries retryAfter so the caller can schedule", async () => {
    const { mf } = makeClient(() => jsonResponse(429, { detail: "slow down" }, { "Retry-After": "12" }), {
      maxRetries: 0,
    });
    await expect(mf.inbox(ADDR).messages()).rejects.toMatchObject({
      name: "RateLimitError",
      retryAfter: 12,
    });
    await expect(mf.inbox(ADDR).messages()).rejects.toBeInstanceOf(RateLimitError);
  });
});

// ------------------------------------------------------------------ direction filter
describe("direction", () => {
  it("🔒 reads default to incoming mail only", async () => {
    const urls: string[] = [];
    const { mf } = makeClient((url) => {
      urls.push(url);
      return url.includes("/messages")
        ? jsonResponse(200, { ok: true, emails: [] })
        : jsonResponse(200, { ok: true, email: { id: 1, otp_code: "123456" } });
    });
    const inbox = mf.inbox(ADDR);
    await inbox.messages();
    await inbox.latest();
    await inbox.waitForOtp({ timeout: 1 });
    expect(urls.every((u) => u.includes("direction=in"))).toBe(true);
  });

  it("direction can be overridden", async () => {
    const urls: string[] = [];
    const { mf } = makeClient((url) => {
      urls.push(url);
      return jsonResponse(200, { ok: true, emails: [], email: null });
    });
    await mf.inbox(ADDR).messages({ direction: "out" });
    await mf.inbox(ADDR).latest({ direction: "all" });
    expect(urls[0]).toContain("direction=out");
    expect(urls[1]).toContain("direction=all");
  });

  it("an invalid direction fails before any network call", async () => {
    const { mf, fetchMock } = makeClient(() => jsonResponse(200, { ok: true }));
    await expect(mf.inbox(ADDR).messages({ direction: "sideways" as any })).rejects.toThrow(
      MailFlatError,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ Message fields
const RICH_EMAIL = {
  id: 42,
  sender: "billing@acme.com",
  subject: "Invoice",
  body_text: "See https://acme.com/inv/1",
  direction: "in",
  links: ["https://acme.com/inv/1"],
  attachments: [
    {
      id: 9,
      filename: "invoice.pdf",
      content_type: "application/pdf",
      size_bytes: 1234,
      is_encrypted: false,
      truncated: false,
    },
  ],
  spam: { score: 0.4, required: 5.0, is_spam: false, rules: [], scanner: "sa" },
  headers: { "Message-Id": "<abc@acme.com>" },
};

describe("message fields the backend was already sending", () => {
  it("exposes links, attachments, spam and headers", async () => {
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email: RICH_EMAIL }));
    const msg = await mf.inbox(ADDR).latest();
    expect(msg?.links).toEqual(["https://acme.com/inv/1"]);
    expect(msg?.spam?.score).toBe(0.4);
    expect(msg?.headers?.["Message-Id"]).toBe("<abc@acme.com>");
    expect(msg?.attachments).toHaveLength(1);
    expect(msg?.attachments[0].filename).toBe("invoice.pdf");
  });

  it("links/attachments are always arrays (callers can check length safely)", async () => {
    const { mf } = makeClient(() =>
      jsonResponse(200, { ok: true, email: { id: 1, subject: "plain" } }),
    );
    const msg = await mf.inbox(ADDR).latest();
    expect(msg?.links).toEqual([]);
    expect(msg?.attachments).toEqual([]);
    expect(msg?.spam).toBeNull();
  });

  it("downloads attachment bytes", async () => {
    const { mf } = makeClient((url) => {
      if (url.includes("/attachments/")) {
        expect(url).toContain("/messages/42/attachments/9");
        return new Response(new Uint8Array([37, 80, 68, 70]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        });
      }
      return jsonResponse(200, { ok: true, email: RICH_EMAIL });
    });
    const msg = await mf.inbox(ADDR).latest();
    const bytes = await msg!.attachments[0].download!();
    expect(Array.from(bytes)).toEqual([37, 80, 68, 70]);
  });

  it("a truncated attachment explains itself instead of returning empty bytes", async () => {
    const email = JSON.parse(JSON.stringify(RICH_EMAIL));
    email.attachments[0].truncated = true;
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email }));
    const msg = await mf.inbox(ADDR).latest();
    expect(() => msg!.attachments[0].download!()).toThrow(/too large/);
  });
});

// ------------------------------------------------------------------ M2 surface
describe("mark read + burn", () => {
  it("calls the expected endpoints", async () => {
    const seen: Array<[string, string]> = [];
    const { mf } = makeClient((url, init) => {
      seen.push([String(init.method), url]);
      return jsonResponse(200, { ok: true, burned: 3 });
    });
    const inbox = mf.inbox(ADDR);
    await inbox.markRead(42);
    const res = await inbox.burn();
    expect(res.burned).toBe(3);
    expect(seen[0][0]).toBe("POST");
    expect(seen[0][1]).toContain(`/api/v1/inboxes/${ADDR}/messages/42/read`);
    expect(seen[1][1]).toContain(`/api/v1/inboxes/${ADDR}/burn`);
  });

  it("message.markRead() is wired to the message it came from", async () => {
    const seen: string[] = [];
    const { mf } = makeClient((url) => {
      seen.push(url);
      return url.includes("/read")
        ? jsonResponse(200, { ok: true })
        : jsonResponse(200, { ok: true, email: RICH_EMAIL });
    });
    const msg = await mf.inbox(ADDR).latest();
    await msg!.markRead!();
    expect(seen[seen.length - 1]).toContain("/messages/42/read");
  });
});

// ------------------------------------------------------------------ diagnosability
describe("diagnosability", () => {
  it("sends a versioned User-Agent so the server can tell which SDK broke", async () => {
    const { mf } = makeClient((_url, init) => {
      expect((init.headers as any)["User-Agent"]).toBe(`mailflat-js/${VERSION}`);
      return jsonResponse(200, { ok: true, emails: [] });
    });
    expect(MailFlat.userAgent()).toBe(`mailflat-js/${VERSION}`);
    await mf.inbox(ADDR).messages();
  });
});

// ------------------------------------------------------------------ 0.3.1 fixes
// The JS mirror of the findings a second external review measured.
describe("0.3.1 fixes", () => {
  it("🔒 attachment download retries like any other GET", async () => {
    let n = 0;
    const { mf, fetchMock } = makeClient((url) => {
      if (!url.includes("/attachments/")) return jsonResponse(200, { ok: true, email: RICH_EMAIL });
      n += 1;
      return n < 3
        ? jsonResponse(503, {})
        : new Response(new Uint8Array([80, 68, 70]), {
            status: 200,
            headers: { "Content-Type": "application/pdf" },
          });
    });
    const msg = await mf.inbox(ADDR).latest();
    const bytes = await msg!.attachments[0].download!();
    expect(Array.from(bytes)).toEqual([80, 68, 70]);
    expect(n).toBe(3);
    expect(fetchMock).toHaveBeenCalled();
  });

  it("markRead/burn retry (idempotent POST) but send still never does", async () => {
    for (const call of [
      (ib: any) => ib.markRead(1),
      (ib: any) => ib.burn(),
    ]) {
      let n = 0;
      const { mf } = makeClient(() => {
        n += 1;
        return n < 2 ? jsonResponse(503, {}) : jsonResponse(200, { ok: true });
      });
      await call(mf.inbox(ADDR));
      expect(n).toBe(2);
    }
    const { mf, fetchMock } = makeClient(() => jsonResponse(503, {}));
    await expect(mf.inbox(ADDR).send("x@example.com")).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("🔒 header lookup is case-insensitive (real key is Message-ID)", async () => {
    const email = { ...RICH_EMAIL, headers: { "Message-ID": "<abc@acme.com>", Subject: "Invoice" } };
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email }));
    const msg = await mf.inbox(ADDR).latest();
    expect(msg!.messageId).toBe("<abc@acme.com>");
    for (const spelling of ["Message-ID", "Message-Id", "message-id", "MESSAGE-ID", "message_id"]) {
      expect(msg!.header(spelling), spelling).toBe("<abc@acme.com>");
    }
    expect(msg!.header("X-Nope")).toBeUndefined();
  });

  it("header helper survives null headers (encrypted inbox)", async () => {
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email: { id: 1, headers: null } }));
    const msg = await mf.inbox(ADDR).latest();
    expect(msg!.messageId).toBeUndefined();
    expect(msg!.header("message-id")).toBeUndefined();
  });
});

// ------------------------------------------------------------------ diagnosable OTP timeout
describe("OTP timeout diagnosability", () => {
  it("says nothing arrived when the inbox is empty", async () => {
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email: null }));
    await expect(mf.inbox(ADDR).waitForOtp({ timeout: 0 })).rejects.toThrow(/No message arrived/);
  });

  it("🔒 quotes the message when one did arrive but carried no code", async () => {
    const email = { id: 1, subject: "Sign in", body_text: "Use 552134 to sign in", otp_code: null };
    const { mf } = makeClient(() => jsonResponse(200, { ok: true, email }));
    await expect(mf.inbox(ADDR).waitForOtp({ timeout: 0 })).rejects.toThrow(
      /Mail arrived.*no OTP could be extracted.*Sign in.*Use 552134 to sign in/s,
    );
  });
});

// ------------------------------------------------------------------ reply / threading
describe("reply", () => {
  const INCOMING = {
    id: 5, sender: "human@gmail.com", subject: "Question", body_text: "can you help?",
    headers: { "Message-ID": "<abc@mail.gmail.com>" },
  };

  it("🔒 fills recipient, Re: subject and the thread headers", async () => {
    let sent: any = null;
    const { mf } = makeClient((url, init) => {
      if (url.includes("/send")) {
        sent = JSON.parse(init.body as string);
        return jsonResponse(200, { ok: true });
      }
      return jsonResponse(200, { ok: true, email: INCOMING });
    });
    const msg = await mf.inbox(ADDR).latest();
    await msg!.reply!("sure, here you go");
    expect(sent.to).toBe("human@gmail.com");
    expect(sent.subject).toBe("Re: Question");
    expect(sent.in_reply_to).toBe("<abc@mail.gmail.com>");
  });

  it("does not stack Re: prefixes", async () => {
    const { replySubject } = await import("../types");
    expect(replySubject("Question")).toBe("Re: Question");
    expect(replySubject("Re: Question")).toBe("Re: Question");
    expect(replySubject("RE: Question")).toBe("RE: Question");
    expect(replySubject(null)).toBe("Re:");

    // 🔒 A stranger's subject must not make a message permanently unanswerable. Inbound
    // mail is parsed with encoded-words decoded, so a subject can hold a real CRLF; this
    // helper prefixed "Re: " and the server's header guard then rejected it, forever, for
    // that message. Same rule as the length axis: repair what WE derived, reject what the
    // caller wrote.
    expect(replySubject("a\r\nb")).toBe("Re: a b");
    expect(replySubject("x\r\nBcc: victim@example.com")).not.toMatch(/[\r\n]/);
    expect(replySubject("a\tb")).toContain("\t");
  });

  it("still sends when the message carries no thread id", async () => {
    let sent: any = null;
    const { mf } = makeClient((url, init) => {
      if (url.includes("/send")) {
        sent = JSON.parse(init.body as string);
        return jsonResponse(200, { ok: true });
      }
      return jsonResponse(200, { ok: true, email: { ...INCOMING, headers: null } });
    });
    const msg = await mf.inbox(ADDR).latest();
    await msg!.reply!("ok");
    expect(sent.to).toBe("human@gmail.com");
    expect(sent.in_reply_to).toBeUndefined();
  });
});

// ------------------------------------------------------------------ reply target (5th review)
describe("reply target", () => {
  const msgWith = (headers: any, sender = "bounces+abc@sendgrid.net") =>
    makeClient((url, init) => {
      if (url.includes("/send")) {
        return jsonResponse(200, { ok: true, echo: JSON.parse(init.body as string) });
      }
      return jsonResponse(200, {
        ok: true,
        email: { id: 9, sender, subject: "Welcome", headers },
      });
    }).mf;

  it("🔒 prefers Reply-To over the envelope sender (a bounce address)", async () => {
    const mf = msgWith({ "Reply-To": "Support <support@acme.com>", From: "Acme <no-reply@acme.com>" });
    const msg = await mf.inbox(ADDR).latest();
    expect(msg!.replyToAddress).toBe("support@acme.com");
    const res = await msg!.reply!("hi");
    expect(res.echo.to).toBe("support@acme.com");
  });

  it("falls back to From, then to the envelope sender", async () => {
    let mf = msgWith({ From: "Acme Billing <billing@acme.com>" });
    expect((await mf.inbox(ADDR).latest())!.replyToAddress).toBe("billing@acme.com");
    mf = msgWith(null, "human@gmail.com");
    expect((await mf.inbox(ADDR).latest())!.replyToAddress).toBe("human@gmail.com");
  });

  it("handles a repeated header and ignores a malformed one", async () => {
    let mf = msgWith({ "Reply-To": ["first@acme.com", "second@acme.com"] });
    expect((await mf.inbox(ADDR).latest())!.replyToAddress).toBe("first@acme.com");
    mf = msgWith({ "Reply-To": "not-an-address", From: "Real <real@acme.com>" });
    expect((await mf.inbox(ADDR).latest())!.replyToAddress).toBe("real@acme.com");
  });
});

describe("reply() can answer a message the service accepted", () => {
  // Round 8 #4: inbound mail is not bound by our 200-char API limit, so a stored
  // 198-character subject made `"Re: " + subject` exceed it and `reply()` refused —
  // a message the service itself had accepted became permanently unanswerable, with
  // no trim and no explanation offered.
  it("🔒 a long inbound subject still produces a usable reply subject", () => {
    const long = "K".repeat(198);
    const subject = replySubject(long);
    expect(subject.length).toBeLessThanOrEqual(MAX_SUBJECT_CHARS);
    expect(subject.startsWith("Re: ")).toBe(true);
    expect(subject.endsWith("…")).toBe(true);
  });

  it("a normal subject is untouched", () => {
    expect(replySubject("Your code")).toBe("Re: Your code");
  });
});
