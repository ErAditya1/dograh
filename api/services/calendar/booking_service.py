import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import async_session
from api.db.models import OrganizationCalendarSettingsModel, ScheduledAppointmentModel
from api.services.calendar.availability_engine import compute_available_slots
from api.services.calendar.ics_generator import generate_ics_calendar

DEFAULT_WEEKLY_SCHEDULE = {
    "mon": ["10:00-19:00"],
    "tue": ["10:00-19:00"],
    "wed": ["10:00-19:00"],
    "thu": ["10:00-19:00"],
    "fri": ["10:00-19:00"],
    "sat": ["10:00-17:00"],
    "sun": [],
}


class CalendarBookingService:
    async def get_or_create_settings(
        self, organization_id: int, session: AsyncSession | None = None
    ) -> OrganizationCalendarSettingsModel:
        """Fetch organization calendar settings or initialize default."""
        close_session = False
        if session is None:
            session = async_session()
            close_session = True

        try:
            stmt = select(OrganizationCalendarSettingsModel).where(
                OrganizationCalendarSettingsModel.organization_id == organization_id
            )
            result = await session.execute(stmt)
            settings = result.scalars().first()

            if not settings:
                settings = OrganizationCalendarSettingsModel(
                    organization_id=organization_id,
                    timezone="Asia/Kolkata",
                    weekly_schedule=DEFAULT_WEEKLY_SCHEDULE,
                    slot_duration_mins=30,
                    buffer_mins=10,
                    max_advance_days=14,
                    meeting_title_template="Consultation with {lead_name}",
                    location_type="phone_call",
                )
                session.add(settings)
                await session.commit()
                await session.refresh(settings)

            return settings
        finally:
            if close_session:
                await session.close()

    async def update_settings(
        self, organization_id: int, updates: Dict[str, Any]
    ) -> OrganizationCalendarSettingsModel:
        """Update organization calendar availability settings."""
        async with async_session() as session:
            settings = await self.get_or_create_settings(organization_id, session=session)
            for k, v in updates.items():
                if v is not None and hasattr(settings, k):
                    setattr(settings, k, v)

            settings.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(settings)
            return settings

    async def get_available_slots(
        self, organization_id: int, target_date: date
    ) -> Dict[str, Any]:
        """Fetch computed available slots for an organization on a target date."""
        async with async_session() as session:
            settings = await self.get_or_create_settings(organization_id, session=session)

            try:
                tz = ZoneInfo(settings.timezone)
            except Exception:
                tz = ZoneInfo("Asia/Kolkata")

            # Fetch active appointments for that target date (day start to day end in org tz)
            day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).astimezone(UTC)
            day_end = datetime.combine(target_date, datetime.max.time(), tzinfo=tz).astimezone(UTC)

            stmt = select(ScheduledAppointmentModel).where(
                and_(
                    ScheduledAppointmentModel.organization_id == organization_id,
                    ScheduledAppointmentModel.status.in_(["confirmed", "rescheduled"]),
                    ScheduledAppointmentModel.scheduled_start <= day_end,
                    ScheduledAppointmentModel.scheduled_end >= day_start,
                )
            )
            result = await session.execute(stmt)
            appts = result.scalars().all()

            busy_intervals = [(a.scheduled_start, a.scheduled_end) for a in appts]

            slots = compute_available_slots(
                target_date=target_date,
                weekly_schedule=settings.weekly_schedule,
                slot_duration_mins=settings.slot_duration_mins,
                buffer_mins=settings.buffer_mins,
                timezone_name=settings.timezone,
                existing_appointments=busy_intervals,
            )

            return {
                "date": target_date.isoformat(),
                "timezone": settings.timezone,
                "slot_duration_mins": settings.slot_duration_mins,
                "slots": slots,
            }

    async def book_appointment(
        self,
        organization_id: int,
        customer_name: str,
        customer_phone: str,
        scheduled_start: datetime,
        scheduled_end: Optional[datetime] = None,
        customer_email: Optional[str] = None,
        campaign_id: Optional[int] = None,
        contact_id: Optional[int] = None,
        run_id: Optional[int] = None,
        notes: Optional[str] = None,
        meeting_link: Optional[str] = None,
    ) -> Tuple[ScheduledAppointmentModel, str]:
        """
        Book an appointment and generate RFC 5545 .ics invite.
        Validates against overlapping double bookings.
        """
        async with async_session() as session:
            settings = await self.get_or_create_settings(organization_id, session=session)

            if scheduled_start.tzinfo is None:
                scheduled_start = scheduled_start.replace(tzinfo=UTC)

            if scheduled_end is None:
                scheduled_end = scheduled_start + timedelta(minutes=settings.slot_duration_mins)
            elif scheduled_end.tzinfo is None:
                scheduled_end = scheduled_end.replace(tzinfo=UTC)

            # Check overlap collision
            stmt = select(ScheduledAppointmentModel).where(
                and_(
                    ScheduledAppointmentModel.organization_id == organization_id,
                    ScheduledAppointmentModel.status.in_(["confirmed", "rescheduled"]),
                    ScheduledAppointmentModel.scheduled_start < scheduled_end,
                    ScheduledAppointmentModel.scheduled_end > scheduled_start,
                )
            )
            res = await session.execute(stmt)
            existing = res.scalars().first()
            if existing:
                raise ValueError("Selected slot is already booked. Please choose another time.")

            ics_uid = str(uuid.uuid4())
            appt = ScheduledAppointmentModel(
                organization_id=organization_id,
                campaign_id=campaign_id,
                contact_id=contact_id,
                run_id=run_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
                customer_email=customer_email,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                status="confirmed",
                notes=notes,
                meeting_link=meeting_link or settings.static_meeting_url,
                ics_uid=ics_uid,
            )
            session.add(appt)
            await session.commit()
            await session.refresh(appt)

            title = settings.meeting_title_template.replace("{lead_name}", customer_name)
            desc = f"Callio AI Appointment with {customer_name}.\nPhone: {customer_phone}"
            if notes:
                desc += f"\nNotes: {notes}"

            ics_content = generate_ics_calendar(
                summary=title,
                description=desc,
                start_time=scheduled_start,
                end_time=scheduled_end,
                uid=ics_uid,
                location=appt.meeting_link or "Phone Call",
                attendee_email=customer_email,
                attendee_name=customer_name,
            )

            return appt, ics_content

    async def list_appointments(
        self,
        organization_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: Optional[str] = None,
        campaign_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[ScheduledAppointmentModel], int]:
        """Query appointments with filtering."""
        async with async_session() as session:
            conditions = [ScheduledAppointmentModel.organization_id == organization_id]
            if start_date:
                conditions.append(ScheduledAppointmentModel.scheduled_start >= start_date)
            if end_date:
                conditions.append(ScheduledAppointmentModel.scheduled_start <= end_date)
            if status:
                conditions.append(ScheduledAppointmentModel.status == status)
            if campaign_id:
                conditions.append(ScheduledAppointmentModel.campaign_id == campaign_id)

            stmt = (
                select(ScheduledAppointmentModel)
                .where(and_(*conditions))
                .order_by(ScheduledAppointmentModel.scheduled_start.asc())
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            appointments = list(result.scalars().all())

            # Count total
            count_stmt = select(ScheduledAppointmentModel).where(and_(*conditions))
            count_res = await session.execute(count_stmt)
            total = len(count_res.scalars().all())

            return appointments, total


calendar_booking_service = CalendarBookingService()
