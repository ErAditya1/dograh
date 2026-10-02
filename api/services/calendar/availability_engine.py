from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo
from loguru import logger

DAY_ABBREVIATIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_time_range(range_str: str) -> Tuple[time, time]:
    """Parse time range string like '10:00-19:00' to (time(10,0), time(19,0))."""
    start_s, end_s = range_str.split("-")
    sh, sm = map(int, start_s.strip().split(":"))
    eh, em = map(int, end_s.strip().split(":"))
    return time(sh, sm), time(eh, em)


def compute_available_slots(
    target_date: date,
    weekly_schedule: Dict[str, List[str]],
    slot_duration_mins: int,
    buffer_mins: int,
    timezone_name: str,
    existing_appointments: List[Tuple[datetime, datetime]],
    now_utc: datetime | None = None,
) -> List[Dict[str, Any]]:
    """
    Computes all valid, non-overlapping available slots for a target_date.
    - Resolves organization timezone.
    - Excludes slots in the past.
    - Excludes slots colliding with existing appointments or their buffer zones.
    """
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")

    if now_utc is None:
        now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)

    day_name = DAY_ABBREVIATIONS[target_date.weekday()]
    day_ranges = weekly_schedule.get(day_name, [])

    if not day_ranges:
        return []

    slot_delta = timedelta(minutes=slot_duration_mins)
    buffer_delta = timedelta(minutes=buffer_mins)
    available_slots: List[Dict[str, Any]] = []

    # Convert existing appointments to local timezone intervals
    busy_intervals: List[Tuple[datetime, datetime]] = []
    for appt_start, appt_end in existing_appointments:
        if appt_start.tzinfo is None:
            appt_start = appt_start.replace(tzinfo=UTC)
        if appt_end.tzinfo is None:
            appt_end = appt_end.replace(tzinfo=UTC)
        busy_intervals.append((appt_start.astimezone(tz), appt_end.astimezone(tz)))

    for r_str in day_ranges:
        try:
            start_t, end_t = parse_time_range(r_str)
        except Exception as e:
            logger.warning(f"Invalid time range format: {r_str}, err: {e}")
            continue

        window_start = datetime.combine(target_date, start_t, tzinfo=tz)
        window_end = datetime.combine(target_date, end_t, tzinfo=tz)

        current_slot_start = window_start
        while current_slot_start + slot_delta <= window_end:
            current_slot_end = current_slot_start + slot_delta

            # 1. Check if slot is in the past
            if current_slot_start <= now_local:
                current_slot_start = current_slot_end + buffer_delta
                continue

            # 2. Check collision with existing appointments (including buffer)
            is_collision = False
            for busy_start, busy_end in busy_intervals:
                # Collision condition: slot interval overlaps with busy interval
                if max(current_slot_start, busy_start) < min(current_slot_end, busy_end):
                    is_collision = True
                    break

            if not is_collision:
                available_slots.append({
                    "start_time": current_slot_start.strftime("%H:%M"),
                    "end_time": current_slot_end.strftime("%H:%M"),
                    "datetime_start": current_slot_start,
                    "datetime_end": current_slot_end,
                })

            current_slot_start = current_slot_end + buffer_delta

    return available_slots
