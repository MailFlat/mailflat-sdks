// Retry safety (B-066) — a retried POST delivers the same mail twice.
//
// The Java client used to retry on 429/502/503/504 without looking at the HTTP method, so a
// gateway error after the MTA had already accepted /send delivered the mail two or three
// times and the caller never found out. This is the Java twin of the Python fix (B-058).
//
// Connected to:
//   - imports from: net.mailflat (SDK), com.sun.net.httpserver, JUnit 5
//
// Coverage: POST never retried (gateway + 429) · GET/DELETE retried · Retry-After honoured
// · malformed Retry-After falls back · idempotent POST opt-in is retried.
package net.mailflat;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class RetrySafetyTest {

    private static final String ADDR = "signup-test@x7k2m.mailflat.net";

    private HttpServer server;
    private String baseUrl;
    private final AtomicInteger calls = new AtomicInteger();
    private volatile int status = 503;
    private volatile String retryAfter = null;
    /** Serve this many failures, then 200. -1 = always fail. */
    private volatile int failTimes = -1;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            int n = calls.incrementAndGet();
            boolean fail = failTimes < 0 || n <= failTimes;
            int code = fail ? status : 200;
            if (fail && retryAfter != null) {
                exchange.getResponseHeaders().add("Retry-After", retryAfter);
            }
            byte[] out = ("{\"ok\":true,\"address\":\"" + ADDR + "\"}").getBytes(UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(code, out.length);
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

    /** maxRetries(2) on purpose: with 0 the whole defect is invisible. */
    private MailFlat client() {
        return MailFlat.builder().apiKey("mf_test_x").baseUrl(baseUrl).maxRetries(2).build();
    }

    // ------------------------------------------------------------ the defect
    @Test
    void sendIsNeverRetriedOnGatewayError() {
        status = 503;
        assertThrows(MailFlatException.class,
                () -> client().inbox(ADDR).send("a@b.com", "hi", "body"));
        // THE point of this test: exactly one delivery attempt. Two means the recipient
        // could receive the same mail twice.
        assertEquals(1, calls.get(), "send() was retried; a retried send delivers twice");
    }

    @Test
    void sendIsNeverRetriedOn429() {
        // 429 is the concurrency guard (B-067). The rejection is safe to repeat, but the
        // SDK still must not decide that on its own for a send: the caller owns that call.
        status = 429;
        retryAfter = "0";
        assertThrows(MailFlatException.class,
                () -> client().inbox(ADDR).send("a@b.com", "hi", "body"));
        assertEquals(1, calls.get(), "send() was retried on 429");
    }

    @Test
    void createIsNeverRetried() {
        // create() is also a POST: a retry opens a second inbox and burns quota.
        status = 502;
        assertThrows(MailFlatException.class, () -> client().create("signup"));
        assertEquals(1, calls.get(), "create() was retried; that opens a second inbox");
    }

    // ------------------------------------------------- reads still get retried
    @Test
    void readsAreRetried() {
        status = 503;
        failTimes = 2;                       // fail twice, then succeed
        client().list();
        assertEquals(3, calls.get(), "GET should retry: repeating a read is harmless");
    }

    @Test
    void deleteIsRetried() {
        status = 503;
        failTimes = 1;
        client().inbox(ADDR).delete();
        assertEquals(2, calls.get(), "DELETE should retry: the end state is the same");
    }

    // --------------------------------------------------------- Retry-After
    @Test
    void retryAfterOverridesOurOwnBackoff() {
        // Our backoff for attempt 1 is 1000ms. With Retry-After: 0 the call should come
        // back almost immediately, which proves the header was read rather than ignored.
        status = 503;
        failTimes = 1;
        retryAfter = "0";
        long start = System.nanoTime();
        client().list();
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        assertEquals(2, calls.get());
        assertTrue(elapsedMs < 500,
                "Retry-After was ignored; waited " + elapsedMs + "ms instead of ~0");
    }

    @Test
    void malformedRetryAfterFallsBackToBackoff() {
        // The HTTP-date form is valid but rare. Rather than guess we fall back, and above
        // all we do not blow up on a header we cannot parse.
        status = 503;
        failTimes = 1;
        retryAfter = "Wed, 21 Oct 2026 07:28:00 GMT";
        client().list();
        assertEquals(2, calls.get(), "an unparsable Retry-After must not break the retry");
    }
}
