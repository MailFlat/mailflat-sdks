// No OTP / message arrived before the timeout. (A MailFlatException subtype.)
package net.mailflat;

public class OtpTimeoutException extends MailFlatException {
    public OtpTimeoutException(String message) {
        super(message);
    }
}
