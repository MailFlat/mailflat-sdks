// Any other non-2xx HTTP status. (MailFlatException alt tipi.)
package net.mailflat;

public class ApiException extends MailFlatException {
    public ApiException(String message, int statusCode) {
        super(message, statusCode);
    }
}
