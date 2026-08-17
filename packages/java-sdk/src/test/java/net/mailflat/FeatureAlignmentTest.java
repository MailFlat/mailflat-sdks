// Feature alignment (W3b) — the surfaces the other five SDKs already had.
//
// Test discipline carried over from the other packages:
//   * the fake must mimic the REAL contract. A fake that ignores ?direction= would let a
//     client that never sends the filter pass for the wrong reason (that happened in MCP,
//     B-059), so the query string itself is asserted.
//   * reply() is checked by the BODY it sends, not by "no exception was thrown".
//   * header() is exercised with five spellings, because RFC 5322 says case-insensitive
//     but the key on the wire is Message-ID.
//
// Connected to:
//   - imports from: net.mailflat (SDK), com.sun.net.httpserver, JUnit 5
package net.mailflat;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
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
import java.nio.file.Paths;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class FeatureAlignmentTest {

    private static final String ADDR = "signup-test@x7k2m.mailflat.net";
    private static final ObjectMapper M = new ObjectMapper();

    /** One message with every surface populated, as the server actually serializes it. */
    private static final String FULL_EMAIL = "{"
            + "\"id\":42,"
            + "\"sender\":\"bounces+abc@sendgrid.net\","
            + "\"subject\":\"Verify your address\","
            + "\"body_text\":\"code 481592\","
            + "\"body_html\":\"<a href=\\\"https://ex.com/v\\\">v</a>\","
            + "\"otp_code\":\"481592\","
            + "\"direction\":\"in\",\"is_read\":false,\"is_encrypted\":false,"
            + "\"links\":[\"https://ex.com/v\",\"https://ex.com/help\"],"
            + "\"attachments\":[{\"id\":7,\"filename\":\"plan.pdf\","
            + "\"content_type\":\"application/pdf\",\"size_bytes\":24325,\"truncated\":false}],"
            + "\"spam\":{\"score\":0.4,\"is_spam\":false},"
            + "\"headers\":{\"Message-ID\":\"<abc@mail.gmail.com>\","
            + "\"Reply-To\":\"Support Team <support@svc.com>\","
            + "\"From\":\"noreply@svc.com\"}"
            + "}";

    private HttpServer server;
    private String baseUrl;
    private volatile String lastMethod, lastPath, lastQuery, lastBody, lastUserAgent;
    private volatile String responseBody = "{}";
    private volatile String responseType = "application/json";
    private volatile byte[] responseBytes = null;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            lastMethod = exchange.getRequestMethod();
            lastPath = exchange.getRequestURI().getPath();
            lastQuery = exchange.getRequestURI().getQuery();
            lastBody = new String(exchange.getRequestBody().readAllBytes(), UTF_8);
            lastUserAgent = exchange.getRequestHeaders().getFirst("User-Agent");
            byte[] out = responseBytes != null ? responseBytes : responseBody.getBytes(UTF_8);
            exchange.getResponseHeaders().add("Content-Type", responseType);
            exchange.sendResponseHeaders(200, out.length);
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

    private Message oneMessage() {
        responseBody = "{\"email\":" + FULL_EMAIL + "}";
        return client().inbox(ADDR).latest().orElseThrow();
    }

    // ------------------------------------------------------------- direction
    @Test
    void messagesDefaultsToIncoming() {
        responseBody = "{\"emails\":[]}";
        client().inbox(ADDR).messages();
        // Asserting the query string is the point: a fake that ignored it would let a
        // client that never sends the filter pass (B-059).
        assertEquals("direction=in", lastQuery);
    }

    @Test
    void messagesHonoursExplicitDirection() {
        responseBody = "{\"emails\":[]}";
        client().inbox(ADDR).messages(Direction.OUT);
        assertEquals("direction=out", lastQuery);
        client().inbox(ADDR).messages(Direction.ALL);
        assertEquals("direction=all", lastQuery);
    }

    @Test
    void waitForMessageDefaultsToIncoming() {
        // Without the filter a wait right after send() returned the agent's OWN mail and
        // finished instantly with the wrong message.
        responseBody = "{\"email\":" + FULL_EMAIL + "}";
        client().inbox(ADDR).waitForMessage(5);
        assertEquals("direction=in", lastQuery);
    }

    @Test
    void waitForOtpAsksForIncomingOnly() {
        responseBody = "{\"email\":" + FULL_EMAIL + "}";
        assertEquals("481592", client().inbox(ADDR).waitForOtp(5));
        assertEquals("direction=in", lastQuery);
    }

    // ---------------------------------------------------------- message fields
    @Test
    void messageExposesLinksAttachmentsSpamAndHeaders() {
        Message msg = oneMessage();
        assertEquals(List.of("https://ex.com/v", "https://ex.com/help"), msg.links());
        assertEquals(1, msg.attachments().size());
        assertEquals("plan.pdf", msg.attachments().get(0).filename());
        assertEquals(24325, msg.attachments().get(0).sizeBytes());
        assertNotNull(msg.spam());
        assertEquals(0.4, msg.spam().get("score").asDouble());
        assertNotNull(msg.headers());
        assertEquals("<abc@mail.gmail.com>", msg.messageId());
    }

    @Test
    void headersAreNullNotEmptyOnAnEncryptedInbox() {
        // null and empty mean different things: "not stored" vs "stored and empty".
        responseBody = "{\"email\":{\"id\":1,\"is_encrypted\":true}}";
        Message msg = client().inbox(ADDR).latest().orElseThrow();
        assertNull(msg.headers());
        assertNull(msg.messageId());
        assertTrue(msg.links().isEmpty());
    }

    @Test
    void headerLookupIsCaseInsensitiveAcrossSpellings() {
        Message msg = oneMessage();
        for (String spelling : new String[]{
                "Message-ID", "Message-Id", "message-id", "MESSAGE-ID", "message_id"}) {
            assertEquals("<abc@mail.gmail.com>", msg.header(spelling),
                    "header lookup failed for spelling: " + spelling);
        }
    }

    // -------------------------------------------------------- replyToAddress
    @Test
    void replyToAddressPrefersReplyToOverFromAndEnvelope() {
        // .sender() is the ENVELOPE sender (a bounce address here). Replying to it reaches
        // a bounce processor, never a person.
        Message msg = oneMessage();
        assertEquals("bounces+abc@sendgrid.net", msg.sender());
        assertEquals("support@svc.com", msg.replyToAddress());
    }

    @Test
    void replyToAddressFallsBackToFromThenEnvelope() {
        responseBody = "{\"email\":{\"id\":1,\"sender\":\"envelope@x.net\","
                + "\"headers\":{\"From\":\"Real Person <real@svc.com>\"}}}";
        assertEquals("real@svc.com", client().inbox(ADDR).latest().orElseThrow().replyToAddress());

        responseBody = "{\"email\":{\"id\":1,\"sender\":\"envelope@x.net\",\"headers\":{}}}";
        assertEquals("envelope@x.net", client().inbox(ADDR).latest().orElseThrow().replyToAddress());
    }

    @Test
    void replyToAddressIgnoresAMalformedHeader() {
        responseBody = "{\"email\":{\"id\":1,\"sender\":\"envelope@x.net\","
                + "\"headers\":{\"Reply-To\":\"not-an-address\",\"From\":\"ok@svc.com\"}}}";
        assertEquals("ok@svc.com", client().inbox(ADDR).latest().orElseThrow().replyToAddress());
    }

    // ------------------------------------------------------------------ reply
    @Test
    void replySendsToReplyToAddressWithThreadingHeaders() throws Exception {
        Message msg = oneMessage();
        msg.reply("thanks");
        JsonNode body = M.readTree(lastBody);
        // Checking the BODY, not "it did not throw".
        assertEquals("support@svc.com", body.get("to").asText());
        assertEquals("Re: Verify your address", body.get("subject").asText());
        assertEquals("thanks", body.get("body").asText());
        assertEquals("<abc@mail.gmail.com>", body.get("in_reply_to").asText());
        assertEquals("POST", lastMethod);
    }

    @Test
    void replySubjectIsNotDoubledUp() {
        assertEquals("Re: hi", Message.replySubject("hi"));
        assertEquals("Re: hi", Message.replySubject("Re: hi"));
        assertEquals("RE: hi", Message.replySubject("RE: hi"));   // already a reply
        assertEquals("Re:", Message.replySubject(null));
    }

    @Test
    void replySubjectRepairsControlCharactersItDidNotWrite() {
        // A stranger's subject must not make a message permanently unanswerable. Inbound
        // mail is parsed with RFC 2047 encoded-words decoded, so a stored subject can hold a
        // real CRLF; this helper prefixed "Re: " and the server's header guard then rejected
        // it — forever, for that message, triggerable by anyone who can send mail to the
        // inbox. Same rule as the length axis: repair what WE derived, reject what the
        // caller wrote, because only the caller can fix theirs.
        assertEquals("Re: a b", Message.replySubject("a\r\nb"));
        String repaired = Message.replySubject("x\r\nBcc: victim@example.com");
        assertTrue(!repaired.contains("\r") && !repaired.contains("\n"));
        assertTrue(repaired.contains("Bcc: victim@example.com"), "text dropped, not repaired");
        // Tab is horizontal space; it does not split a header line and survives.
        assertTrue(Message.replySubject("a\tb").contains("\t"));
    }

    @Test
    void replyWithoutMessageIdSendsNoInReplyTo() throws Exception {
        // Encrypted inbox: no headers stored. The reply must still go out, just unthreaded.
        responseBody = "{\"email\":{\"id\":1,\"sender\":\"a@b.com\",\"subject\":\"hi\"}}";
        client().inbox(ADDR).latest().orElseThrow().reply("ok");
        JsonNode body = M.readTree(lastBody);
        assertEquals("a@b.com", body.get("to").asText());
        assertTrue(body.get("in_reply_to") == null || body.get("in_reply_to").isNull(),
                "in_reply_to must be omitted when there is no Message-ID");
    }

    // ------------------------------------------------------- markRead / burn
    @Test
    void markReadAndBurnPostToTheRightPaths() {
        Message msg = oneMessage();
        msg.markRead();
        assertEquals("POST", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/messages/42/read", lastPath);

        client().inbox(ADDR).burn();
        assertEquals("POST", lastMethod);
        assertEquals("/api/v1/inboxes/" + ADDR + "/burn", lastPath);
    }

    // --------------------------------------------------------- attachments
    @Test
    void attachmentDownloadReturnsRawBytes() {
        Message msg = oneMessage();
        byte[] pdf = "%PDF-1.4 fake".getBytes(UTF_8);
        responseBytes = pdf;
        responseType = "application/pdf";
        assertArrayEquals(pdf, msg.attachments().get(0).download());
        assertEquals("/api/v1/inboxes/" + ADDR + "/messages/42/attachments/7", lastPath);
    }

    @Test
    void attachmentDownloadOnEncryptedInboxRaisesRatherThanReturningJson() {
        // The server answers with envelope JSON it cannot decrypt. Handing that back as if
        // it were file bytes would write a JSON blob to disk named plan.pdf.
        Message msg = oneMessage();
        responseBody = "{\"is_encrypted\":true,\"filename\":\"plan.pdf\"}";
        responseType = "application/json";
        assertThrows(EncryptedInboxException.class,
                () -> msg.attachments().get(0).download());
    }

    @Test
    void truncatedAttachmentSaysSoInsteadOfReturningEmptyBytes() {
        responseBody = "{\"email\":{\"id\":1,\"attachments\":[{\"id\":9,\"filename\":\"big.zip\","
                + "\"size_bytes\":99999999,\"truncated\":true}]}}";
        Attachment a = client().inbox(ADDR).latest().orElseThrow().attachments().get(0);
        MailFlatException e = assertThrows(MailFlatException.class, a::download);
        assertTrue(e.getMessage().contains("never stored"));
    }

    // -------------------------------------------------- OTP timeout diagnosis
    @Test
    void otpTimeoutDistinguishesNoMailFromNoCode() {
        // "mail came but had no code" and "nothing arrived" are different failures; reported
        // as one message the caller cannot tell a broken signup from an unparsed format.
        responseBody = "{\"email\":null}";
        OtpTimeoutException empty = assertThrows(OtpTimeoutException.class,
                () -> client().inbox(ADDR).waitForOtp(1));
        assertTrue(empty.getMessage().contains("No mail arrived"), empty.getMessage());

        responseBody = "{\"email\":{\"id\":1,\"subject\":\"Welcome\",\"body_text\":\"no code here\"}}";
        OtpTimeoutException noCode = assertThrows(OtpTimeoutException.class,
                () -> client().inbox(ADDR).waitForOtp(1));
        assertTrue(noCode.getMessage().contains("no OTP could be extracted"), noCode.getMessage());
        // The mail is quoted so the caller can see the code with their own eyes.
        assertTrue(noCode.getMessage().contains("Welcome"), noCode.getMessage());
        assertTrue(noCode.getMessage().contains("no code here"), noCode.getMessage());
    }

    // ------------------------------------------------------------- version
    @Test
    void userAgentCarriesTheVersion() {
        responseBody = "{\"emails\":[]}";
        client().inbox(ADDR).messages();
        assertNotNull(lastUserAgent);
        assertTrue(lastUserAgent.startsWith("mailflat-java/"), lastUserAgent);
    }

    @Test
    void reportedVersionMatchesPomExactly() throws Exception {
        // B-052: four packages once reported a version they had not published, because it
        // was written by hand in a second place. pom.xml is the only source; this locks it.
        String pom = Files.readString(Paths.get("pom.xml"));
        int start = pom.indexOf("<version>");
        String pomVersion = pom.substring(start + 9, pom.indexOf("</version>", start)).trim();
        assertEquals(pomVersion, MailFlat.version(),
                "runtime version drifted from pom.xml; never write the version by hand");
    }
}
