// We stopped waiting while the mail was still queued. NOT a failure: the queue keeps
// retrying, so treating this as "it never went out" and sending again delivers it twice.
package net.mailflat;

public class SendTimeoutException extends SendException {
    public SendTimeoutException(String message, Integer messageId, String sendStatus) {
        this(message, messageId, sendStatus, null);
    }

    public SendTimeoutException(String message, Integer messageId, String sendStatus,
                                String lastError) {
        super(message, messageId, sendStatus, lastError);
    }
}
