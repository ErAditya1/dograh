"""Built-in calendar booking tools for AI agent workflow execution."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from loguru import logger

from api.services.calendar.booking_service import calendar_booking_service


def get_calendar_tools() -> List[Dict[str, Any]]:
    """Return function schemas for calendar booking and availability checking."""
    return [
        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": (
                    "Book a scheduled appointment or meeting on the organization's calendar. "
                    "Use this tool whenever the customer confirms or requests an appointment date and time."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Date of the appointment in YYYY-MM-DD format (e.g., '2026-10-15').",
                        },
                        "time": {
                            "type": "string",
                            "description": "Start time of the appointment (e.g., '14:00', '16:30', '4:00 PM', '11:00 AM').",
                        },
                        "customer_name": {
                            "type": "string",
                            "description": "Name of the customer. If unknown, use 'Customer'.",
                        },
                        "customer_phone": {
                            "type": "string",
                            "description": "Customer phone number. If not explicitly provided, provide caller phone or leave blank.",
                        },
                        "customer_email": {
                            "type": "string",
                            "description": "Optional email address of the customer to receive calendar invite.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional notes, meeting topic, agenda, or discussion points.",
                        },
                    },
                    "required": ["date", "time"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_available_slots",
                "description": (
                    "Check available open meeting slots on the company calendar for a specific date."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "The date to check in YYYY-MM-DD format (e.g., '2026-10-15').",
                        },
                    },
                    "required": ["date"],
                },
            },
        },
    ]


def _parse_time_str(time_str: str) -> dtime:
    """Parse various time formats such as '14:00', '4:00 PM', '4 PM', '10:30' into dtime."""
    s = time_str.strip().lower()

    # Check 12-hour AM/PM format (e.g., '4:00 pm', '4 pm', '11 am')
    match_12 = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if match_12:
        hour = int(match_12.group(1))
        minute = int(match_12.group(2) or 0)
        meridiem = match_12.group(3)
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return dtime(hour=hour, minute=minute)

    # Check 24-hour HH:MM format
    match_24 = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if match_24:
        hour = int(match_24.group(1))
        minute = int(match_24.group(2))
        return dtime(hour=hour, minute=minute)

    # Check simple integer hour (e.g. '16', '14')
    if s.isdigit():
        h = int(s)
        if 0 <= h <= 23:
            return dtime(hour=h, minute=0)

    # Fallback default 11:00 AM
    return dtime(hour=11, minute=0)


def _parse_date_str(date_str: str, tz: ZoneInfo) -> date:
    """Parse date string, supporting YYYY-MM-DD, today, tomorrow, etc."""
    s = date_str.strip().lower()
    now_in_tz = datetime.now(tz).date()

    if s in ("today", "aaj"):
        return now_in_tz
    if s in ("tomorrow", "kal"):
        return now_in_tz + timedelta(days=1)
    if s in ("day after tomorrow", "parson"):
        return now_in_tz + timedelta(days=2)

    try:
        return date.fromisoformat(s)
    except ValueError:
        # Fallback to tomorrow if parsing fails
        logger.warning(f"Could not parse date string '{date_str}', falling back to tomorrow")
        return now_in_tz + timedelta(days=1)


async def execute_check_available_slots(
    organization_id: int, date_str: str
) -> Dict[str, Any]:
    """Check open slots for an organization on a given date."""
    try:
        settings = await calendar_booking_service.get_or_create_settings(organization_id)
        try:
            tz = ZoneInfo(settings.timezone)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")

        target_date = _parse_date_str(date_str, tz)
        res = await calendar_booking_service.get_available_slots(organization_id, target_date)
        all_slots = res.get("slots", [])
        open_slots = [s["start"] for s in all_slots if s.get("available")]

        return {
            "success": True,
            "date": target_date.isoformat(),
            "timezone": res.get("timezone", settings.timezone),
            "available_slots": open_slots,
            "message": (
                f"Available slots for {target_date.isoformat()}: {', '.join(open_slots[:10])}"
                if open_slots
                else f"No open slots available for {target_date.isoformat()}."
            ),
        }
    except Exception as e:
        logger.error(f"Error checking available slots for org {organization_id}: {e}")
        return {"success": False, "error": str(e), "available_slots": []}


async def execute_book_appointment(
    organization_id: int,
    args: Dict[str, Any],
    context_vars: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute appointment booking and commit record to ScheduledAppointmentModel."""
    ctx = context_vars or {}
    try:
        settings = await calendar_booking_service.get_or_create_settings(organization_id)
        try:
            tz = ZoneInfo(settings.timezone)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")

        raw_date = str(args.get("date") or "tomorrow")
        raw_time = str(args.get("time") or "11:00")

        target_date = _parse_date_str(raw_date, tz)
        target_time = _parse_time_str(raw_time)

        # Build timezone-aware local datetime then convert to UTC
        local_start = datetime.combine(target_date, target_time, tzinfo=tz)
        scheduled_start_utc = local_start.astimezone(UTC)
        scheduled_end_utc = scheduled_start_utc + timedelta(minutes=settings.slot_duration_mins)

        # Resolve customer name & phone
        customer_name = (
            args.get("customer_name")
            or ctx.get("lead_name")
            or ctx.get("name")
            or ctx.get("customer_name")
            or "Valued Customer"
        ).strip()

        customer_phone = (
            args.get("customer_phone")
            or ctx.get("to_phone")
            or ctx.get("from_phone")
            or ctx.get("phone")
            or ctx.get("customer_phone")
            or "+919876543210"
        ).strip()

        customer_email = args.get("customer_email")
        if customer_email:
            customer_email = str(customer_email).strip()

        notes = args.get("notes") or f"Booked via Voice AI on {target_date.isoformat()}"

        run_id = ctx.get("run_id")
        campaign_id = ctx.get("campaign_id")
        contact_id = ctx.get("contact_id")

        appt, _ics = await calendar_booking_service.book_appointment(
            organization_id=organization_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            scheduled_start=scheduled_start_utc,
            scheduled_end=scheduled_end_utc,
            customer_email=customer_email,
            campaign_id=campaign_id,
            contact_id=contact_id,
            run_id=run_id,
            notes=str(notes),
        )

        formatted_time = target_time.strftime("%I:%M %p").lstrip("0")
        return {
            "success": True,
            "status": "confirmed",
            "appointment_id": appt.id,
            "customer_name": appt.customer_name,
            "date": target_date.isoformat(),
            "time": formatted_time,
            "timezone": settings.timezone,
            "meeting_link": appt.meeting_link,
            "message": (
                f"Appointment successfully booked for {appt.customer_name} on "
                f"{target_date.isoformat()} at {formatted_time} ({settings.timezone})."
            ),
        }
    except ValueError as ve:
        # Time slot conflict or validation error
        logger.warning(f"Booking conflict for org {organization_id}: {ve}")
        return {
            "success": False,
            "status": "conflict",
            "error": str(ve),
            "message": (
                f"The requested time slot is not available: {ve}. "
                "Please check available slots and ask the customer for another preferred time."
            ),
        }
    except Exception as e:
        logger.error(f"Failed to book appointment for org {organization_id}: {e}")
        return {
            "success": False,
            "status": "error",
            "error": str(e),
            "message": f"Could not schedule appointment due to an error: {e}",
        }
