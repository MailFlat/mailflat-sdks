// CalendarEvent — one meeting on an inbox calendar, built from the invitations it received.
//
// Connected to:
//   - used by:    Inbox (calendarEvents / calendarEvent / rsvp), user code
//   - depends on: Jackson JsonNode
//
// Key export: CalendarEvent — id(), uid(), title(), start(), end(), allDay(), organizerEmail(),
//             status(), myStatus(), isCancelled(), attendees(), raw()
package net.mailflat;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * A calendar event. Times are UTC ({@code 2026-10-01T14:00:00Z}), or a plain date for all-day
 * events. An event outlives the email that carried it: retention can delete the invitation,
 * the meeting stays. If the organizer moves the meeting, {@link #myStatus()} goes back to
 * {@code "needs-action"}: the old answer was for the old time.
 */
public final class CalendarEvent {
    private final JsonNode raw;

    private CalendarEvent(JsonNode raw) {
        this.raw = raw;
    }

    static CalendarEvent fromJson(JsonNode d) {
        return new CalendarEvent(d);
    }

    private String text(String field) {
        JsonNode n = raw.get(field);
        return (n == null || n.isNull()) ? null : n.asText();
    }

    public Integer id()            { return raw.hasNonNull("id") ? raw.get("id").asInt() : null; }
    public String uid()            { return text("uid"); }
    public String title()          { return text("title"); }
    public String start()          { return text("start"); }
    public String end()            { return text("end"); }
    public boolean allDay()        { return raw.path("all_day").asBoolean(false); }
    /** The organizer's time zone name, for display only (times are already UTC). */
    public String timezone()       { return text("timezone"); }
    public String location()       { return text("location"); }
    public String description()    { return text("description"); }
    public String organizerEmail() { return raw.path("organizer").hasNonNull("email")
                                            ? raw.path("organizer").get("email").asText() : null; }
    public String organizerName()  { return raw.path("organizer").hasNonNull("name")
                                            ? raw.path("organizer").get("name").asText() : null; }
    /** {@code "confirmed"}, {@code "tentative"} or {@code "cancelled"}. */
    public String status()         { return text("status"); }
    public boolean isCancelled()   { return "cancelled".equals(status()); }
    /** This inbox's answer: needs-action, accepted, declined or tentative. */
    public String myStatus()       { return text("my_status"); }
    public int sequence()          { return raw.path("sequence").asInt(0); }
    public boolean recurring()     { return raw.path("recurring").asBoolean(false); }
    public Integer lastMessageId() { return raw.hasNonNull("last_message_id")
                                            ? raw.get("last_message_id").asInt() : null; }
    /** Array of {email, name, status, role}. */
    public JsonNode attendees()    { return raw.path("attendees"); }
    /** The full backend JSON for this event. */
    public JsonNode raw()          { return raw; }

    @Override
    public String toString() {
        return "CalendarEvent{" + id() + ", " + title() + ", " + start() + ", " + myStatus() + "}";
    }
}
