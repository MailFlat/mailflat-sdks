// A queued mail did not reach a successful delivery state. Base type for the two ways that
// happens: it failed for good (SendFailedException) or it was still queued when we stopped
// waiting (SendTimeoutException). Those are different outcomes and must not be one exception:
// a caller that retries on the first is right, and on the second sends the mail twice.
package net.mailflat;

public class SendException extends MailFlatException {

    private final Integer messageId;
    private final String sendStatus;
    private final String lastError;

    public SendException(String message, Integer messageId, String sendStatus) {
        this(message, messageId, sendStatus, null);
    }

    public SendException(String message, Integer messageId, String sendStatus, String lastError) {
        super(message);
        this.messageId = messageId;
        this.sendStatus = sendStatus;
        this.lastError = lastError;
    }

    /** The message this is about, so the caller can look it up again later. */
    public Integer messageId() {
        return messageId;
    }

    /**
     * Last {@code send_status} the server reported: {@code queued} (accepted, never
     * attempted), {@code retrying} (attempted, not through yet), {@code failed}, ...
     */
    public String sendStatus() {
        return sendStatus;
    }

    /**
     * What the most recent delivery attempt reported, or {@code null} when there has not
     * been one. On a timeout this is what separates "greylisted, try again in two minutes"
     * from a problem no amount of waiting fixes; the sentence alone described neither.
     */
    public String lastError() {
        return lastError;
    }
}
