// RsvpResponse — what an inbox answers to a calendar invitation.
//
// An enum rather than a String, for the same reason as Direction: the server rejects any
// other value, and a typo in a string literal would only surface at runtime.
//
// Connected to:
//   - used by:    Inbox.rsvp(), user code
//
// Key export: RsvpResponse — ACCEPTED · DECLINED · TENTATIVE
package net.mailflat;

/** An answer to a calendar invitation. */
public enum RsvpResponse {
    ACCEPTED("accepted"),
    DECLINED("declined"),
    TENTATIVE("tentative");

    private final String wire;

    RsvpResponse(String wire) {
        this.wire = wire;
    }

    /** The value the API expects in the request body. */
    public String wire() {
        return wire;
    }
}
