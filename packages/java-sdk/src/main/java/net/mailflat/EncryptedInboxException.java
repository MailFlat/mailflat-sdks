// The inbox is end-to-end encrypted, so the server cannot read its contents
// (use a non-encrypted inbox for agent automation). (MailFlatException alt tipi.)
package net.mailflat;

public class EncryptedInboxException extends MailFlatException {
    public EncryptedInboxException(String message) {
        super(message);
    }
}
