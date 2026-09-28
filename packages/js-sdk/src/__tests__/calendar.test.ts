// Calendar surface (plan 379 phase 1) — the JS mirror of python-sdk/tests/test_calendar.py.
//
// What these prove (behaviour, not shape):
//   - each method hits the exact route, and the include_cancelled flag is on the wire;
//   - snake_case server fields become the camelCase the rest of this SDK uses (myStatus,
//     allDay, eventId), and `raw` keeps the payload untouched;
//   - rsvp sends exactly what the caller said (no `comment: undefined` key);
//   - rsvp is NOT retried: a retry after a lost response would answer the organizer twice;
//   - message.calendarEvent is parsed, and null when the message carries no invitation.
//
// Connected to:
//   - exercises: ../inbox.ts (calendarEvents / calendarEvent / rsvp), ../types.ts

import { describe, expect, it, vi } from "vitest";
import { MailFlat } from "../index";

const ADDR = "agent@x7k2m.mailflat.net";
const BASE = `/api/v1/inboxes/${ADDR}/calendar/events`;
const EVENT = {
  id: 7, uid: "abc@google.com", title: "Onboarding call", start: "2026-10-01T14:00:00Z",
  end: "2026-10-01T14:30:00Z", all_day: false, status: "confirmed", my_status: "needs-action",
  sequence: 0, attendees: [{ email: ADDR, name: null, status: "needs-action", role: "req-participant" }],
  organizer: { email: "jane@example.com", name: "Jane" }, last_message_id: 3,
};

function json(status: number, body: any): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function client(handler: (url: URL, init: RequestInit) => Response) {
  const fetchMock = vi.fn(async (url: any, init: any) => handler(new URL(String(url)), init));
  return { mf: new MailFlat({ apiKey: "mf_test_x", fetch: fetchMock as any, maxRetries: 3 }), fetchMock };
}

describe("calendar", () => {
  it("calendarEvents hits the route, sends the flag and maps to camelCase", async () => {
    const seen: string[] = [];
    const { mf } = client((url) => {
      seen.push(`${url.pathname}?${url.searchParams.toString()}`);
      return json(200, { events: [EVENT] });
    });
    const ib = mf.inbox(ADDR);
    const [ev] = await ib.calendarEvents();
    await ib.calendarEvents({ includeCancelled: true });
    expect(seen).toEqual([`${BASE}?include_cancelled=false`, `${BASE}?include_cancelled=true`]);
    expect(ev.myStatus).toBe("needs-action");
    expect(ev.allDay).toBe(false);
    expect(ev.organizer?.email).toBe("jane@example.com");
    expect(ev.lastMessageId).toBe(3);
    expect(ev.raw).toEqual(EVENT);
  });

  it("calendarEvent reads one event", async () => {
    const { mf } = client((url) => {
      expect(url.pathname).toBe(`${BASE}/7`);
      return json(200, EVENT);
    });
    expect((await mf.inbox(ADDR).calendarEvent(7)).uid).toBe("abc@google.com");
  });

  it("rsvp sends exactly what the caller said and maps the result", async () => {
    const bodies: any[] = [];
    const { mf } = client((url, init) => {
      expect(init.method).toBe("POST");
      expect(url.pathname).toBe(`${BASE}/7/rsvp`);
      bodies.push(JSON.parse(String(init.body)));
      return json(202, { ok: true, event: { ...EVENT, my_status: "accepted" }, message_id: 99, send_status: "queued" });
    });
    const ib = mf.inbox(ADDR);
    const res = await ib.rsvp(7, "accepted", { comment: "See you there" });
    await ib.rsvp(7, "declined");
    expect(bodies).toEqual([{ response: "accepted", comment: "See you there" }, { response: "declined" }]);
    expect(res.ok).toBe(true);
    expect(res.messageId).toBe(99);
    expect(res.event.myStatus).toBe("accepted");
  });

  it("🔒 rsvp is NOT retried — the organizer would get two answers", async () => {
    const { mf, fetchMock } = client(() => json(504, { detail: "gateway timeout" }));
    await expect(mf.inbox(ADDR).rsvp(7, "accepted")).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("message.calendarEvent is parsed, and null without an invitation", async () => {
    const invite = { uid: "abc@google.com", method: "REQUEST", action: "created", event_id: 7,
                     title: "Onboarding call", all_day: false, attendees: [] };
    const { mf } = client(() => json(200, { ok: true, emails: [
      { id: 2, subject: "Invitation", calendar_event: invite },
      { id: 1, subject: "Plain mail" },
    ] }));
    const [withInvite, plain] = await mf.inbox(ADDR).messages();
    expect(withInvite.calendarEvent?.eventId).toBe(7);
    expect(withInvite.calendarEvent?.action).toBe("created");
    expect(withInvite.calendarEvent?.raw).toEqual(invite);
    expect(plain.calendarEvent).toBeNull();
  });
});
