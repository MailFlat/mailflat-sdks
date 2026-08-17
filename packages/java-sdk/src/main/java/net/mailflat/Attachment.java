// Attachment — metadata for one file on a message; the bytes are fetched on demand.
//
// Contents are never included in a message listing: a single 5 MB attachment would make the
// list unusable, so the bytes live behind a separate request.
//
// Connected to:
//   - used by:    Message (attachments()), user code
//   - depends on: Inbox (downloadAttachment), Jackson JsonNode
//
// Key export: Attachment — filename(), contentType(), sizeBytes(), isTruncated(), download()
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;

/** One attachment. Call {@link #download()} for the bytes. */
public final class Attachment {
    private final Integer id;
    private final String filename;
    private final String contentType;
    private final Integer sizeBytes;
    private final boolean encrypted;
    private final boolean truncated;
    private final JsonNode raw;

    private Message message;   // set by Message so download() knows where to fetch from

    private Attachment(JsonNode d) {
        this.id = d.hasNonNull("id") ? d.get("id").asInt() : null;
        this.filename = text(d, "filename");
        this.contentType = text(d, "content_type");
        this.sizeBytes = d.hasNonNull("size_bytes") ? d.get("size_bytes").asInt() : null;
        this.encrypted = d.path("is_encrypted").asBoolean(false);
        this.truncated = d.path("truncated").asBoolean(false);
        this.raw = d;
    }

    static Attachment fromJson(JsonNode d) {
        return new Attachment(d);
    }

    void attachTo(Message message) {
        this.message = message;
    }

    private static String text(JsonNode d, String field) {
        JsonNode n = d.get(field);
        return (n == null || n.isNull()) ? null : n.asText();
    }

    public Integer id()          { return id; }
    public String filename()     { return filename; }
    public String contentType()  { return contentType; }
    public Integer sizeBytes()   { return sizeBytes; }
    public boolean isEncrypted() { return encrypted; }

    /**
     * True when the file exceeded the storage cap and the bytes were never stored.
     *
     * <p>The metadata is kept on purpose so "there was an attachment" is not lost silently.
     * {@link #download()} on a truncated attachment has nothing to return.
     */
    public boolean isTruncated()  { return truncated; }

    /** The full backend JSON for this attachment. */
    public JsonNode raw()         { return raw; }

    /** Download the bytes. */
    public byte[] download() {
        if (message == null || message.id() == null || id == null) {
            throw new MailFlatException(
                    "This attachment is not attached to a message. "
                            + "Use inbox.downloadAttachment(messageId, attachmentId) instead.");
        }
        if (truncated) {
            throw new MailFlatException(
                    "Attachment '" + filename + "' exceeded the storage cap, so its bytes were "
                            + "never stored. Only metadata is available.");
        }
        return message.downloadAttachment(id);
    }

    @Override
    public String toString() {
        return "Attachment{" + filename + ", " + sizeBytes + " bytes}";
    }
}
