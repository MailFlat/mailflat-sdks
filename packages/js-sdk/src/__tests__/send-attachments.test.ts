// Outgoing attachments, cc/bcc and waitUntilSent — the JS side of phase 3b.
//
// The mirror of packages/python-sdk/tests/test_send_attachments.py. Both surfaces get the
// same behaviour tests on purpose: B-055 was exactly a feature that existed on both sides
// but only worked on one, and a name-by-name comparison would not have caught it.
//
// Connected to:
//   - exercises: ../inbox.ts (encodeAttachment, send, reply, message, waitUntilSent),
//                ../errors.ts (SendFailedError, SendTimeoutError)

import { describe, expect, it, vi } from "vitest";
import { MailFlat, MailFlatError, SendFailedError, SendTimeoutError } from "../index";

const ADDR = "agent@x7k2m.mailflat.net";
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0xff, 0x00, 0x7f]);

function jsonResponse(status: number, body: any): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Client plus the list of request bodies it sent. */
function captureSend(status = 202, body: any = { ok: true, queued: true, message_id: 7 }) {
  const sent: any[] = [];
  const fetchMock = vi.fn(async (_url: any, init: any) => {
    sent.push(JSON.parse(init.body));
    return jsonResponse(status, body);
  });
  return { mf: new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any }), sent };
}

describe("outgoing attachments", () => {
  it("encodes raw bytes to base64 so an agent never has to", async () => {
    const { mf, sent } = captureSend();
    await mf.inbox(ADDR).send("peer@example.com", {
      attachments: [{ filename: "invoice.pdf", content: PDF, contentType: "application/pdf" }],
    });

    const att = sent[0].attachments[0];
    expect(att.filename).toBe("invoice.pdf");
    expect(att.content_type).toBe("application/pdf");
    // Decode back: high bytes (0xff, 0x00) are where a naive string conversion corrupts data.
    const decoded = Uint8Array.from(atob(att.content_b64), (ch) => ch.charCodeAt(0));
    expect(Array.from(decoded)).toEqual(Array.from(PDF));
  });

  it("encodes a large attachment without blowing the call stack", async () => {
    // String.fromCharCode(...bytes) throws on a big array — the chunked encoder is why
    // this passes. A 2 MB file is small for the product and already breaks the naive way.
    const big = new Uint8Array(2 * 1024 * 1024).fill(0x41);
    const { mf, sent } = captureSend();
    await mf.inbox(ADDR).send("peer@example.com", {
      attachments: [{ filename: "big.bin", content: big }],
    });
    expect(atob(sent[0].attachments[0].content_b64).length).toBe(big.length);
  });

  it("accepts ready-made base64 and defaults the content type", async () => {
    const { mf, sent } = captureSend();
    await mf.inbox(ADDR).send("peer@example.com", {
      attachments: [{ filename: "a.txt", contentBase64: "aGVsbG8=" }],
    });
    expect(sent[0].attachments[0].content_b64).toBe("aGVsbG8=");
    expect(sent[0].attachments[0].content_type).toBe("application/octet-stream");
  });

  it("rejects an attachment with no content before the request goes out", async () => {
    const { mf, sent } = captureSend();
    await expect(
      mf.inbox(ADDR).send("peer@example.com", { attachments: [{ filename: "a.pdf" }] }),
    ).rejects.toThrow(MailFlatError);
    expect(sent).toEqual([]);
  });

  it("omits cc/bcc/attachments entirely when not given", async () => {
    const { mf, sent } = captureSend();
    await mf.inbox(ADDR).send("peer@example.com", { subject: "hi" });
    expect(sent[0].cc).toBeUndefined();
    expect(sent[0].bcc).toBeUndefined();
    expect(sent[0].attachments).toBeUndefined();
  });
});

describe("cc / bcc", () => {
  it("sends them under the wire names", async () => {
    const { mf, sent } = captureSend();
    await mf.inbox(ADDR).send("peer@example.com", {
      cc: ["watcher@example.com"],
      bcc: ["secret@example.com"],
    });
    expect(sent[0].cc).toEqual(["watcher@example.com"]);
    expect(sent[0].bcc).toEqual(["secret@example.com"]);
  });
});

describe("reply", () => {
  it("carries attachments, cc and bcc while staying threaded", async () => {
    // K6: answering "here is the invoice" must not force a drop back to send(), which
    // would start a NEW conversation in the recipient's client.
    const sent: any[] = [];
    const fetchMock = vi.fn(async (url: any, init: any) => {
      if (String(url).endsWith("/send")) {
        sent.push(JSON.parse(init.body));
        return jsonResponse(202, { ok: true, queued: true, message_id: 9 });
      }
      return jsonResponse(200, {
        emails: [
          {
            id: 1,
            sender: "human@gmail.com",
            subject: "Invoice?",
            headers: { "Message-ID": "<abc@gmail.com>", From: "human@gmail.com" },
          },
        ],
      });
    });
    const mf = new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any });
    const [message] = await mf.inbox(ADDR).messages({ direction: "all" });
    await message.reply!("attached", {
      cc: ["boss@example.com"],
      bcc: ["audit@example.com"],
      attachments: [{ filename: "report.pdf", content: PDF }],
    });

    expect(sent[0].in_reply_to).toBe("<abc@gmail.com>");
    expect(sent[0].cc).toEqual(["boss@example.com"]);
    expect(sent[0].bcc).toEqual(["audit@example.com"]);
    expect(sent[0].attachments[0].filename).toBe("report.pdf");
  });
});

describe("202 + waitUntilSent", () => {
  it("treats 202 Accepted as success", async () => {
    // Regression lock: the endpoint moved from 200 to 202 under us.
    const { mf } = captureSend(202);
    await expect(mf.inbox(ADDR).send("peer@example.com")).resolves.toMatchObject({
      queued: true,
    });
  });

  function statusClient(statuses: Record<string, any>[]) {
    let n = 0;
    const fetchMock = vi.fn(async () => {
      const s = statuses[Math.min(n, statuses.length - 1)];
      n += 1;
      return jsonResponse(200, { ok: true, email: { id: 7, ...s } });
    });
    return {
      mf: new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any }),
      calls: () => n,
    };
  }

  it("returns the message once it is delivered", async () => {
    const { mf, calls } = statusClient([{ send_status: "queued" }, { send_status: "sent" }]);
    const msg = await mf.inbox(ADDR).waitUntilSent(7, { timeout: 5000, pollInterval: 5 });
    expect(msg.sendStatus).toBe("sent");
    expect(calls()).toBe(2);
  });

  it("accepts unsigned as delivered", async () => {
    // "unsigned" = delivered without a DKIM signature. Delivered nonetheless.
    const { mf } = statusClient([{ send_status: "unsigned" }]);
    await expect(mf.inbox(ADDR).waitUntilSent(7, { timeout: 1000 })).resolves.toMatchObject({
      sendStatus: "unsigned",
    });
  });

  it("throws on permanent failure with the server's reason", async () => {
    const { mf } = statusClient([{ send_status: "failed", send_error: "550 user unknown" }]);
    await expect(mf.inbox(ADDR).waitUntilSent(7, { timeout: 1000 })).rejects.toThrow(
      /550 user unknown/,
    );
    await expect(mf.inbox(ADDR).waitUntilSent(7, { timeout: 1000 })).rejects.toBeInstanceOf(
      SendFailedError,
    );
  });

  it("throws a timeout that says the mail may still arrive", async () => {
    // Timeout is NOT failure: the queue keeps retrying. An agent told "lost" would resend
    // and double-deliver.
    const { mf } = statusClient([{ send_status: "queued" }]);
    const err = await mf
      .inbox(ADDR)
      .waitUntilSent(7, { timeout: 30, pollInterval: 5 })
      .catch((e) => e);
    expect(err).toBeInstanceOf(SendTimeoutError);
    expect(err.message).toMatch(/still be delivered/);
    expect(err.status).toBe("queued");
  });

  it("reads one message by id instead of the whole outbound list", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn(async (url: any) => {
      urls.push(String(url));
      return jsonResponse(200, { ok: true, email: { id: 42, subject: "x" } });
    });
    const mf = new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any });
    const msg = await mf.inbox(ADDR).message(42);
    expect(msg.id).toBe(42);
    expect(urls[0]).toContain(`/api/v1/inboxes/${ADDR}/messages/42`);
  });
});
