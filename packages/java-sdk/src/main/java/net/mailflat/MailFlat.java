// MailFlat — thin, typed Java wrapper over the /api/v1 automation API.
//
// Opens, lists, reads (mail/OTP) and sends with a single account API key (X-API-Key).
// HTTP via java.net.http (JDK built-in, no HTTP dependency); JSON via Jackson.
//
// Connected to:
//   - used by:    user code (Selenium/JUnit test suites), Inbox
//   - depends on: Inbox, Message, exceptions, Jackson, java.net.http
//
// Key export: MailFlat — create()/create(label)/create(opts), list(), inbox(address)
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * MailFlat automation API client.
 *
 * <pre>{@code
 * MailFlat mf = new MailFlat("mf_live_...");
 * Inbox inbox = mf.create("signup");
 * String otp = inbox.waitForOtp(30);
 * }</pre>
 */
public final class MailFlat {
    private static final String DEFAULT_BASE_URL = "https://mailflat.net";
    private static final Set<Integer> RETRY_STATUSES =
            new HashSet<>(Arrays.asList(429, 502, 503, 504));

    // Only side-effect-free (idempotent) methods are retried. POST is deliberately excluded:
    // if the MTA accepts a /send and the gateway then returns 504, a retry delivers THE SAME
    // MAIL TWICE and the caller never finds out. POST stays single-shot until we support
    // Idempotency-Key.
    private static final Set<String> IDEMPOTENT_METHODS =
            new HashSet<>(Arrays.asList("GET", "HEAD", "DELETE"));

    private static final long MAX_BACKOFF_MS = 8000L;

    /** Read once from the filtered resource; pom.xml is the only place the version lives. */
    private static final String VERSION = readVersion();

    private static String readVersion() {
        try (java.io.InputStream in =
                     MailFlat.class.getResourceAsStream("/mailflat-version.properties")) {
            if (in == null) {
                return "unknown";
            }
            java.util.Properties p = new java.util.Properties();
            p.load(in);
            String v = p.getProperty("version");
            // An unfiltered build leaves the literal placeholder; report unknown rather
            // than send "${project.version}" to the server as our version.
            return (v == null || v.isEmpty() || v.startsWith("${")) ? "unknown" : v;
        } catch (IOException e) {
            return "unknown";
        }
    }

    /** The version this SDK actually reports. Package-private so tests can lock it. */
    static String version() {
        return VERSION;
    }

    private static String userAgent() {
        return "mailflat-java/" + VERSION;
    }

    private final String apiKey;
    private final String baseUrl;
    private final Duration timeout;
    private final int maxRetries;
    private final HttpClient http;
    private final ObjectMapper mapper = new ObjectMapper();

    public MailFlat(String apiKey) {
        this(builder().apiKey(apiKey));
    }

    public MailFlat(String apiKey, String baseUrl) {
        this(builder().apiKey(apiKey).baseUrl(baseUrl));
    }

    private MailFlat(Builder b) {
        String key = (b.apiKey != null && !b.apiKey.isEmpty())
                ? b.apiKey : System.getenv("MAILFLAT_API_KEY");
        if (key == null || key.isEmpty()) {
            throw new MailFlatException(
                    "An API key is required. Pass new MailFlat(apiKey) or set MAILFLAT_API_KEY.");
        }
        this.apiKey = key;
        this.baseUrl = stripTrailingSlash(b.baseUrl != null ? b.baseUrl : DEFAULT_BASE_URL);
        this.timeout = b.timeout != null ? b.timeout : Duration.ofSeconds(30);
        this.maxRetries = Math.max(0, b.maxRetries);
        this.http = b.httpClient != null ? b.httpClient
                : HttpClient.newBuilder().connectTimeout(this.timeout).build();
    }

    public static Builder builder() {
        return new Builder();
    }

    // ------------------------------------------------------------------ public
    /** Open a new inbox with default options. The address is permanent; only its messages expire. */
    public Inbox create() {
        return create(CreateInboxOptions.builder().build());
    }

    /** Open a new inbox with the given label. The address is permanent; only its messages expire. */
    public Inbox create(String label) {
        return create(CreateInboxOptions.builder().label(label).build());
    }

    /** Open a new inbox with explicit options. The address is permanent; only its messages expire. */
    public Inbox create(CreateInboxOptions opts) {
        ObjectNode payload = mapper.createObjectNode();
        if (opts.prefix != null)         payload.put("prefix", opts.prefix);
        if (opts.label != null)          payload.put("label", opts.label);
        if (opts.subdomain != null)      payload.put("subdomain", opts.subdomain);
        if (opts.domain != null)         payload.put("domain", opts.domain);
        if (opts.retentionHours != null) payload.put("retention_hours", opts.retentionHours);
        JsonNode res = post("/api/v1/inboxes", payload);
        return new Inbox(this, res.path("address").asText(), res);
    }

    /** All inboxes opened with this API key. */
    public List<Inbox> list() {
        JsonNode res = get("/api/v1/inboxes");
        List<Inbox> out = new ArrayList<>();
        JsonNode arr = res.get("inboxes");
        if (arr != null && arr.isArray()) {
            for (JsonNode i : arr) {
                out.add(new Inbox(this, i.path("address").asText(), i));
            }
        }
        return out;
    }

    /** Attach to an existing address without a network call. */
    public Inbox inbox(String address) {
        return new Inbox(this, address, null);
    }

    // ----------------------------------------------------------- package HTTP
    ObjectMapper json() {
        return mapper;
    }

    JsonNode get(String path) {
        return request("GET", path, null, false);
    }

    /**
     * POST that is NEVER retried. See {@link #IDEMPOTENT_METHODS}: a retried send delivers
     * the same mail twice and the caller never finds out.
     */
    JsonNode post(String path, JsonNode body) {
        return request("POST", path, body, false);
    }

    /**
     * POST that IS safe to repeat, because repeating it lands on the same end state
     * (mark-read, burn). Never pass this for /send.
     */
    JsonNode postIdempotent(String path, JsonNode body) {
        return request("POST", path, body, true);
    }

    JsonNode delete(String path) {
        return request("DELETE", path, null, false);
    }

    /**
     * GET that returns raw bytes (attachment download) rather than JSON.
     *
     * <p>On an encrypted inbox the server cannot decrypt the file and answers with the
     * envelope JSON instead of the bytes; that is detected here by the content type so the
     * caller gets a clear exception rather than a JSON blob masquerading as a file.
     */
    byte[] getBytes(String path) {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(timeout)
                .header("X-API-Key", apiKey)
                .header("User-Agent", userAgent())
                .GET()
                .build();
        HttpResponse<byte[]> resp;
        try {
            resp = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new MailFlatException("Network error: interrupted");
        } catch (IOException e) {
            throw new MailFlatException("Network error: " + e.getMessage());
        }
        String contentType = resp.headers().firstValue("content-type").orElse("");
        if (resp.statusCode() >= 400) {
            String detail = new String(resp.body(), java.nio.charset.StandardCharsets.UTF_8);
            throw MailFlatException.forStatus(resp.statusCode(),
                    extractDetail(parse(detail), resp.statusCode()));
        }
        if (contentType.startsWith("application/json")) {
            throw new EncryptedInboxException(
                    "This inbox is end-to-end encrypted; the server cannot decrypt this attachment.");
        }
        return resp.body();
    }

    private JsonNode request(String method, String path, JsonNode body, boolean idempotent) {
        int attempt = 0;
        while (true) {
            HttpRequest.Builder rb = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + path))
                    .timeout(timeout)
                    .header("X-API-Key", apiKey)
                    .header("User-Agent", userAgent());
            if (body != null) {
                rb.header("Content-Type", "application/json");
                rb.method(method, HttpRequest.BodyPublishers.ofString(body.toString()));
            } else {
                rb.method(method, HttpRequest.BodyPublishers.noBody());
            }

            HttpResponse<String> resp;
            try {
                resp = http.send(rb.build(), HttpResponse.BodyHandlers.ofString());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new MailFlatException("Network error: interrupted");
            } catch (IOException e) {
                throw new MailFlatException("Network error: " + e.getMessage());
            }

            int status = resp.statusCode();
            boolean canRepeat = idempotent || IDEMPOTENT_METHODS.contains(method);
            if (RETRY_STATUSES.contains(status) && canRepeat && attempt < maxRetries) {
                attempt++;
                // Obey the server when it says how long to wait. Coming back after our own
                // 1s both earns another 429 and stretches the limit window.
                backoff(attempt, retryAfterSeconds(resp));
                continue;
            }

            JsonNode data = parse(resp.body());
            if (status >= 400) {
                throw MailFlatException.forStatus(status, extractDetail(data, status));
            }
            if (data.hasNonNull("error")) {
                throw new MailFlatException(data.get("error").asText(), status);
            }
            return data;
        }
    }

    private JsonNode parse(String body) {
        if (body == null || body.isEmpty()) {
            return mapper.createObjectNode();
        }
        try {
            JsonNode n = mapper.readTree(body);
            return (n == null || n.isNull()) ? mapper.createObjectNode() : n;
        } catch (IOException e) {
            return mapper.createObjectNode();
        }
    }

    private String extractDetail(JsonNode data, int status) {
        if (data.hasNonNull("detail")) return data.get("detail").asText();
        if (data.hasNonNull("error"))  return data.get("error").asText();
        return "Request failed with status " + status;
    }

    /** `Retry-After` in its seconds form. Absent or unparsable returns null (use backoff). */
    private Long retryAfterSeconds(HttpResponse<String> resp) {
        String raw = resp.headers().firstValue("Retry-After").orElse(null);
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return Math.max(0L, (long) Double.parseDouble(raw.trim()));
        } catch (NumberFormatException e) {
            // The HTTP-date form is valid but rare; rather than guess, fall back to backoff.
            return null;
        }
    }

    private void backoff(int attempt, Long retryAfter) {
        long ms = retryAfter != null
                ? Math.min(retryAfter * 1000L, MAX_BACKOFF_MS)
                : Math.min((long) Math.pow(2, attempt) * 500L, MAX_BACKOFF_MS);
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new MailFlatException("Interrupted during retry backoff");
        }
    }

    private static String stripTrailingSlash(String url) {
        int end = url.length();
        while (end > 0 && url.charAt(end - 1) == '/') {
            end--;
        }
        return url.substring(0, end);
    }

    // --------------------------------------------------------------- builder
    public static final class Builder {
        private String apiKey;
        private String baseUrl;
        private Duration timeout;
        private int maxRetries = 2;
        private HttpClient httpClient;

        public Builder apiKey(String v)        { this.apiKey = v; return this; }
        public Builder baseUrl(String v)       { this.baseUrl = v; return this; }
        public Builder timeout(Duration v)     { this.timeout = v; return this; }
        public Builder maxRetries(int v)       { this.maxRetries = v; return this; }
        /** Inject a custom HttpClient (e.g. for tests). */
        public Builder httpClient(HttpClient v){ this.httpClient = v; return this; }

        public MailFlat build() {
            return new MailFlat(this);
        }
    }
}
