// Calendar surface (plan 379 phase 1) — the Java mirror of the Python/JS calendar tests.
//
// What these prove (behaviour, not shape):
//   * each method hits the exact route, and include_cancelled is on the query string;
//   * rsvp sends exactly what the caller said (no "comment": null when none was given);
//   * rsvp is NEVER retried: a retry after a lost response answers the organizer twice;
//   * Message.calendarEvent() / calendarEventId() read the invitation summary, and are null
//     when a message carries no invitation.
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
}
