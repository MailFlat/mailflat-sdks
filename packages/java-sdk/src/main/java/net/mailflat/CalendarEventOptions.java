// CalendarEventOptions — the fields of a meeting this inbox organizes (plan 379 phase 2).
//
// One builder for both calls: createCalendarEvent() needs title + start + attendees,
// updateCalendarEvent() takes only what changes. It is the ONLY place that builds those
// request bodies, so a field added later cannot reach one call and miss the other.
//
// Connected to:
//   - used by:    Inbox.createCalendarEvent / updateCalendarEvent, user code
//   - depends on: Jackson
//
// Key export: CalendarEventOptions.builder()...build()
package net.mailflat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * A meeting to schedule or the changes to one.
 *
 * <p>{@code start} is ISO 8601 with an offset ({@code "2026-10-06T14:00:00-04:00"}), or without
 * one plus {@code timezone} ({@code "America/New_York"}). Give {@code end} or
 * {@code durationMinutes} (default 30). All-day: {@code allDay(true)} with dates, end exclusive.
 */
public final class CalendarEventOptions {

    /** Someone to invite. */
    public static final class Attendee {
        final String email;
        final String name;
        final boolean optional;

        Attendee(String email, String name, boolean optional) {
            this.email = email;
            this.name = name;
            this.optional = optional;
        }

        public String email()      { return email; }
        public String name()       { return name; }
        public boolean optional()  { return optional; }
    }

    final String title;
    final String start;
    final String end;
    final Integer durationMinutes;
    final String timezone;
    final Boolean allDay;
    final String location;
    final String description;
    final String message;
    final String inReplyTo;
    final List<Attendee> attendees;

    private CalendarEventOptions(Builder b) {
        this.title = b.title;
        this.start = b.start;
        this.end = b.end;
        this.durationMinutes = b.durationMinutes;
        this.timezone = b.timezone;
        this.allDay = b.allDay;
        this.location = b.location;
        this.description = b.description;
        this.message = b.message;
        this.inReplyTo = b.inReplyTo;
        this.attendees = Collections.unmodifiableList(new ArrayList<>(b.attendees));
    }

    public static Builder builder() {
        return new Builder();
    }

    public List<Attendee> attendees() { return attendees; }

    /** Body for {@code POST .../calendar/events}. Title, start and one attendee are required. */
    ObjectNode toCreatePayload(ObjectMapper mapper) {
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("title is required");
        }
        if (start == null || start.isBlank()) {
            throw new IllegalArgumentException("start is required");
        }
        if (attendees.isEmpty()) {
            throw new IllegalArgumentException("at least one attendee is required");
        }
        ObjectNode body = common(mapper);
        ArrayNode people = body.putArray("attendees");
        for (Attendee a : attendees) {
            if (a.name == null && !a.optional) {
                people.add(a.email);
            } else {
                ObjectNode o = people.addObject();
                o.put("email", a.email);
                if (a.name != null) {
                    o.put("name", a.name);
                }
                if (a.optional) {
                    o.put("optional", true);
                }
            }
        }
        if (inReplyTo != null) {
            body.put("in_reply_to", inReplyTo);
        }
        return body;
    }

    /** Body for {@code PATCH .../calendar/events/{id}}: only what changes, at least one field. */
    ObjectNode toUpdatePayload(ObjectMapper mapper) {
        if (!attendees.isEmpty() || inReplyTo != null) {
            throw new IllegalArgumentException(
                    "attendees and inReplyTo cannot be changed on an existing meeting; "
                    + "cancel it and create a new one");
        }
        ObjectNode body = common(mapper);
        if (body.isEmpty()) {
            throw new IllegalArgumentException("updateCalendarEvent needs at least one field to change");
        }
        return body;
    }

    private ObjectNode common(ObjectMapper mapper) {
        ObjectNode body = mapper.createObjectNode();
        putIf(body, "title", title);
        putIf(body, "start", start);
        putIf(body, "end", end);
        if (durationMinutes != null) {
            body.put("duration_minutes", durationMinutes);
        }
        putIf(body, "timezone", timezone);
        if (allDay != null) {
            body.put("all_day", allDay);
        }
        putIf(body, "location", location);
        putIf(body, "description", description);
        putIf(body, "message", message);
        return body;
    }

    private static void putIf(ObjectNode body, String field, String value) {
        if (value != null) {
            body.put(field, value);
        }
    }

    public static final class Builder {
        private String title;
        private String start;
        private String end;
        private Integer durationMinutes;
        private String timezone;
        private Boolean allDay;
        private String location;
        private String description;
        private String message;
        private String inReplyTo;
        private final List<Attendee> attendees = new ArrayList<>();

        private Builder() {
        }

        public Builder title(String v)             { this.title = v; return this; }
        public Builder start(String v)             { this.start = v; return this; }
        public Builder end(String v)               { this.end = v; return this; }
        public Builder durationMinutes(int v)      { this.durationMinutes = v; return this; }
        /** IANA zone, e.g. {@code "America/New_York"}. Needed when start has no offset. */
        public Builder timezone(String v)          { this.timezone = v; return this; }
        public Builder allDay(boolean v)           { this.allDay = v; return this; }
        public Builder location(String v)          { this.location = v; return this; }
        public Builder description(String v)       { this.description = v; return this; }
        /** A short note at the top of the invitation email. */
        public Builder message(String v)           { this.message = v; return this; }
        /** Message-ID of an email to thread the invitation under (create only). */
        public Builder inReplyTo(String v)         { this.inReplyTo = v; return this; }

        public Builder attendee(String email) {
            return attendee(email, null, false);
        }

        public Builder optionalAttendee(String email) {
            return attendee(email, null, true);
        }

        public Builder attendee(String email, String name, boolean optional) {
            if (email == null || email.isBlank()) {
                throw new IllegalArgumentException("attendee email is required");
            }
            attendees.add(new Attendee(email, name, optional));
            return this;
        }

        public CalendarEventOptions build() {
            return new CalendarEventOptions(this);
        }
    }
}
