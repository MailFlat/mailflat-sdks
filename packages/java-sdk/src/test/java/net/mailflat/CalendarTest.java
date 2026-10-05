// Calendar surface (plan 379 phases 1-2) — the Java mirror of the Python/JS calendar tests.
//
// What these prove (behaviour, not shape):
//   * each method hits the exact route, and include_cancelled is on the query string;
//   * rsvp sends exactly what the caller said (no "comment": null when none was given);
//   * rsvp is NEVER retried: a retry after a lost response answers the organizer twice;
//   * Message.calendarEvent() / calendarEventId() read the invitation summary, and are null
//     when a message carries no invitation;
//   * phase 2: create/update/cancel send exactly the builder's fields in wire format, an
//     update refuses attendees or an empty change before any request, and none of them is
//     retried (a retry would invite everyone twice).
//
// Connected to:
//   - imports from: net.mailflat (SDK), com.sun.net.httpserver, JUnit 5
package net.mailflat;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CalendarTest {

    private static final String ADDR = "agent@x7k2m.mailflat.net";
    private static final String BASE = "/api/v1/inboxes/" + ADDR + "/calendar/events";
    private static final ObjectMapper M = new ObjectMapper();
    private static final String EVENT = "{\"id\":7,\"uid\":\"abc@google.com\",\"title\":\"Onboarding call\","
            + "\"start\":\"2026-10-01T14:00:00Z\",\"end\":\"2026-10-01T14:30:00Z\",\"all_day\":false,"
            + "\"status\":\"confirmed\",\"my_status\":\"needs-action\",\"sequence\":1,"
            + "\"organizer\":{\"email\":\"jane@example.com\",\"name\":\"Jane\"},"
            + "\"attendees\":[{\"email\":\"" + ADDR + "\",\"status\":\"needs-action\"}],"
            + "\"last_message_id\":3}";

    private HttpServer server;
    private String baseUrl;
    private final AtomicInteger calls = new AtomicInteger();
    private volatile String lastMethod, lastPath, lastQuery, lastBody;
    private volatile String responseBody = "{}";
    private volatile int status = 200;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            calls.incrementAndGet();
            lastMethod = exchange.getRequestMethod();
            lastPath = exchange.getRequestURI().getPath();
            lastQuery = exchange.getRequestURI().getQuery();
            lastBody = new String(exchange.getRequestBody().readAllBytes(), UTF_8);
            byte[] out = responseBody.getBytes(UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(out);
            }
        });
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    /** maxRetries(2): with 0 the no-retry guarantee would be invisible. */
    private Inbox inbox() {
        return MailFlat.builder().apiKey("mf_test_x").baseUrl(baseUrl).maxRetries(2).build().inbox(ADDR);
    }

    @Test
    void calendarEventsHitsTheRouteAndSendsTheFlag() {
        responseBody = "{\"events\":[" + EVENT + "]}";
        List<CalendarEvent> events = inbox().calendarEvents();
        assertEquals("GET", lastMethod);
        assertEquals(BASE, lastPath);
        assertEquals("include_cancelled=false", lastQuery);
        inbox().calendarEvents(true);
        assertEquals("include_cancelled=true", lastQuery);

        CalendarEvent ev = events.get(0);
        assertEquals(7, ev.id());
        assertEquals("2026-10-01T14:00:00Z", ev.start());
        assertEquals("needs-action", ev.myStatus());
        assertEquals("jane@example.com", ev.organizerEmail());
        assertEquals(1, ev.sequence());
        assertEquals(3, ev.lastMessageId());
        assertFalse(ev.isCancelled());
        assertFalse(ev.allDay());
    }

    @Test
    void calendarEventReadsOne() {
        responseBody = EVENT;
        assertEquals("abc@google.com", inbox().calendarEvent(7).uid());
        assertEquals(BASE + "/7", lastPath);
    }

    @Test
    void rsvpSendsExactlyWhatTheCallerSaid() throws Exception {
        status = 202;
        responseBody = "{\"ok\":true,\"message_id\":99,\"send_status\":\"queued\",\"event\":" + EVENT + "}";
        JsonNode res = inbox().rsvp(7, RsvpResponse.ACCEPTED, "See you there");
        assertEquals("POST", lastMethod);
        assertEquals(BASE + "/7/rsvp", lastPath);
        assertEquals(M.readTree("{\"response\":\"accepted\",\"comment\":\"See you there\"}"), M.readTree(lastBody));
        assertEquals(99, res.get("message_id").asInt());

        inbox().rsvp(7, RsvpResponse.TENTATIVE);
        assertEquals(M.readTree("{\"response\":\"tentative\"}"), M.readTree(lastBody));
    }

    @Test
    void rsvpIsNeverRetried() {
        status = 503;
        responseBody = "{\"detail\":\"unavailable\"}";
        assertThrows(MailFlatException.class, () -> inbox().rsvp(7, RsvpResponse.DECLINED));
        assertEquals(1, calls.get(), "rsvp() was retried; the organizer would get two answers");
    }

    @Test
    void rsvpRequiresAnAnswer() {
        assertThrows(IllegalArgumentException.class, () -> inbox().rsvp(7, null));
        assertEquals(0, calls.get(), "an rsvp without an answer reached the server");
    }

    @Test
    void messageCarriesTheInvitationSummary() {
        responseBody = "{\"emails\":["
                + "{\"id\":2,\"calendar_event\":{\"uid\":\"abc@google.com\",\"method\":\"REQUEST\","
                + "\"action\":\"created\",\"event_id\":7}},"
                + "{\"id\":1,\"subject\":\"plain\"}]}";
        List<Message> msgs = inbox().messages();
        assertEquals(7, msgs.get(0).calendarEventId());
        assertEquals("created", msgs.get(0).calendarEvent().get("action").asText());
        assertNull(msgs.get(1).calendarEvent());
        assertNull(msgs.get(1).calendarEventId());
        assertTrue(msgs.get(0).raw().has("calendar_event"));
    }

    // ------------------------------------------------------------ phase 2: invitations
    private static final String OUT = "{\"ok\":true,\"message_id\":5,\"send_status\":\"queued\","
            + "\"event\":{\"id\":8,\"uid\":\"n@mailflat.net\",\"status\":\"confirmed\","
            + "\"source\":\"outbound\",\"organizer_verified\":true}}";

    @Test
    void createSendsTheBuilderFieldsInWireFormat() throws Exception {
        status = 202;
        responseBody = OUT;
        JsonNode res = inbox().createCalendarEvent(CalendarEventOptions.builder()
                .title("Intro").start("2026-10-06T14:00:00-04:00").durationMinutes(45)
                .attendee("ali@example.org").attendee("bea@example.org", "Bea", true)
                .inReplyTo("<m1@example.org>").build());
        assertEquals("POST", lastMethod);
        assertEquals(BASE, lastPath);
        assertEquals(M.readTree("{\"title\":\"Intro\",\"start\":\"2026-10-06T14:00:00-04:00\","
                + "\"duration_minutes\":45,\"attendees\":[\"ali@example.org\","
                + "{\"email\":\"bea@example.org\",\"name\":\"Bea\",\"optional\":true}],"
                + "\"in_reply_to\":\"<m1@example.org>\"}"), M.readTree(lastBody));
        assertEquals(5, res.get("message_id").asInt());
        CalendarEvent ev = CalendarEvent.fromJson(res.get("event"));
        assertEquals("outbound", ev.source());
        assertTrue(ev.organizerVerified());
    }

    @Test
    void updatePatchesOnlyTheChangesAndCancelPosts() throws Exception {
        status = 202;
        responseBody = OUT;
        inbox().updateCalendarEvent(8, CalendarEventOptions.builder().timezone("Europe/Istanbul").build());
        assertEquals("PATCH", lastMethod);
        assertEquals(BASE + "/8", lastPath);
        assertEquals(M.readTree("{\"timezone\":\"Europe/Istanbul\"}"), M.readTree(lastBody));

        inbox().cancelCalendarEvent(8, "Something came up");
        assertEquals("POST", lastMethod);
        assertEquals(BASE + "/8/cancel", lastPath);
        assertEquals(M.readTree("{\"message\":\"Something came up\"}"), M.readTree(lastBody));
        inbox().cancelCalendarEvent(8);
        assertEquals(M.readTree("{}"), M.readTree(lastBody));
    }

    @Test
    void invalidCallsAreRefusedBeforeAnyRequest() {
        assertThrows(IllegalArgumentException.class, () -> inbox().updateCalendarEvent(8,
                CalendarEventOptions.builder().build()));
        assertThrows(IllegalArgumentException.class, () -> inbox().updateCalendarEvent(8,
                CalendarEventOptions.builder().title("x").attendee("c@d.co").build()));
        assertThrows(IllegalArgumentException.class, () -> inbox().createCalendarEvent(
                CalendarEventOptions.builder().title("x").start("2026-10-06T14:00:00Z").build()));
        assertEquals(0, calls.get(), "an invalid call reached the server");
    }

    @Test
    void invitationCallsAreNeverRetried() {
        status = 503;
        responseBody = "{\"detail\":\"unavailable\"}";
        assertThrows(MailFlatException.class, () -> inbox().createCalendarEvent(CalendarEventOptions
                .builder().title("x").start("2026-10-06T14:00:00Z").attendee("a@b.co").build()));
        assertThrows(MailFlatException.class, () -> inbox().updateCalendarEvent(8,
                CalendarEventOptions.builder().title("y").build()));
        assertThrows(MailFlatException.class, () -> inbox().cancelCalendarEvent(8));
        assertEquals(3, calls.get(), "an invitation call was retried; attendees would be emailed twice");
    }

    // Phase 3: the read-only subscribe link a human adds to their calendar app.
    @Test
    void calendarFeedAndRotateHitTheirRoutes() throws Exception {
        responseBody = "{\"enabled\":true,\"feed_url\":\"https://mailflat.net/api/cal/mfcal_abc.ics\"}";
        JsonNode feed = inbox().calendarFeed();
        assertEquals("GET", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/calendar/feed", lastPath);
        assertEquals("https://mailflat.net/api/cal/mfcal_abc.ics", feed.get("feed_url").asText());
        inbox().rotateCalendarFeed();
        assertEquals("POST", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/calendar/feed/rotate", lastPath);
    }
    // The owner's calendar copy: read the switch, set it with a PUT.
    @Test
    void calendarCopyReadsAndSetsOnItsRoute() throws Exception {
        responseBody = "{\"enabled\":true,\"email\":\"owner@example.com\",\"available\":true,\"blocked\":false}";
        JsonNode state = inbox().calendarCopy();
        assertEquals("GET", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/calendar/copy", lastPath);
        assertEquals("owner@example.com", state.get("email").asText());
        inbox().setCalendarCopy(false);
        assertEquals("PUT", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/calendar/copy", lastPath);
        assertEquals(false, M.readTree(lastBody).get("enabled").asBoolean());
    }
}
