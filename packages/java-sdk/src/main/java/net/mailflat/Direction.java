// Direction — which way a message travelled, for the read filters.
//
// An enum rather than a String on purpose: the server rejects unknown values, and a typo in
// a string literal would only surface as a 400 at runtime.
//
// Connected to:
//   - used by:    Inbox (messages/latest/waitForMessage), user code
//
// Key export: Direction — IN (default) · OUT · ALL
package net.mailflat;

/**
 * Message direction filter.
 *
 * <p>{@link #IN} is the default everywhere a filter is optional. That is deliberate: on the
 * agent surface "messages" means <em>incoming</em> mail. Without the filter a
 * {@code waitForMessage()} right after {@code send()} returned the agent's own outgoing
 * message and the wait finished instantly with the wrong mail.
 */
public enum Direction {
    IN("in"),
    OUT("out"),
    ALL("all");

    private final String wire;

    Direction(String wire) {
        this.wire = wire;
    }

    /** The value the API expects on the query string. */
    public String wire() {
        return wire;
    }
}
