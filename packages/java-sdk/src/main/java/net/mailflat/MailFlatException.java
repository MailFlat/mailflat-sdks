// MailFlat SDK base exception — maps HTTP status codes to meaningful Java exceptions.
//
// Connected to:
//   - used by:    MailFlat, Inbox + alt tipler (Authentication/Permission/NotFound/RateLimit/Api/OtpTimeout/EncryptedInbox)
//   - depends on: yok (saf)
//
// Key export: MailFlatException (+ the static forStatus factory)
package net.mailflat;

/** Base type for every MailFlat error. Carries the HTTP status code when available. */
public class MailFlatException extends RuntimeException {
    private final int statusCode;

    public MailFlatException(String message) {
        this(message, 0);
    }

    public MailFlatException(String message, int statusCode) {
        super(message);
        this.statusCode = statusCode;
    }

    /** HTTP status code that caused this error, or 0 if not HTTP-related. */
    public int statusCode() {
        return statusCode;
    }

    /** Maps an HTTP status code + server detail to the matching MailFlatException subtype. */
    public static MailFlatException forStatus(int statusCode, String detail) {
        switch (statusCode) {
            case 401:
                return new AuthenticationException(
                        detail != null ? detail : "Invalid or missing API key", statusCode);
            case 403:
                return new PermissionException(
                        detail != null ? detail : "This API key does not own that inbox", statusCode);
            case 404:
                return new NotFoundException(detail != null ? detail : "Not found", statusCode);
            case 429:
                return new RateLimitException(
                        detail != null ? detail : "Rate limit exceeded", statusCode);
            default:
                return new ApiException(
                        detail != null ? detail : "Request failed with status " + statusCode, statusCode);
        }
    }
}
