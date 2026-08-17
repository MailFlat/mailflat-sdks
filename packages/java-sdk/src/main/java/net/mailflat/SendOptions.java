// SendOptions — everything a send can carry: body, html, cc/bcc, attachments, threading.
//
// It also builds the request body, and it is the ONLY place that does. send() and reply()
// must accept exactly the same fields (an agent answering a mail with an invoice attached is
// the normal case, not an edge case); two builders would drift the first time a field is
// added to one of them.
//
// Connected to:
//   - used by:    Inbox.send(), Message.reply(), user code
//   - depends on: OutgoingAttachment, Jackson
//
// Key export: SendOptions.builder()...build()
package net.mailflat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Optional parameters for {@link Inbox#send(String, SendOptions)} and {@link Message#reply(SendOptions)}. */
public final class SendOptions {

    final String subject;
    final String body;
    final String html;
    final String inReplyTo;
    final List<String> cc;
    final List<String> bcc;
    final List<OutgoingAttachment> attachments;

    private SendOptions(Builder b) {
        this.subject = b.subject;
        this.body = b.body;
        this.html = b.html;
        this.inReplyTo = b.inReplyTo;
        this.cc = Collections.unmodifiableList(new ArrayList<>(b.cc));
        this.bcc = Collections.unmodifiableList(new ArrayList<>(b.bcc));
        this.attachments = Collections.unmodifiableList(new ArrayList<>(b.attachments));
    }

    public static Builder builder() {
        return new Builder();
    }

    public String subject()                       { return subject; }
    public String body()                          { return body; }
    public String html()                          { return html; }
    public String inReplyTo()                     { return inReplyTo; }
    public List<String> cc()                      { return cc; }
    public List<String> bcc()                     { return bcc; }
    public List<OutgoingAttachment> attachments() { return attachments; }

    /**
     * The request body for {@code POST /api/v1/inboxes/{address}/send}.
     *
     * <p>{@code subjectOverride} / {@code inReplyToOverride} let {@link Message#reply} fill in
     * the two fields a reply owns without copying the rest of this builder.
     */
    ObjectNode toPayload(ObjectMapper mapper, String to, String subjectOverride,
                         String inReplyToOverride) {
        ObjectNode payload = mapper.createObjectNode();
        payload.put("to", to);
        String finalSubject = subjectOverride != null ? subjectOverride : subject;
        payload.put("subject", finalSubject != null ? finalSubject : "");
        payload.put("body", body != null ? body : "");
        if (html != null) {
            payload.put("html", html);
        }
        String finalInReplyTo = inReplyToOverride != null ? inReplyToOverride : inReplyTo;
        if (finalInReplyTo != null) {
            payload.put("in_reply_to", finalInReplyTo);
        }
        if (!cc.isEmpty()) {
            ArrayNode node = payload.putArray("cc");
            cc.forEach(node::add);
        }
        if (!bcc.isEmpty()) {
            ArrayNode node = payload.putArray("bcc");
            bcc.forEach(node::add);
        }
        if (!attachments.isEmpty()) {
            ArrayNode node = payload.putArray("attachments");
            for (OutgoingAttachment a : attachments) {
                ObjectNode item = node.addObject();
                item.put("filename", a.filename());
                item.put("content_type", a.contentType());
                item.put("content_b64", a.contentBase64());
            }
        }
        return payload;
    }

    /** Fluent builder; every field is optional. */
    public static final class Builder {
        private String subject;
        private String body;
        private String html;
        private String inReplyTo;
        private final List<String> cc = new ArrayList<>();
        private final List<String> bcc = new ArrayList<>();
        private final List<OutgoingAttachment> attachments = new ArrayList<>();

        public Builder subject(String v) { this.subject = v; return this; }
        public Builder body(String v)    { this.body = v; return this; }
        public Builder html(String v)    { this.html = v; return this; }

        /**
         * Continue an existing conversation by quoting the {@code Message-ID} you are
         * answering. {@link Message#reply(SendOptions)} sets this for you and ignores
         * whatever is here.
         */
        public Builder inReplyTo(String v) { this.inReplyTo = v; return this; }

        /** Carbon copy: these recipients are written into the headers and see each other. */
        public Builder cc(String... addresses) {
            this.cc.addAll(Arrays.asList(addresses));
            return this;
        }

        public Builder cc(List<String> addresses) {
            if (addresses != null) {
                this.cc.addAll(addresses);
            }
            return this;
        }

        /**
         * Blind carbon copy: these recipients get the mail but appear in no header — not
         * even in their own copy.
         */
        public Builder bcc(String... addresses) {
            this.bcc.addAll(Arrays.asList(addresses));
            return this;
        }

        public Builder bcc(List<String> addresses) {
            if (addresses != null) {
                this.bcc.addAll(addresses);
            }
            return this;
        }

        /** Attach a file from disk; the content type is guessed from the extension. */
        public Builder attach(Path path) {
            this.attachments.add(OutgoingAttachment.of(path));
            return this;
        }

        /** Attach bytes you already hold in memory. */
        public Builder attach(String filename, byte[] content) {
            this.attachments.add(OutgoingAttachment.of(filename, content));
            return this;
        }

        public Builder attach(OutgoingAttachment attachment) {
            if (attachment != null) {
                this.attachments.add(attachment);
            }
            return this;
        }

        public Builder attachments(List<OutgoingAttachment> items) {
            if (items != null) {
                this.attachments.addAll(items);
            }
            return this;
        }

        public SendOptions build() {
            return new SendOptions(this);
        }
    }
}
