// Inbox — high-level operations on a single MailFlat inbox.
//
// Returned by MailFlat.create()/list()/inbox(address). Reads mail, waits for OTPs, sends, deletes.
//
// Connected to:
//   - used by:    MailFlat (creates it), user code
//   - depends on: MailFlat (HTTP), Message, SendOptions, SendResult, exceptions, Jackson
//
// Key export: Inbox — address(), messages(), message(id), latest(), waitForOtp(),
//                     send(to, SendOptions), waitUntilSent(), delete()
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** A single inbox. The address is permanent; only its messages expire, on the retention
 *  window you choose. Obtain it from {@link MailFlat}, don't construct directly. */
public final class Inbox {
    private static final long DEFAULT_POLL_MILLIS = 1000L;
    /** Delivery is slower than arrival, so polling it every second only burns requests. */
    private static final long SEND_POLL_MILLIS = 2000L;
    private static final int DEFAULT_SEND_TIMEOUT_SECONDS = 120;

    private final MailFlat client;
    private final String address;
    private final JsonNode raw;

    Inbox(MailFlat client, String address, JsonNode meta) {
        this.client = client;
        this.address = address;
        this.raw = meta;
    }

    public String address() {
        return address;
    }

    /** Per-inbox API key (mf_sk_...) when the server returned one, else null. */
    public String apiKey() {
        return raw != null && raw.hasNonNull("api_key") ? raw.get("api_key").asText() : null;
    }

    public String name() {
        return raw != null && raw.hasNonNull("name") ? raw.get("name").asText() : null;
    }

    public Integer retentionHours() {
        return raw != null && raw.hasNonNull("retention_hours") ? raw.get("retention_hours").asInt() : null;
    }

    public boolean isEncrypted() {
        return raw != null && raw.path("encrypted").asBoolean(false);
    }

    /** The full backend JSON returned for this inbox (or null if attached via inbox(address)). */
    public JsonNode raw() {
        return raw;
    }

    // -------------------------------------------------------------------- read
    /** Incoming messages in this inbox, newest first. */
    public List<Message> messages() {
        return messages(Direction.IN);
    }

    /** Messages in this inbox filtered by direction, newest first. */
    public List<Message> messages(Direction direction) {
        JsonNode res = client.get("/api/v1/inboxes/" + address + "/messages"
                + "?direction=" + dir(direction));
        List<Message> out = new ArrayList<>();
        JsonNode emails = res.get("emails");
        if (emails != null && emails.isArray()) {
            for (JsonNode e : emails) {
                out.add(attach(Message.fromJson(e)));
            }
        }
        return out;
    }

    /** The most recent incoming message, or empty if the inbox has none. */
    public Optional<Message> latest() {
        return latest(Direction.IN);
    }

    /** The most recent message in the given direction, or empty if there is none. */
    public Optional<Message> latest(Direction direction) {
        JsonNode res = client.get("/api/v1/inboxes/" + address + "/latest"
                + "?direction=" + dir(direction));
        JsonNode email = res.get("email");
        return (email != null && !email.isNull())
                ? Optional.of(attach(Message.fromJson(email))) : Optional.empty();
    }

    /** Polls (default 30s) until any message arrives. */
    public Message waitForMessage() {
        return waitForMessage(30);
    }

    /**
     * Polls until any message arrives, then returns it.
     *
     * @throws OtpTimeoutException   if nothing arrives before {@code timeoutSeconds}
     * @throws EncryptedInboxException if the inbox is end-to-end encrypted
     */
    public Message waitForMessage(int timeoutSeconds) {
        return waitForMessage(timeoutSeconds, Direction.IN);
    }

    /**
     * Polls until a message in the given direction arrives, then returns it.
     *
     * <p>{@link Direction#IN} by default on purpose: without the filter a wait right after
     * {@code send()} returned the agent's own outgoing mail and finished instantly.
     */
    public Message waitForMessage(int timeoutSeconds, Direction direction) {
        long deadline = System.nanoTime() + timeoutSeconds * 1_000_000_000L;
        String path = "/api/v1/inboxes/" + address + "/latest?direction=" + dir(direction);
        while (true) {
            JsonNode res = client.get(path);
            requireNotEncrypted(res, "use a non-encrypted inbox for agent automation.");
            JsonNode email = res.get("email");
            if (email != null && !email.isNull()) {
                return attach(Message.fromJson(email));
            }
            if (System.nanoTime() >= deadline) {
                throw new OtpTimeoutException(
                        "No message arrived for " + address + " within " + timeoutSeconds + "s");
            }
            sleep();
        }
    }

    /** Polls (default 30s) until an OTP code arrives, then returns it. */
    public String waitForOtp() {
        return waitForOtp(30);
    }

    /**
     * Polls until a one-time code (OTP) arrives, then returns it.
     *
     * @throws OtpTimeoutException     if no OTP arrives before {@code timeoutSeconds}
     * @throws EncryptedInboxException if the inbox is end-to-end encrypted
     */
    public String waitForOtp(int timeoutSeconds) {
        long deadline = System.nanoTime() + timeoutSeconds * 1_000_000_000L;
        String path = "/api/v1/inboxes/" + address + "/latest?direction=in";
        JsonNode seen = null;                 // newest mail we looked at, for diagnostics
        while (true) {
            JsonNode res = client.get(path);
            requireNotEncrypted(res, "OTP cannot be read via the API.");
            JsonNode email = res.get("email");
            if (email != null && !email.isNull()) {
                if (email.hasNonNull("otp_code")) {
                    return email.get("otp_code").asText();
                }
                seen = email;                 // mail arrived, but no code could be extracted
            }
            if (System.nanoTime() >= deadline) {
                throw new OtpTimeoutException(otpTimeoutMessage(seen, timeoutSeconds));
            }
            sleep();
        }
    }

    /**
     * "Mail came but had no code" and "nothing arrived" are different failures.
     *
     * <p>Reported as one message they are undiagnosable: the caller cannot tell a broken
     * signup flow from an OTP format we fail to parse. When mail did arrive we quote it so
     * the caller can see the code with their own eyes.
     */
    private String otpTimeoutMessage(JsonNode seen, int timeoutSeconds) {
        if (seen == null) {
            return "No mail arrived for " + address + " within " + timeoutSeconds + "s";
        }
        String subject = seen.hasNonNull("subject") ? seen.get("subject").asText().trim() : "";
        if (subject.isEmpty()) {
            subject = "(no subject)";
        }
        String body = seen.hasNonNull("body_text") ? seen.get("body_text").asText() : "";
        String snippet = body.replaceAll("\\s+", " ").trim();
        if (snippet.length() > 120) {
            snippet = snippet.substring(0, 120);
        }
        return "Mail arrived for " + address + " but no OTP could be extracted from it within "
                + timeoutSeconds + "s. Newest message: '" + subject + "'"
                + (snippet.isEmpty() ? "" : " - '" + snippet + "'")
                + ". Read inbox.latest().get().text() and parse the code yourself, "
                + "and please report the format so we can support it.";
    }

    /**
     * Fetch one message by id — the way to ask "what happened to that send?".
     *
     * <p>Without it the only way to check a sent mail was to pull the whole outbound list and
     * find the id by hand, which for an agent that sent 100 mails means 100 messages per check.
     */
    public Message message(int messageId) {
        JsonNode res = client.get("/api/v1/inboxes/" + address + "/messages/" + messageId);
        JsonNode email = res.get("email");
        return attach(Message.fromJson(email != null && !email.isNull()
                ? email : client.json().createObjectNode()));
    }

    // ------------------------------------------------------------------- write
    /** Send a DKIM-signed email from this inbox (plain text). */
    public SendResult send(String to, String subject, String body) {
        return send(to, subject, body, null);
    }

    /** Send a DKIM-signed email from this inbox; {@code html} is optional (null = plain only). */
    public SendResult send(String to, String subject, String body, String html) {
        return send(to, subject, body, html, null);
    }

    /**
     * Send, optionally continuing an existing conversation.
     *
     * <p>Pass {@code inReplyTo} (a {@code Message-ID}) to keep the mail in the same thread;
     * without it the recipient's client starts a new one. {@link Message#reply(String)}
     * fills this in for you.
     */
    public SendResult send(String to, String subject, String body, String html, String inReplyTo) {
        return send(to, SendOptions.builder()
                .subject(subject).body(body).html(html).inReplyTo(inReplyTo).build());
    }

    /**
     * Send with everything a mail can carry: cc, bcc, attachments, threading.
     *
     * <pre>{@code
     * SendResult r = inbox.send("finance@acme.com", SendOptions.builder()
     *         .subject("Invoice")
     *         .body("Attached.")
     *         .cc("cc@acme.com")
     *         .attach(Path.of("/tmp/invoice.pdf"))
     *         .build());
     * inbox.waitUntilSent(r);
     * }</pre>
     *
     * <p>Returns as soon as the mail is <em>accepted for delivery</em> (HTTP 202) — not once
     * it is delivered. Delivery runs on a queue; ask {@link #waitUntilSent(SendResult)} or
     * subscribe to the {@code message.delivered} / {@code message.failed} webhook for the
     * outcome. Blocking here would put back the exact stall the queue was built to remove.
     *
     * <p>Attachment size and count depend on your plan (free is deliberately small); going
     * over is rejected with the limit spelled out rather than silently dropping the file.
     *
     * <p>Not retried on gateway errors: a retried send delivers the same mail twice.
     */
    public SendResult send(String to, SendOptions options) {
        return sendReply(to, options, null, null);
    }

    /**
     * The one place a send actually goes out; {@link Message#reply(SendOptions)} comes through
     * here too, with the two fields a reply owns (recipient subject, In-Reply-To) overridden.
     * One path, so a field added to a send cannot quietly miss replies.
     */
    SendResult sendReply(String to, SendOptions options, String subjectOverride,
                         String inReplyToOverride) {
        SendOptions opts = options != null ? options : SendOptions.builder().build();
        ObjectNode payload = opts.toPayload(client.json(), to, subjectOverride, inReplyToOverride);
        return new SendResult(client.post("/api/v1/inboxes/" + address + "/send", payload));
    }

    /** Wait (up to 120s) for a mail to reach a final delivery state. */
    public Message waitUntilSent(int messageId) {
        return waitUntilSent(messageId, DEFAULT_SEND_TIMEOUT_SECONDS);
    }

    /** Wait for the mail this send accepted to reach a final delivery state. */
    public Message waitUntilSent(SendResult result) {
        return waitUntilSent(result.requireMessageId(), DEFAULT_SEND_TIMEOUT_SECONDS);
    }

    /** Wait for the mail this send accepted, with your own timeout. */
    public Message waitUntilSent(SendResult result, int timeoutSeconds) {
        return waitUntilSent(result.requireMessageId(), timeoutSeconds);
    }

    /**
     * Block until a sent mail reaches a final delivery state, then return it.
     *
     * <p>{@code send()} is asynchronous, so "did it actually go out?" has no synchronous
     * answer. This polls the message until the server reports one; it is the pull half of the
     * contract, the push half being the {@code message.delivered} / {@code message.failed}
     * webhook (prefer that when you can receive one).
     *
     * <p>It does NOT return quietly on failure: a helper called {@code waitUntilSent} that
     * hands back a failed mail as if nothing happened is how mail goes missing.
     *
     * @throws SendFailedException  if delivery permanently failed
     * @throws SendTimeoutException if it is still queued when the timeout elapses. That is
     *                              not a failure — the queue keeps retrying, and a caller who
     *                              reads it as one and sends again delivers the mail twice.
     */
    public Message waitUntilSent(int messageId, int timeoutSeconds) {
        long deadline = System.nanoTime() + timeoutSeconds * 1_000_000_000L;
        while (true) {
            Message message = message(messageId);
            String status = message.sendStatus();
            if ("sent".equals(status) || "unsigned".equals(status)) {
                return message;
            }
            if ("failed".equals(status)) {
                String reason = message.sendError();
                throw new SendFailedException(
                        reason != null && !reason.isEmpty() ? reason : "Delivery failed",
                        messageId, status);
            }
            if (System.nanoTime() >= deadline) {
                // The reason goes into the sentence when the queue has actually tried:
                // greylisting and an unreachable recipient used to read identically.
                // Timing out is still NOT a failure, so the wording keeps saying so.
                String lastError = message.sendError();
                if (lastError != null && lastError.isEmpty()) {
                    lastError = null;
                }
                throw new SendTimeoutException(
                        "Message " + messageId + " was still " + (status != null ? status : "queued")
                                + " after " + timeoutSeconds + "s. It may still be delivered; "
                                + "the queue keeps retrying."
                                + (lastError != null ? " The last attempt reported: " + lastError : ""),
                        messageId, status, lastError);
            }
            sleep(SEND_POLL_MILLIS);
        }
    }

    /**
     * Mark one message as read, so a poll can look for <em>new</em> mail instead of
     * re-reading the inbox and tracking state itself.
     */
    public JsonNode markRead(int messageId) {
        // Idempotent: repeating it lands on the same end state, so a retry is safe.
        return client.postIdempotent(
                "/api/v1/inboxes/" + address + "/messages/" + messageId + "/read", null);
    }

    /**
     * Delete every message in this inbox and keep the address.
     *
     * <p>Useful between test scenarios: the address stays registered wherever you used it.
     */
    public JsonNode burn() {
        return client.postIdempotent("/api/v1/inboxes/" + address + "/burn", null);
    }

    /**
     * Download one attachment's bytes. Prefer {@code msg.attachments().get(0).download()}.
     *
     * @throws EncryptedInboxException on an end-to-end encrypted inbox, where the server
     *                                 cannot decrypt the file
     */
    public byte[] downloadAttachment(int messageId, int attachmentId) {
        return client.getBytes("/api/v1/inboxes/" + address + "/messages/" + messageId
                + "/attachments/" + attachmentId);
    }

    /** Delete this inbox and all its messages. Irreversible. */
    public JsonNode delete() {
        return client.delete("/api/v1/inboxes/" + address);
    }

    /** Delete a single message in this inbox (the inbox itself stays). Use with {@code msg.id()}. */
    public JsonNode deleteMessage(int messageId) {
        return client.delete("/api/v1/inboxes/" + address + "/messages/" + messageId);
    }

    // ------------------------------------------------------------------- utils
    private static String dir(Direction direction) {
        return (direction == null ? Direction.IN : direction).wire();
    }

    /** Wire the message back to this inbox so reply()/markRead()/download() work. */
    private Message attach(Message message) {
        message.attachTo(this);
        return message;
    }

    private void requireNotEncrypted(JsonNode res, String hint) {
        if (res.path("encrypted").asBoolean(false)) {
            String note = res.hasNonNull("note") ? res.get("note").asText()
                    : "This inbox is end-to-end encrypted; " + hint;
            throw new EncryptedInboxException(note);
        }
    }

    private void sleep() {
        sleep(DEFAULT_POLL_MILLIS);
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new MailFlatException("Interrupted while polling " + address);
        }
    }

    @Override
    public String toString() {
        return "Inbox{" + address + "}";
    }
}
