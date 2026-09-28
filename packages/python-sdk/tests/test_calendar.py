"""Calendar surface (plan 379 phase 1): list events, read one, RSVP, message.calendar_event.

What these prove (behaviour, not shape):
  - Each method hits the exact server route, and the query flag is actually on the wire.
  - RSVP sends the body the server expects, and omits `comment` when not given (sending
    `"comment": null` would be accepted too, but the wire should say what the caller said).
  - RSVP is NOT retried on a gateway error: a retry after a lost response answers twice.
  - `Message.calendar_event` is parsed on both the sync and the async message types.

Connected to:
  - imports from: mailflat (SDK), httpx
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mailflat import MailFlat
from mailflat.aio import AsyncMailFlat

ADDR = "agent@x7k2m.mailflat.net"
EVENT = {"id": 7, "uid": "abc@google.com", "title": "Onboarding call",
         "start": "2026-10-01T14:00:00Z", "my_status": "needs-action", "status": "confirmed"}


def make_client(handler, **kw) -> MailFlat:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://mailflat.net",
                        headers={"X-API-Key": "mf_test_x"})
    return MailFlat(api_key="mf_test_x", http_client=http, **kw)


def test_calendar_events_hits_route_and_sends_the_flag():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"events": [EVENT]})

    ib = make_client(handler).inbox(ADDR)
    assert ib.calendar_events() == [EVENT]
    ib.calendar_events(include_cancelled=True)
    path = f"/api/v1/inboxes/{ADDR}/calendar/events"
    assert seen == [("GET", path, {"include_cancelled": "false"}),
                    ("GET", path, {"include_cancelled": "true"})]


def test_calendar_event_reads_one():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/calendar/events/7"
        return httpx.Response(200, json=EVENT)

    assert make_client(handler).inbox(ADDR).calendar_event(7)["uid"] == "abc@google.com"


def test_rsvp_sends_the_expected_body():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == f"/api/v1/inboxes/{ADDR}/calendar/events/7/rsvp"
        bodies.append(json.loads(req.content))
        return httpx.Response(202, json={"ok": True, "event": {**EVENT, "my_status": "accepted"},
                                         "message_id": 99, "send_status": "queued"})

    ib = make_client(handler).inbox(ADDR)
    res = ib.rsvp(7, "accepted", comment="See you there")
    assert res["message_id"] == 99 and res["event"]["my_status"] == "accepted"
    ib.rsvp(7, "declined")
    assert bodies == [{"response": "accepted", "comment": "See you there"},
                      {"response": "declined"}]


def test_rsvp_is_not_retried():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(504, json={"detail": "gateway timeout"})

    with pytest.raises(Exception):
        make_client(handler, max_retries=3).inbox(ADDR).rsvp(7, "accepted")
    assert len(calls) == 1, f"rsvp retried {len(calls)} times; the organizer would get two answers"


def test_message_carries_calendar_event_sync_and_async():
    summary = {"uid": "abc@google.com", "method": "REQUEST", "action": "created", "event_id": 7}
    email = {"id": 1, "subject": "Invitation", "calendar_event": summary}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "email": email, "emails": [email]})

    msg = make_client(handler).inbox(ADDR).latest()
    assert msg.calendar_event == summary

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://mailflat.net")
        async with AsyncMailFlat(api_key="mf_test_x", http_client=http) as mf:
            return await mf.inbox(ADDR).latest()

    assert asyncio.run(run()).calendar_event == summary


def test_async_calendar_methods_hit_the_same_routes():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(202, json={"ok": True, "message_id": 1})
        return httpx.Response(200, json={"events": [EVENT], **EVENT})

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://mailflat.net")
        async with AsyncMailFlat(api_key="mf_test_x", http_client=http) as mf:
            ib = mf.inbox(ADDR)
            await ib.calendar_events()
            await ib.calendar_event(7)
            await ib.rsvp(7, "tentative")

    asyncio.run(run())
    base = f"/api/v1/inboxes/{ADDR}/calendar/events"
    assert seen == [("GET", base), ("GET", f"{base}/7"), ("POST", f"{base}/7/rsvp")]
