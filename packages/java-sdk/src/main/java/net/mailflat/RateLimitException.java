// 429 — rate limit exceeded. (MailFlatException alt tipi.)
package net.mailflat;

public class RateLimitException extends MailFlatException {
    public RateLimitException(String message, int statusCode) {
        super(message, statusCode);
    }
}
