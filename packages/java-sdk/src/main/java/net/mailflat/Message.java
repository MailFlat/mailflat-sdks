// Message — a single email, the typed form of the /api/v1 email serialization.
//
// Connected to:
//   - used by:    Inbox (messages/latest/waitForMessage), user code
//   - depends on: Inbox (reply/markRead/delete/download), Attachment, Jackson JsonNode
//
// Key export: Message — getters (otp(), subject(), links(), attachments(), headers(),
//             sendStatus(), ...) + header(name) + messageId() + replyToAddress()
//             + reply(SendOptions) + markRead()
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One email. Build it from a server JSON node with {@link #fromJson(JsonNode)}. */
public final class Message {
    private final Integer id;
    private final String sender;
    private final String subject;
    private final String text;        // body_text
    private final String html;        // body_html
    private final String otp;         // otp_code (server-extracted)
    private final String tag;
    private final String toAddress;
    private final boolean encrypted;
    private final String direction;   // "in" | "out"
    private final boolean read;
    private final String sendStatus;
    private final String sendError;
    private final String receivedAt;
    private final List<String> links;
    private final List<Attachment> attachments;
    private final JsonNode spam;
    private final Map<String, String> headers;
    private final JsonNode raw;

    private Inbox inbox;              // set by Inbox so reply()/markRead()/delete() work

    private Message(JsonNode d) {
        this.id = d.hasNonNull("id") ? d.get("id").asInt() : null;
        this.sender = text(d, "sender");
        this.subject = text(d, "subject");
        this.text = text(d, "body_text");
        this.html = text(d, "body_html");
        this.otp = text(d, "otp_code");
        this.tag = text(d, "tag");
        this.toAddress = text(d, "to_address");
        this.encrypted = d.path("is_encrypted").asBoolean(false);
        this.direction = text(d, "direction");
        this.read = d.path("is_read").asBoolean(false);
        this.sendStatus = text(d, "send_status");
        this.sendError = text(d, "send_error");
        this.receivedAt = text(d, "received_at");
        this.links = readLinks(d);
        this.attachments = readAttachments(d);
        JsonNode s = d.get("spam");
        this.spam = (s == null || s.isNull()) ? null : s;
        this.headers = readHeaders(d);
        this.raw = d;
    }

    static Message fromJson(JsonNode d) {
        return new Message(d);
    }

    void attachTo(Inbox inbox) {
        this.inbox = inbox;
        for (Attachment a : attachments) {
            a.attachTo(this);
        }
    }

    private static String text(JsonNode d, String field) {
        JsonNode n = d.get(field);
        return (n == null || n.isNull()) ? null : n.asText();
    }

    private static List<String> readLinks(JsonNode d) {
        List<String> out = new ArrayList<>();
        JsonNode arr = d.get("links");
        if (arr != null && arr.isArray()) {
            for (JsonNode n : arr) {
                out.add(n.asText());
            }
        }
        return Collections.unmodifiableList(out);
    }

    private static List<Attachment> readAttachments(JsonNode d) {
        List<Attachment> out = new ArrayList<>();
        JsonNode arr = d.get("attachments");
        if (arr != null && arr.isArray()) {
            for (JsonNode n : arr) {
                out.add(Attachment.fromJson(n));
            }
        }
        return Collections.unmodifiableList(out);
    }

    private static Map<String, String> readHeaders(JsonNode d) {
        JsonNode h = d.get("headers");
        if (h == null || h.isNull() || !h.isObject()) {
            return null;   // null, not empty: encrypted inboxes store no headers at all
        }
        Map<String, String> out = new LinkedHashMap<>();
        h.fields().forEachRemaining(e -> out.put(e.getKey(), e.getValue().asText()));
        return Collections.unmodifiableMap(out);
    }

    public Integer id()         { return id; }

    /**
     * The SMTP envelope sender (MAIL FROM). For transactional mail this is usually a bounce
     * address, so do not reply to it: use {@link #replyToAddress()} or {@link #reply(String)}.
     */
    public String sender()      { return sender; }

    public String subject()     { return subject; }
    public String text()        { return text; }
    public String html()        { return html; }

    /** The one-time code, if the server extracted one (else null). */
    public String otp()         { return otp; }

    public String tag()         { return tag; }
    public String toAddress()   { return toAddress; }
    public boolean isEncrypted(){ return encrypted; }
    public String direction()   { return direction; }
    public boolean isRead()     { return read; }
    public String sendStatus()  { return sendStatus; }
    public String sendError()   { return sendError; }
    public String receivedAt()  { return receivedAt; }

    /**
     * Clickable http(s) URLs written in this message, in order, de-duplicated.
     *
     * <p>Links quoted from an earlier message are excluded, so a reply that adds no link of
     * its own gives you an empty list rather than your own link back.
     */
    public List<String> links() { return links; }

    /** Attachment metadata; call {@link Attachment#download()} for the bytes. */
    public List<Attachment> attachments() { return attachments; }

    /**
     * Spam scan result, or null when the message was NOT SCANNED (not the same as a score
     * of 0.0). Scanning is currently switched off, so in practice this is null today.
     */
    public JsonNode spam()      { return spam; }

    /** Raw message headers, or null on an encrypted inbox (headers carry the Subject). */
    public Map<String, String> headers() { return headers; }

    /** The full backend JSON for this email (escape hatch for fields not surfaced above). */
    public JsonNode raw()       { return raw; }

    /**
     * Look up a header case-insensitively, e.g. {@code msg.header("message-id")}.
     *
     * <p>Header names are case-insensitive per RFC 5322, but the real key on the wire is
     * {@code Message-ID}, so a plain map lookup for {@code "Message-Id"} misses. Use this.
     */
    public String header(String name) {
        if (headers == null || name == null) {
            return null;
        }
        String target = name.replace('_', '-').toLowerCase();
        for (Map.Entry<String, String> e : headers.entrySet()) {
            if (e.getKey().toLowerCase().equals(target)) {
                return e.getValue();
            }
        }
        return null;
    }

    /** This message's {@code Message-ID}, or null when headers were not stored. */
    public String messageId() {
        return header("message-id");
    }

    /**
     * Where a reply should actually go: {@code Reply-To}, else {@code From}, else the
     * envelope sender.
     *
     * <p>{@link #sender()} is the SMTP envelope sender. For transactional mail that is
     * usually a bounce address such as {@code bounces+abc@sendgrid.net}, so replying to it
     * reaches a bounce processor rather than a person. RFC 5322 says a reply goes to
     * {@code Reply-To} when present and to {@code From} otherwise.
     */
    public String replyToAddress() {
        for (String name : new String[]{"reply-to", "from"}) {
            String value = header(name);
            String addr = extractAddress(value);
            if (addr != null) {
                return addr;
            }
        }
        return sender;
    }

    /** Pull the bare address out of {@code Support Team <support@svc.com>}. */
    private static String extractAddress(String value) {
        if (value == null || value.isEmpty()) {
            return null;
        }
        String v = value.trim();
        // A header sent twice arrives comma-joined; the first one wins.
        int comma = v.indexOf(',');
        int lt = v.indexOf('<');
        if (lt >= 0) {
            int gt = v.indexOf('>', lt);
            if (gt > lt) {
                String inner = v.substring(lt + 1, gt).trim();
                return inner.contains("@") ? inner : null;
            }
        }
        if (comma > 0) {
            v = v.substring(0, comma).trim();
        }
        return v.contains("@") ? v : null;
    }

    /**
     * Reply to this message, keeping it in the same conversation.
     *
     * <p>Fills in what a reply needs and is easy to get wrong by hand: the recipient, an
     * {@code Re:} subject that is not doubled up, and the {@code In-Reply-To} /
     * {@code References} headers Gmail and Outlook use to thread. Without those a reply
     * shows up as a separate conversation, which is what a hand-rolled send produces.
     */
    public SendResult reply(String body) {
        return reply(body, null, null);
    }

    /** Reply with an explicit HTML part and/or subject (null = derive it). */
    public SendResult reply(String body, String html, String subject) {
        return reply(SendOptions.builder().body(body).html(html).subject(subject).build());
    }

    /**
     * Reply carrying everything a send can carry — cc, bcc, attachments included.
     *
     * <p>Answering a mail with a file attached is the normal case, and an agent should not
     * have to drop back to {@code inbox.send()} (losing the threading headers) just to attach
     * one. The recipient and {@code In-Reply-To} are this method's own: whatever
     * {@link SendOptions.Builder#inReplyTo(String)} holds is ignored here. A subject in the
     * options wins over the derived {@code Re:} one.
     */
    public SendResult reply(SendOptions options) {
        if (inbox == null) {
            throw new MailFlatException(
                    "This message is not attached to an inbox. Use inbox.send(...) instead.");
        }
        String target = replyToAddress();
        if (target == null || target.isEmpty()) {
            throw new MailFlatException("This message has no sender to reply to.");
        }
        SendOptions opts = options != null ? options : SendOptions.builder().build();
        String subject = opts.subject() != null ? opts.subject() : replySubject(subject());
        return inbox.sendReply(target, opts, subject, messageId());
    }

    /** {@code Re:} prefix without doubling it up; clients also thread on the subject. */
    /** The server's {@code subject} column limit. */
    static final int MAX_SUBJECT_CHARS = 200;

    static String replySubject(String subject) {
        String s = oneLine(subject == null ? "" : subject).trim();
        if (s.isEmpty()) {
            return "Re:";
        }
        String reply = s.toLowerCase().startsWith("re:") ? s : "Re: " + s;
        // A reply subject the SDK BUILDS ITSELF must fit the server's limit. Inbound mail is
        // not bound by our API limit, so a stored 198-character subject made "Re: " + it
        // exceed 200 and reply() refused to answer a message the service had accepted.
        // Truncating a value WE derived differs from truncating the caller's input: an
        // explicit subject that is too long is still rejected, because they can fix it.
        //
        // Control characters follow the same rule, and it took a second external round to
        // notice the rule had only been applied along the length axis. A stranger can send a
        // subject whose RFC 2047 encoded-word decodes to a real CRLF; the server stored it,
        // this helper prefixed "Re: ", and the server's header guard then rejected it \u2014 so
        // that message could never be answered again, permanently.
        return reply.length() <= MAX_SUBJECT_CHARS
                ? reply
                : reply.substring(0, MAX_SUBJECT_CHARS - 1) + "\u2026";
    }

    /**
     * Control characters out of a value about to become a mail header.
     *
     * <p>A space, not deletion: {@code "a\r\nb"} becoming {@code "ab"} would silently join
     * two words. Tab survives \u2014 it is horizontal space and does not split a header line.
     */
    static String oneLine(String text) {
        StringBuilder out = new StringBuilder(text.length());
        boolean inRun = false;
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            boolean control = (ch < ' ' && ch != '\t') || ch == '\u007F';
            if (control) {
                // A RUN collapses to one space: CRLF is two characters and "a\r\nb" should
                // read "a b", not "a  b".
                if (!inRun) {
                    out.append(' ');
                }
            } else {
                out.append(ch);
            }
            inRun = control;
        }
        return out.toString();
    }

    /** Mark this message as read, so the next poll can skip it. */
    public JsonNode markRead() {
        requireInbox("markRead");
        return inbox.markRead(id);
    }

    /** Delete this message; the inbox itself stays. */
    public JsonNode delete() {
        requireInbox("deleteMessage");
        return inbox.deleteMessage(id);
    }

    byte[] downloadAttachment(int attachmentId) {
        requireInbox("downloadAttachment");
        return inbox.downloadAttachment(id, attachmentId);
    }

    private void requireInbox(String alternative) {
        if (inbox == null || id == null) {
            throw new MailFlatException(
                    "This message is not attached to an inbox (or has no id). "
                            + "Use inbox." + alternative + "(...) instead.");
        }
    }

    @Override
    public String toString() {
        return "Message{subject=" + subject + ", sender=" + sender + ", otp=" + otp + "}";
    }
}
