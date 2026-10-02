import uuid
from datetime import UTC, datetime


def format_ics_datetime(dt: datetime) -> str:
    """Format datetime to UTC iCalendar format YYYYMMDDTHHMMSSZ."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def generate_ics_calendar(
    summary: str,
    description: str,
    start_time: datetime,
    end_time: datetime,
    uid: str | None = None,
    location: str | None = None,
    organizer_email: str | None = None,
    attendee_email: str | None = None,
    attendee_name: str | None = None,
) -> str:
    """
    Generate an RFC 5545 standard .ics file string.
    Works seamlessly with Google Calendar, Apple Calendar, and Outlook without OAuth.
    """
    if not uid:
        uid = f"{uuid.uuid4()}@calling-saas"
    elif "@" not in uid:
        uid = f"{uid}@calling-saas"

    now_str = format_ics_datetime(datetime.now(UTC))
    dtstart_str = format_ics_datetime(start_time)
    dtend_str = format_ics_datetime(end_time)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Calling SaaS//AI Calendar Engine//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_str}",
        f"DTSTART:{dtstart_str}",
        f"DTEND:{dtend_str}",
        f"SUMMARY:{summary.replace('\n', ' ')}",
        f"DESCRIPTION:{description.replace('\n', '\\n')}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
    ]

    if location:
        lines.append(f"LOCATION:{location.replace('\n', ' ')}")

    if organizer_email:
        lines.append(f"ORGANIZER;CN=Host:mailto:{organizer_email}")

    if attendee_email:
        cn = attendee_name or attendee_email
        lines.append(f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN={cn}:mailto:{attendee_email}")

    lines.extend([
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:Reminder",
        "TRIGGER:-PT15M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ])

    return "\r\n".join(lines) + "\r\n"
