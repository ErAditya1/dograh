from datetime import UTC, date, datetime, timedelta
import pytest

from api.services.calendar.availability_engine import compute_available_slots
from api.services.calendar.ics_generator import generate_ics_calendar
from api.services.notifications.dispatcher import interpolate_template


def test_compute_available_slots_basic():
    # Monday 10:00 to 12:00, 30m slots, 0 buffer
    weekly_schedule = {
        "mon": ["10:00-12:00"],
        "tue": [],
        "wed": [],
        "thu": [],
        "fri": [],
        "sat": [],
        "sun": [],
    }
    target_date = date(2026, 10, 5)  # A Monday
    now_utc = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # 09:30 AM IST

    slots = compute_available_slots(
        target_date=target_date,
        weekly_schedule=weekly_schedule,
        slot_duration_mins=30,
        buffer_mins=0,
        timezone_name="Asia/Kolkata",
        existing_appointments=[],
        now_utc=now_utc,
    )

    # Expected slots: 10:00-10:30, 10:30-11:00, 11:00-11:30, 11:30-12:00
    assert len(slots) == 4
    assert slots[0]["start_time"] == "10:00"
    assert slots[0]["end_time"] == "10:30"
    assert slots[3]["start_time"] == "11:30"
    assert slots[3]["end_time"] == "12:00"


def test_compute_available_slots_collision_and_past():
    weekly_schedule = {
        "mon": ["10:00-13:00"],
        "tue": [],
        "wed": [],
        "thu": [],
        "fri": [],
        "sat": [],
        "sun": [],
    }
    target_date = date(2026, 10, 5)  # Monday
    # Assume current time is 10:15 AM IST, so 10:00-10:30 slot has already started/past
    now_utc = datetime(2026, 10, 5, 4, 45, tzinfo=UTC)  # 10:15 AM IST

    # Existing busy appointment: 11:00-11:30
    appt_start = datetime(2026, 10, 5, 5, 30, tzinfo=UTC)  # 11:00 AM IST
    appt_end = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)    # 11:30 AM IST

    slots = compute_available_slots(
        target_date=target_date,
        weekly_schedule=weekly_schedule,
        slot_duration_mins=30,
        buffer_mins=10,
        timezone_name="Asia/Kolkata",
        existing_appointments=[(appt_start, appt_end)],
        now_utc=now_utc,
    )

    start_times = [s["start_time"] for s in slots]
    # 10:00 slot must be skipped because it started in past
    assert "10:00" not in start_times
    # 11:00 slot must be skipped because of collision
    assert "11:00" not in start_times


def test_generate_ics_calendar():
    start = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    end = datetime(2026, 10, 5, 10, 30, tzinfo=UTC)
    ics_text = generate_ics_calendar(
        summary="Consultation with John Doe",
        description="Discuss AI Calling plan",
        start_time=start,
        end_time=end,
        uid="appt-12345",
        attendee_email="john@example.com",
        attendee_name="John Doe",
    )

    assert "BEGIN:VCALENDAR" in ics_text
    assert "VERSION:2.0" in ics_text
    assert "BEGIN:VEVENT" in ics_text
    assert "UID:appt-12345@calling-saas" in ics_text
    assert "SUMMARY:Consultation with John Doe" in ics_text
    assert "ATTENDEE;CUTYPE=INDIVIDUAL" in ics_text
    assert "mailto:john@example.com" in ics_text
    assert "END:VCALENDAR" in ics_text


def test_interpolate_template():
    tpl = "Hi {name}, your slot on {appointment_time} is confirmed! Link: {booking_link}"
    vars_data = {
        "name": "Priya Sharma",
        "appointment_time": "Tuesday 3:00 PM",
        "booking_link": "https://callio.ai/meet/123",
    }
    result = interpolate_template(tpl, vars_data)
    assert result == "Hi Priya Sharma, your slot on Tuesday 3:00 PM is confirmed! Link: https://callio.ai/meet/123"

    # Test case insensitivity
    tpl2 = "Hello {FIRST_NAME}, call at {Phone}"
    res2 = interpolate_template(tpl2, {"name": "Amit Patel", "phone": "+919876543210"})
    assert res2 == "Hello Amit, call at +919876543210"


@pytest.mark.asyncio
async def test_messaging_channel_config_system_and_byok(async_session):
    from api.services.notifications.dispatcher import notification_dispatcher
    from api.db.models import OrganizationModel

    org = OrganizationModel(provider_id="org_test_123")
    async_session.add(org)
    await async_session.commit()
    await async_session.refresh(org)
    org_id = org.id

    # Default configuration returns system mode
    cfg = await notification_dispatcher.get_messaging_channel_config(
        organization_id=org_id, session=async_session
    )
    assert cfg["whatsapp"]["mode"] == "system"
    assert cfg["sms"]["mode"] == "system"
    assert cfg["email"]["mode"] == "system"

    # Update to BYOK mode for WhatsApp
    updates = {
        "whatsapp": {
            "mode": "byok",
            "provider": "meta_whatsapp",
            "credentials": {
                "phone_number_id": "1029384756",
                "access_token": "EAA_test_token_123",
            },
            "is_active": True,
        }
    }
    updated = await notification_dispatcher.save_messaging_channel_config(
        org_id, updates, session=async_session
    )
    assert updated["whatsapp"]["mode"] == "byok"
    assert updated["whatsapp"]["credentials"]["phone_number_id"] == "1029384756"
    assert updated["sms"]["mode"] == "system"  # Unaffected channel remains system


