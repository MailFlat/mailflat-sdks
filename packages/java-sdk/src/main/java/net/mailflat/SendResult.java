// SendResult — what the server said when it ACCEPTED a mail for delivery.
//
// The endpoint answers 202, not 200: delivery happens on a queue afterwards. This type exists
// so that fact is visible in the caller's code — a raw JSON blob lets you read `ok: true` and
// believe the mail was delivered. Ask waitUntilSent(messageId) (or subscribe to the
// message.delivered / message.failed webhook) for the outcome.
//
// Connected to:
//   - used by:    Inbox.send(), Message.reply(), Inbox.waitUntilSent()
//   - depends on: Jackson
//
// Key export: SendResult — messageId(), queued(), message(), raw()
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;

/** The 202 Accepted body of a send. Not a delivery receipt. */
public final class SendResult {

    private final JsonNode raw;

    SendResult(JsonNode raw) {
        this.raw = raw;
    }

    /**
     * Id of the queued message — the handle for {@link Inbox#waitUntilSent(int)} and
     * {@link Inbox#message(int)}. Null only if the server answered without one.
     */
    public Integer messageId() {
        return raw != null && raw.hasNonNull("message_id") ? raw.get("message_id").asInt() : null;
    }

    /** True when the mail was queued for delivery, which is the normal answer. */
    public boolean queued() {
        return raw != null && raw.path("queued").asBoolean(false);
    }

    /** The server's acknowledgement, e.g. {@code "Accepted for delivery to a@b.com"}. */
    public String message() {
        return raw != null && raw.hasNonNull("message") ? raw.get("message").asText() : null;
    }

    /** The full response body, for fields this type does not model. */
    public JsonNode raw() {
        return raw;
    }

    /** The message id, or a clear error instead of a null unboxing further down the line. */
    int requireMessageId() {
        Integer id = messageId();
        if (id == null) {
            throw new MailFlatException(
                    "The server accepted the mail but returned no message_id, so its delivery "
                            + "cannot be tracked. Response: " + raw);
        }
        return id;
    }

    @Override
    public String toString() {
        return "SendResult{messageId=" + messageId() + ", queued=" + queued() + "}";
    }
}
