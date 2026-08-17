// 401 — invalid or missing API key. (MailFlatException alt tipi.)
package net.mailflat;

public class AuthenticationException extends MailFlatException {
    public AuthenticationException(String message, int statusCode) {
        super(message, statusCode);
    }
}
