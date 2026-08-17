// 403 — the API key does not own that inbox. (MailFlatException alt tipi.)
package net.mailflat;

public class PermissionException extends MailFlatException {
    public PermissionException(String message, int statusCode) {
        super(message, statusCode);
    }
}
