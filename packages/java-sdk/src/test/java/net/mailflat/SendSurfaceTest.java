// The 0.4.0 send surface: attachments, cc/bcc, the 202 result, message(id), waitUntilSent.
//
// The fake server here answers what the real one answers — 202 with `queued`/`message_id` for
// a send, `{"email": {...}}` for a single message. A fake that returns a friendlier shape than
// production tests our idea of the server instead of the server (B-059).
//
// Connected to:
//   - imports from: net.mailflat (SDK), com.sun.net.httpserver, Jackson, JUnit 5
//
// Coverage: attachment encoding (path/bytes/type guessing) · cc/bcc on the wire · empty fields
// omitted · SendResult · message(id) · waitUntilSent (sent/unsigned/failed/timeout) · reply
// carrying the same surface while keeping its threading headers.
package net.mailflat;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SendSurfaceTest {

    private static final String ADDR = "signup-test@x7k2m.mailflat.net";
    private static final ObjectMapper M = new ObjectMapper();

    @FunctionalInterface
    interface Responder {
        String handle(String method, String path) throws Exception;
    }

    private HttpServer server;
    private String baseUrl;
    private volatile Responder responder = (m, p) -> "{}";
    private volatile int status = 200;
    private volatile String lastPath;
    private volatile String lastBody;
    private final AtomicInteger calls = new AtomicInteger();
    private final List<String> paths = new ArrayList<>();

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            calls.incrementAndGet();
            lastPath = exchange.getRequestURI().getPath();
            paths.add(lastPath);
            lastBody = new String(exchange.getRequestBody().readAllBytes(), UTF_8);
            String out;
            try {
                out = responder.handle(exchange.getRequestMethod(), lastPath);
            } catch (Exception e) {
                out = "{\"error\":\"test handler failed\"}";
            }
            byte[] bytes = out.getBytes(UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(bytes);
            }
        });
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    private Inbox inbox() {
        return MailFlat.builder().apiKey("mf_test_x").baseUrl(baseUrl).maxRetries(0)
                .build().inbox(ADDR);
    }

    private JsonNode body() throws Exception {
        return M.readTree(lastBody);
    }

    /** What the real endpoint answers: 202 Accepted, not 200, and no delivery claim. */
    private void acceptSend(int messageId) {
        status = 202;
        responder = (m, p) -> "{\"ok\":true,\"queued\":true,\"message_id\":" + messageId
                + ",\"message\":\"Accepted for delivery to a@b.com\"}";
    }

    // ------------------------------------------------------------- attachments
    @Test
    void attachmentFromPathCarriesNameTypeAndBytes(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("invoice.pdf");
        byte[] content = "%PDF-1.4 not really a pdf".getBytes(UTF_8);
        Files.write(file, content);
        acceptSend(1);

        inbox().send("a@b.com", SendOptions.builder().body("see attached").attach(file).build());

        JsonNode att = body().get("attachments").get(0);
        assertEquals("invoice.pdf", att.get("filename").asText());
        // Guessed from the extension, not probed: Files.probeContentType returns null for
        // .pdf on macOS, so a probe-first implementation would send octet-stream there and
        // application/pdf on Linux — the recipient sees a nameless blob on one of them.
        assertEquals("application/pdf", att.get("content_type").asText());
        assertEquals(new String(content, UTF_8),
                new String(Base64.getDecoder().decode(att.get("content_b64").asText()), UTF_8));
    }

    @Test
    void attachmentFromBytesNeedsNoFile() throws Exception {
        acceptSend(1);
        inbox().send("a@b.com", SendOptions.builder()
                .attach("report.csv", "a,b\n1,2\n".getBytes(UTF_8)).build());

        JsonNode att = body().get("attachments").get(0);
        assertEquals("report.csv", att.get("filename").asText());
        assertEquals("text/csv", att.get("content_type").asText());
    }

    @Test
    void unknownExtensionFallsBackInsteadOfGuessingWrong() throws Exception {
        acceptSend(1);
        inbox().send("a@b.com", SendOptions.builder()
                .attach("dump.zzz", new byte[]{1, 2, 3}).build());
        assertEquals("application/octet-stream",
                body().get("attachments").get(0).get("content_type").asText());
    }

    /**
     * A file on disk must get the same type on every machine. This one is the regression:
     * .bin used to fall through to Files.probeContentType(), which answers
     * application/macbinary on macOS and something else on a Linux CI runner — so a test
     * written against the type passed locally and failed in CI.
     */
    @Test
    void aFileOnDiskGetsTheSameTypeOnEveryPlatform(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("payload.bin");
        Files.write(file, new byte[]{0, 1, 2, 3});
        acceptSend(1);

        inbox().send("a@b.com", SendOptions.builder().attach(file).build());

        assertEquals("application/octet-stream",
                body().get("attachments").get(0).get("content_type").asText());
    }

    /**
     * The disk path and the bytes path must agree: nothing about the file itself — only its
     * name — may influence the type, or the two overloads drift apart per platform.
     */
    @Test
    void pathAndBytesOverloadsGuessIdentically(@TempDir Path dir) throws Exception {
        for (String name : new String[]{"a.pdf", "b.csv", "c.bin", "d.zzz", "noextension"}) {
            Path file = dir.resolve(name);
            Files.write(file, new byte[]{9});
            assertEquals(OutgoingAttachment.of(name, new byte[]{9}).contentType(),
                    OutgoingAttachment.of(file).contentType(), name);
        }
    }

    @Test
    void explicitContentTypeWinsOverTheGuess() throws Exception {
        acceptSend(1);
        inbox().send("a@b.com", SendOptions.builder()
                .attach(OutgoingAttachment.of("data.txt", "application/json",
                        "{}".getBytes(UTF_8))).build());
        assertEquals("application/json",
                body().get("attachments").get(0).get("content_type").asText());
    }

    @Test
    void anUnreadableFileFailsBeforeAnythingIsSent(@TempDir Path dir) {
        Path missing = dir.resolve("does-not-exist.pdf");
        MailFlatException ex = assertThrows(MailFlatException.class,
                () -> SendOptions.builder().attach(missing).build());
        assertTrue(ex.getMessage().contains("does-not-exist.pdf"));
        // Nothing left the process: a half-built payload must not reach the server.
        assertEquals(0, calls.get());
    }

    @Test
    void aTwoMegabyteFileIsEncodedWhole(@TempDir Path dir) throws Exception {
        // The JS SDK crashed here (btoa(String.fromCharCode(...bytes)) blows the stack on a
        // real file). Java has no such limit, but the size is what the feature is for.
        byte[] big = new byte[2 * 1024 * 1024];
        for (int i = 0; i < big.length; i++) {
            big[i] = (byte) (i % 251);
        }
        Path file = dir.resolve("big.bin");
        Files.write(file, big);
        acceptSend(1);

        inbox().send("a@b.com", SendOptions.builder().attach(file).build());

        byte[] decoded = Base64.getDecoder()
                .decode(body().get("attachments").get(0).get("content_b64").asText());
        assertEquals(big.length, decoded.length);
        assertEquals(big[big.length - 1], decoded[decoded.length - 1]);
    }

    // ----------------------------------------------------------------- cc/bcc
    @Test
    void ccAndBccGoOnTheWire() throws Exception {
        acceptSend(1);
        inbox().send("a@b.com", SendOptions.builder()
                .cc("cc1@x.com", "cc2@x.com").bcc("hidden@x.com").build());

        JsonNode b = body();
        assertEquals(2, b.get("cc").size());
        assertEquals("cc1@x.com", b.get("cc").get(0).asText());
        assertEquals("hidden@x.com", b.get("bcc").get(0).asText());
    }

    @Test
    void unusedFieldsAreOmittedRatherThanSentEmpty() throws Exception {
        acceptSend(1);
        inbox().send("a@b.com", "Hi", "Yo");

        JsonNode b = body();
        assertFalse(b.has("cc"), "an empty cc must not be sent");
        assertFalse(b.has("bcc"), "an empty bcc must not be sent");
        assertFalse(b.has("attachments"), "an empty attachments list must not be sent");
        assertFalse(b.has("html"), "html must be absent, not null");
        assertFalse(b.has("in_reply_to"), "in_reply_to must be absent, not null");
    }

    // ------------------------------------------------------------- SendResult
    @Test
    void sendResultExposesTheQueuedId() {
        acceptSend(77);
        SendResult r = inbox().send("a@b.com", SendOptions.builder().body("hi").build());
        assertEquals(77, r.messageId());
        assertTrue(r.queued());
        assertTrue(r.message().startsWith("Accepted"));
    }

    @Test
    void aResultWithoutAnIdSaysSoInsteadOfThrowingNullPointer() {
        status = 202;
        responder = (m, p) -> "{\"ok\":true,\"queued\":true}";
        SendResult r = inbox().send("a@b.com", SendOptions.builder().build());
        assertNull(r.messageId());
        MailFlatException ex = assertThrows(MailFlatException.class,
                () -> inbox().waitUntilSent(r));
        assertTrue(ex.getMessage().contains("message_id"));
    }

    // ---------------------------------------------------------- message(id)
    @Test
    void messageByIdHitsTheSingleMessageEndpoint() {
        responder = (m, p) -> "{\"email\":{\"id\":9,\"subject\":\"Invoice\","
                + "\"direction\":\"out\",\"send_status\":\"sent\"}}";
        Message msg = inbox().message(9);
        assertEquals(9, msg.id());
        assertEquals("sent", msg.sendStatus());
        assertEquals("/api/v1/inboxes/" + ADDR + "/messages/9", lastPath);
    }

    // -------------------------------------------------------- waitUntilSent
    @Test
    void waitUntilSentReturnsOnceDelivered() {
        // queued on the first look, sent on the second: proves it actually polls.
        responder = (m, p) -> calls.get() <= 1
                ? "{\"email\":{\"id\":5,\"send_status\":\"queued\"}}"
                : "{\"email\":{\"id\":5,\"send_status\":\"sent\"}}";
        Message msg = inbox().waitUntilSent(5, 30);
        assertEquals("sent", msg.sendStatus());
        assertTrue(calls.get() >= 2, "it returned without polling twice");
    }

    @Test
    void unsignedCountsAsDelivered() {
        // The mail went out without a DKIM signature. Delivered is delivered; treating this
        // as "not done yet" would hang until the timeout on every unsigned send.
        responder = (m, p) -> "{\"email\":{\"id\":5,\"send_status\":\"unsigned\"}}";
        // 1s, not 30: if "unsigned" stops counting as done this must fail fast rather than
        // sit there for half a minute pretending to be slow.
        assertEquals("unsigned", inbox().waitUntilSent(5, 1).sendStatus());
    }

    @Test
    void permanentFailureRaisesWithTheReason() {
        responder = (m, p) -> "{\"email\":{\"id\":5,\"send_status\":\"failed\","
                + "\"send_error\":\"550 mailbox unavailable\"}}";
        SendFailedException ex = assertThrows(SendFailedException.class,
                () -> inbox().waitUntilSent(5, 30));
        assertTrue(ex.getMessage().contains("550 mailbox unavailable"));
        assertEquals(5, ex.messageId());
        assertEquals("failed", ex.sendStatus());
    }

    @Test
    void stillQueuedAtTheDeadlineIsATimeoutNotAFailure() {
        responder = (m, p) -> "{\"email\":{\"id\":5,\"send_status\":\"queued\"}}";
        // Caught as the shared supertype on purpose: what is asserted is WHICH of the two a
        // caller gets. The distinction is the whole point — the queue is still retrying, so
        // someone who reads this as "it never went out" and sends again delivers it twice.
        SendException ex = assertThrows(SendException.class, () -> inbox().waitUntilSent(5, 0));
        assertTrue(ex instanceof SendTimeoutException);
        assertFalse(ex instanceof SendFailedException);
        assertEquals("queued", ex.sendStatus());
        assertTrue(ex.getMessage().contains("still be delivered"));
    }

    // ---------------------------------------------------------------- reply
    @Test
    void replyCarriesAttachmentsAndKeepsItsThreadingHeaders() throws Exception {
        responder = (m, p) -> p.endsWith("/send")
                ? "{\"ok\":true,\"queued\":true,\"message_id\":8}"
                : "{\"email\":{\"id\":3,\"subject\":\"Invoice due\",\"from\":\"billing@acme.com\","
                        + "\"headers\":{\"Message-ID\":\"<abc@acme.com>\","
                        + "\"From\":\"billing@acme.com\"}}}";
        status = 200;
        Message msg = inbox().message(3);
        status = 202;

        msg.reply(SendOptions.builder()
                .body("Paid, receipt attached.")
                .cc("accounts@acme.com")
                .attach("receipt.pdf", "pdf bytes".getBytes(UTF_8))
                .build());

        JsonNode b = body();
        assertEquals("billing@acme.com", b.get("to").asText());
        assertEquals("Re: Invoice due", b.get("subject").asText());
        assertEquals("<abc@acme.com>", b.get("in_reply_to").asText(),
                "a reply that loses In-Reply-To starts a new conversation");
        assertEquals("receipt.pdf", b.get("attachments").get(0).get("filename").asText());
        assertEquals("accounts@acme.com", b.get("cc").get(0).asText());
    }

    @Test
    void replyKeepsItsOwnThreadingEvenIfTheOptionsSaySomethingElse() throws Exception {
        responder = (m, p) -> p.endsWith("/send")
                ? "{\"ok\":true,\"queued\":true,\"message_id\":8}"
                : "{\"email\":{\"id\":3,\"subject\":\"Invoice\",\"from\":\"billing@acme.com\","
                        + "\"headers\":{\"Message-ID\":\"<real@acme.com>\","
                        + "\"From\":\"billing@acme.com\"}}}";
        Message msg = inbox().message(3);
        status = 202;

        msg.reply(SendOptions.builder().body("hi").inReplyTo("<wrong@example.com>").build());

        assertEquals("<real@acme.com>", body().get("in_reply_to").asText());
    }

    @Test
    void anExplicitSubjectStillWinsOverTheDerivedOne() throws Exception {
        responder = (m, p) -> p.endsWith("/send")
                ? "{\"ok\":true,\"queued\":true,\"message_id\":8}"
                : "{\"email\":{\"id\":3,\"subject\":\"Invoice\",\"from\":\"b@acme.com\","
                        + "\"headers\":{\"From\":\"b@acme.com\"}}}";
        Message msg = inbox().message(3);
        status = 202;

        msg.reply(SendOptions.builder().subject("Payment confirmation").build());

        assertEquals("Payment confirmation", body().get("subject").asText());
    }

    // ------------------------------------------------------------ retry safety
    @Test
    void theOptionsPathIsAlsoNeverRetried() {
        // B-066 was closed on the old signature. The new one is a second POST path into the
        // same client, and a send that retries delivers the mail twice however it was built.
        status = 503;
        responder = (m, p) -> "{}";
        MailFlat client = MailFlat.builder().apiKey("mf_test_x").baseUrl(baseUrl)
                .maxRetries(2).build();
        assertThrows(MailFlatException.class, () -> client.inbox(ADDR).send("a@b.com",
                SendOptions.builder().body("hi").attach("a.txt", "x".getBytes(UTF_8)).build()));
        assertEquals(1, calls.get(), "send(SendOptions) was retried; that delivers twice");
    }

    @Test
    void waitUntilSentAcceptsTheResultDirectly() {
        acceptSend(12);
        SendResult r = inbox().send("a@b.com", SendOptions.builder().body("hi").build());
        status = 200;
        responder = (m, p) -> "{\"email\":{\"id\":12,\"send_status\":\"sent\"}}";
        Message msg = inbox().waitUntilSent(r);
        assertNotNull(msg);
        assertEquals("/api/v1/inboxes/" + ADDR + "/messages/12", lastPath);
    }
}
