// 404 — inbox or resource not found. (MailFlatException alt tipi.)
package net.mailflat;

public class NotFoundException extends MailFlatException {
    public NotFoundException(String message, int statusCode) {
        super(message, statusCode);
    }
}
