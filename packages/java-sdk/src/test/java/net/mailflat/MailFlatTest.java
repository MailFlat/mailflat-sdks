// MailFlat Java SDK tests — an embedded JDK HttpServer fakes /api/v1 responses.
//
// Connected to:
//   - imports from: net.mailflat (SDK), com.sun.net.httpserver, Jackson, JUnit 5
//
// Coverage: create/list/messages/latest/send/delete + waitForOtp (success/timeout/encrypted)
// + error mapping (401/403/404/429) + X-API-Key header + create payload.
package net.mailflat;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class MailFlatTest {

    private static final String ADDR = "signup-test@x7k2m.mailflat.net";
    private static final ObjectMapper M = new ObjectMapper();

    @FunctionalInterface
    interface Responder {
        Resp handle(String method, String path, String body) throws Exception;
    }

    static final class Resp {
        final int status;
        final String body;
        Resp(int status, String body) { this.status = status; this.body = body; }
        static Resp ok(String body) { return new Resp(200, body); }
    }

    private HttpServer server;
    private String baseUrl;
    private volatile Responder responder = (m, p, b) -> Resp.ok("{}");
    // last request capture
    private volatile String lastMethod, lastPath, lastBody, lastApiKey;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            lastMethod = exchange.getRequestMethod();
            lastPath = exchange.getRequestURI().getPath();
            lastBody = new String(exchange.getRequestBody().readAllBytes(), UTF_8);
            lastApiKey = exchange.getRequestHeaders().getFirst("X-API-Key");
            Resp r;
            try {
                r = responder.handle(lastMethod, lastPath, lastBody);
            } catch (Exception e) {
                r = new Resp(500, "{\"error\":\"test handler failed\"}");
            }
            byte[] out = r.body.getBytes(UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(r.status, out.length);
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

    private MailFlat client() {
        return MailFlat.builder().apiKey("mf_test_x").baseUrl(baseUrl).maxRetries(0).build();
    }

    private static JsonNode parse(String s) throws Exception {
        return M.readTree(s);
    }

    // ---------------------------------------------------------------- create
    @Test
    void createReturnsInbox() {
        responder = (m, p, b) -> Resp.ok(
                "{\"ok\":true,\"address\":\"" + ADDR + "\",\"api_key\":\"mf_sk_1\","
                        + "\"name\":\"signup\",\"retention_hours\":2}");
        Inbox inbox = client().create("signup");
        assertEquals(ADDR, inbox.address());
        assertEquals("signup", inbox.name());
        assertEquals(2, inbox.retentionHours());
        assertEquals("mf_sk_1", inbox.apiKey());
        // request shape
        assertEquals("POST", lastMethod);
        assertEquals("/api/v1/inboxes", lastPath);
        assertEquals("mf_test_x", lastApiKey);
    }

    @Test
    void createSendsLabelPayload() throws Exception {
        responder = (m, p, b) -> Resp.ok("{\"ok\":true,\"address\":\"" + ADDR + "\"}");
        client().create("signup");
        JsonNode body = parse(lastBody);
        assertEquals("signup", body.get("label").asText());
    }

    @Test
    void createWithFullOptions() throws Exception {
        responder = (m, p, b) -> Resp.ok("{\"ok\":true,\"address\":\"" + ADDR + "\"}");
        client().create(CreateInboxOptions.builder()
                .prefix("agent-7f3").label("ci").subdomain("x7k2m")
                .domain("klawsearch.com").retentionHours(2).build());
        JsonNode body = parse(lastBody);
        assertEquals("agent-7f3", body.get("prefix").asText());
        assertEquals("ci", body.get("label").asText());
        assertEquals("x7k2m", body.get("subdomain").asText());
        assertEquals("klawsearch.com", body.get("domain").asText());
        assertEquals(2, body.get("retention_hours").asInt());
    }

    // ------------------------------------------------------------------ list
    @Test
    void listReturnsInboxes() {
        responder = (m, p, b) -> Resp.ok(
                "{\"ok\":true,\"inboxes\":[{\"address\":\"" + ADDR + "\"}]}");
        List<Inbox> inboxes = client().list();
        assertEquals(1, inboxes.size());
        assertEquals(ADDR, inboxes.get(0).address());
        assertEquals("GET", lastMethod);
        assertEquals("/api/v1/inboxes", lastPath);
    }

    // --------------------------------------------------------------- messages
    @Test
    void messagesParsed() {
        responder = (m, p, b) -> Resp.ok(
                "{\"ok\":true,\"emails\":[{\"id\":1,\"subject\":\"Hi\",\"otp_code\":\"123456\","
                        + "\"sender\":\"a@b.com\",\"body_text\":\"hello\"}]}");
        List<Message> msgs = client().inbox(ADDR).messages();
        assertEquals(1, msgs.size());
        assertEquals("Hi", msgs.get(0).subject());
        assertEquals("123456", msgs.get(0).otp());
        assertEquals("a@b.com", msgs.get(0).sender());
        assertEquals("/api/v1/inboxes/" + ADDR + "/messages", lastPath);
    }

    @Test
    void latestPresentAndEmpty() {
        responder = (m, p, b) -> Resp.ok("{\"email\":{\"subject\":\"Yo\"}}");
        Optional<Message> present = client().inbox(ADDR).latest();
        assertTrue(present.isPresent());
        assertEquals("Yo", present.get().subject());

        responder = (m, p, b) -> Resp.ok("{\"email\":null}");
        assertTrue(client().inbox(ADDR).latest().isEmpty());
    }

    // ------------------------------------------------------------- waitForOtp
    @Test
    void waitForOtpReturnsCode() {
        responder = (m, p, b) -> Resp.ok("{\"email\":{\"otp_code\":\"987654\"}}");
        assertEquals("987654", client().inbox(ADDR).waitForOtp(5));
        assertEquals("/api/v1/inboxes/" + ADDR + "/latest", lastPath);
    }

    @Test
    void waitForOtpTimesOut() {
        responder = (m, p, b) -> Resp.ok("{\"email\":null}");
        assertThrows(OtpTimeoutException.class, () -> client().inbox(ADDR).waitForOtp(0));
    }

    @Test
    void waitForOtpEncryptedThrows() {
        responder = (m, p, b) -> Resp.ok("{\"encrypted\":true,\"note\":\"E2E inbox\"}");
        EncryptedInboxException ex = assertThrows(
                EncryptedInboxException.class, () -> client().inbox(ADDR).waitForOtp(5));
        assertTrue(ex.getMessage().contains("E2E inbox"));
    }

    // ------------------------------------------------------------------ send
    @Test
    void sendPostsPayload() throws Exception {
        // The real endpoint answers 202 with `queued`, never `status: sent` — the fake used
        // to claim a delivery the server does not promise (B-059: a fake that lies about the
        // contract tests our idea of the server, not the server).
        responder = (m, p, b) -> new Resp(202, "{\"ok\":true,\"queued\":true,\"message_id\":41,"
                + "\"message\":\"Accepted for delivery to x@y.com\"}");
        SendResult res = client().inbox(ADDR).send("x@y.com", "Hi", "Yo");
        assertEquals(41, res.messageId());
        assertTrue(res.queued());
        assertEquals("/api/v1/inboxes/" + ADDR + "/send", lastPath);
        JsonNode body = parse(lastBody);
        assertEquals("x@y.com", body.get("to").asText());
        assertEquals("Hi", body.get("subject").asText());
        assertEquals("Yo", body.get("body").asText());
    }

    // ---------------------------------------------------------------- delete
    @Test
    void deleteInbox() {
        responder = (m, p, b) -> Resp.ok("{\"ok\":true}");
        JsonNode res = client().inbox(ADDR).delete();
        assertTrue(res.get("ok").asBoolean());
        assertEquals("DELETE", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR, lastPath);
    }

    // ----------------------------------------------------------- error mapping
    @Test
    void mapsErrorStatuses() {
        responder = (m, p, b) -> new Resp(401, "{\"detail\":\"Invalid API key\"}");
        AuthenticationException auth = assertThrows(
                AuthenticationException.class, () -> client().create("x"));
        assertEquals(401, auth.statusCode());
        assertTrue(auth.getMessage().contains("Invalid API key"));

        responder = (m, p, b) -> new Resp(403, "{\"detail\":\"Not your inbox\"}");
        assertThrows(PermissionException.class, () -> client().inbox(ADDR).delete());

        responder = (m, p, b) -> new Resp(404, "{\"detail\":\"Nope\"}");
        assertThrows(NotFoundException.class, () -> client().inbox(ADDR).messages());

        responder = (m, p, b) -> new Resp(429, "{\"detail\":\"Slow down\"}");
        assertThrows(RateLimitException.class, () -> client().list());
    }

    // -------------------------------------------------------------- api key
    @Test
    void missingApiKeyThrowsWhenNoEnv() {
        // Only assert the guard when the env var is absent (don't fight the machine's env).
        if (System.getenv("MAILFLAT_API_KEY") == null) {
            assertThrows(MailFlatException.class,
                    () -> MailFlat.builder().apiKey(null).baseUrl(baseUrl).build());
        }
    }
}
