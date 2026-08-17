// Delivery failed permanently — the queue gave up and will not try again.
package net.mailflat;

public class SendFailedException extends SendException {
    public SendFailedException(String message, Integer messageId, String sendStatus) {
        super(message, messageId, sendStatus);
    }
}
