"""Calendar surface (plan 379): list events, read one, RSVP, message.calendar_event, and
phase 2: create / update / cancel invitations this inbox organizes.

What these prove (behaviour, not shape):
  - Each method hits the exact server route, and the query flag is actually on the wire.
  - RSVP sends the body the server expects, and omits `comment` when not given (sending
    `"comment": null` would be accepted too, but the wire should say what the caller said).
  - RSVP is NOT retried on a gateway error: a retry after a lost response answers twice.
  - `Message.calendar_event` is parsed on both the sync and the async message types.
  - Phase 2: create/update/cancel send exactly the given fields (no `null`s, no silently
    dropped typo), are never retried (a retry would invite everyone twice), and the async
    client hits the same routes with the same bodies.

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


# ------------------------------------------------------------ phase 2: invitations
def _recorder(status=202):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        return httpx.Response(status, json={"ok": True, "message_id": 5,
                                            "event": {**EVENT, "source": "outbound"}})
    return seen, handler


def test_create_update_cancel_send_exactly_the_given_fields():
    seen, handler = _recorder()
    ib = make_client(handler).inbox(ADDR)
    ib.create_calendar_event("Intro", "2026-10-06T14:00:00-04:00",
                             attendees=["ali@example.org", {"email": "bea@example.org",
                                                            "optional": True}],
                             duration_minutes=45)
    ib.update_calendar_event(7, start="2026-10-07T14:00:00-04:00")
    ib.cancel_calendar_event(7, message="Something came up")
    ib.cancel_calendar_event(7)
    base = f"/api/v1/inboxes/{ADDR}/calendar/events"
    assert seen == [
        ("POST", base, {"title": "Intro", "start": "2026-10-06T14:00:00-04:00",
                        "attendees": ["ali@example.org",
                                      {"email": "bea@example.org", "optional": True}],
                        "duration_minutes": 45}),
        ("PATCH", f"{base}/7", {"start": "2026-10-07T14:00:00-04:00"}),
        ("POST", f"{base}/7/cancel", {"message": "Something came up"}),
        ("POST", f"{base}/7/cancel", {}),
    ]


def test_update_refuses_a_typo_and_an_empty_change_before_any_request():
    seen, handler = _recorder()
    ib = make_client(handler).inbox(ADDR)
    with pytest.raises(TypeError, match="starts"):
        ib.update_calendar_event(7, starts="2026-10-07T14:00:00Z")
    with pytest.raises(TypeError, match="at least one field"):
        ib.update_calendar_event(7)
    assert seen == []


@pytest.mark.parametrize("call", [
    lambda ib: ib.create_calendar_event("x", "2026-10-06T14:00:00Z", attendees=["a@b.co"]),
    lambda ib: ib.update_calendar_event(7, title="y"),
    lambda ib: ib.cancel_calendar_event(7),
])
def test_invitation_calls_are_not_retried(call):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(504, json={"detail": "gateway timeout"})

    with pytest.raises(Exception):
        call(make_client(handler, max_retries=3).inbox(ADDR))
    assert len(calls) == 1, f"retried {len(calls)} times; every attendee would be emailed again"


def test_async_invitation_methods_match_sync():
    seen, handler = _recorder()

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://mailflat.net")
        async with AsyncMailFlat(api_key="mf_test_x", http_client=http) as mf:
            ib = mf.inbox(ADDR)
            await ib.create_calendar_event("Intro", "2026-10-06T18:00:00Z",
                                           attendees=["ali@example.org"])
            await ib.update_calendar_event(7, title="Moved")
            await ib.cancel_calendar_event(7)

    asyncio.run(run())
    base = f"/api/v1/inboxes/{ADDR}/calendar/events"
    assert seen == [
        ("POST", base, {"title": "Intro", "start": "2026-10-06T18:00:00Z",
                        "attendees": ["ali@example.org"]}),
        ("PATCH", f"{base}/7", {"title": "Moved"}),
        ("POST", f"{base}/7/cancel", {}),
    ]



# ------------------------------------------------------------ phase 3: subscribe link
FEED = {"enabled": True, "feed_url": "https://mailflat.net/api/cal/mfcal_abc.ics",
        "webcal_url": "webcal://mailflat.net/api/cal/mfcal_abc.ics",
        "subscribe_links": {"google": "https://calendar.google.com/calendar/r?cid=x",
                            "apple": "webcal://mailflat.net/api/cal/mfcal_abc.ics",
                            "outlook": "https://outlook.live.com/calendar/0/addfromweb?url=x"}}


def test_calendar_feed_and_rotate_hit_their_routes_sync_and_async():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json=FEED)

    ib = make_client(handler).inbox(ADDR)
    assert ib.calendar_feed()["feed_url"] == FEED["feed_url"]
    assert ib.rotate_calendar_feed()["webcal_url"] == FEED["webcal_url"]

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://mailflat.net")
        async with AsyncMailFlat(api_key="mf_test_x", http_client=http) as mf:
            aib = mf.inbox(ADDR)
            await aib.calendar_feed()
            await aib.rotate_calendar_feed()

    asyncio.run(run())
    base = f"/api/v1/inboxes/{ADDR}/calendar/feed"
    assert seen == [("GET", base), ("POST", f"{base}/rotate")] * 2


def test_rotate_is_retried_because_a_second_rotation_is_harmless():
    # A lost response to rotate leaves the caller without the new link; rotating again only
    # replaces a link nobody has seen yet. Unlike an invitation, retrying emails nobody.
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if len(calls) == 1:
            return httpx.Response(504, json={"detail": "gateway timeout"})
        return httpx.Response(200, json=FEED)

    out = make_client(handler, max_retries=3).inbox(ADDR).rotate_calendar_feed()
    assert out["feed_url"] == FEED["feed_url"] and len(calls) == 2
