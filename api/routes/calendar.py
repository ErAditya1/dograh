from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select

from api.db.database import async_session
from api.db.models import ScheduledAppointmentModel, UserModel
from api.schemas.calendar import (
    AppointmentResponse,
    AppointmentsListResponse,
    AvailableSlotsResponse,
    CalendarSettingsResponse,
    CreateAppointmentRequest,
    UpdateAppointmentRequest,
    UpdateCalendarSettingsRequest,
)
from api.services.auth.depends import get_user
from api.services.calendar.booking_service import calendar_booking_service

router = APIRouter(
    prefix="/calendar",
    tags=["calendar"],
)


def _get_org_id(user: UserModel) -> int:
    org_id = user.selected_organization_id or (
        user.organizations[0].id if user.organizations else None
    )
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an active organization",
        )
    return org_id


@router.get("/settings", response_model=CalendarSettingsResponse)
async def get_calendar_settings(user: UserModel = Depends(get_user)):
    """Fetch current organization calendar and availability settings."""
    org_id = _get_org_id(user)
    settings = await calendar_booking_service.get_or_create_settings(org_id)
    return CalendarSettingsResponse(
        organization_id=settings.organization_id,
        timezone=settings.timezone,
        weekly_schedule=settings.weekly_schedule,
        slot_duration_mins=settings.slot_duration_mins,
        buffer_mins=settings.buffer_mins,
        max_advance_days=settings.max_advance_days,
        meeting_title_template=settings.meeting_title_template,
        location_type=settings.location_type,
        static_meeting_url=settings.static_meeting_url,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


@router.put("/settings", response_model=CalendarSettingsResponse)
async def update_calendar_settings(
    req: UpdateCalendarSettingsRequest, user: UserModel = Depends(get_user)
):
    """Update working hours, slot duration, buffer, and timezone."""
    org_id = _get_org_id(user)
    updates = req.model_dump(exclude_unset=True)
    settings = await calendar_booking_service.update_settings(org_id, updates)
    return CalendarSettingsResponse(
        organization_id=settings.organization_id,
        timezone=settings.timezone,
        weekly_schedule=settings.weekly_schedule,
        slot_duration_mins=settings.slot_duration_mins,
        buffer_mins=settings.buffer_mins,
        max_advance_days=settings.max_advance_days,
        meeting_title_template=settings.meeting_title_template,
        location_type=settings.location_type,
        static_meeting_url=settings.static_meeting_url,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


@router.get("/slots", response_model=AvailableSlotsResponse)
async def get_available_slots(
    date_str: str = Query(..., alias="date", description="Date in YYYY-MM-DD format"),
    user: UserModel = Depends(get_user),
):
    """Fetch open available booking slots for a specific date."""
    org_id = _get_org_id(user)
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD",
        )

    res = await calendar_booking_service.get_available_slots(org_id, target_date)
    return AvailableSlotsResponse(**res)


@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    req: CreateAppointmentRequest, user: UserModel = Depends(get_user)
):
    """Book a new appointment (from in-call AI or dashboard)."""
    org_id = _get_org_id(user)
    try:
        appt, _ics = await calendar_booking_service.book_appointment(
            organization_id=org_id,
            customer_name=req.customer_name,
            customer_phone=req.customer_phone,
            scheduled_start=req.scheduled_start,
            scheduled_end=req.scheduled_end,
            customer_email=req.customer_email,
            campaign_id=req.campaign_id,
            contact_id=req.contact_id,
            run_id=req.run_id,
            notes=req.notes,
            meeting_link=req.meeting_link,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return AppointmentResponse(
        id=appt.id,
        organization_id=appt.organization_id,
        campaign_id=appt.campaign_id,
        contact_id=appt.contact_id,
        run_id=appt.run_id,
        customer_name=appt.customer_name,
        customer_phone=appt.customer_phone,
        customer_email=appt.customer_email,
        scheduled_start=appt.scheduled_start,
        scheduled_end=appt.scheduled_end,
        status=appt.status,
        notes=appt.notes,
        meeting_link=appt.meeting_link,
        ics_uid=appt.ics_uid,
        created_at=appt.created_at,
        updated_at=appt.updated_at,
    )


@router.get("/appointments", response_model=AppointmentsListResponse)
async def list_appointments(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    campaign_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: UserModel = Depends(get_user),
):
    """List scheduled appointments with optional date and status filters."""
    org_id = _get_org_id(user)
    appts, total = await calendar_booking_service.list_appointments(
        organization_id=org_id,
        start_date=start_date,
        end_date=end_date,
        status=status_filter,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )

    items = [
        AppointmentResponse(
            id=a.id,
            organization_id=a.organization_id,
            campaign_id=a.campaign_id,
            contact_id=a.contact_id,
            run_id=a.run_id,
            customer_name=a.customer_name,
            customer_phone=a.customer_phone,
            customer_email=a.customer_email,
            scheduled_start=a.scheduled_start,
            scheduled_end=a.scheduled_end,
            status=a.status,
            notes=a.notes,
            meeting_link=a.meeting_link,
            ics_uid=a.ics_uid,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in appts
    ]

    return AppointmentsListResponse(total_count=total, appointments=items)


@router.patch("/appointments/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    req: UpdateAppointmentRequest,
    user: UserModel = Depends(get_user),
):
    """Reschedule, cancel or update an existing appointment."""
    org_id = _get_org_id(user)
    async with async_session() as session:
        stmt = select(ScheduledAppointmentModel).where(
            ScheduledAppointmentModel.id == appointment_id,
            ScheduledAppointmentModel.organization_id == org_id,
        )
        res = await session.execute(stmt)
        appt = res.scalars().first()
        if not appt:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

        updates = req.model_dump(exclude_unset=True)
        for k, v in updates.items():
            if v is not None and hasattr(appt, k):
                setattr(appt, k, v)

        appt.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(appt)

        return AppointmentResponse(
            id=appt.id,
            organization_id=appt.organization_id,
            campaign_id=appt.campaign_id,
            contact_id=appt.contact_id,
            run_id=appt.run_id,
            customer_name=appt.customer_name,
            customer_phone=appt.customer_phone,
            customer_email=appt.customer_email,
            scheduled_start=appt.scheduled_start,
            scheduled_end=appt.scheduled_end,
            status=appt.status,
            notes=appt.notes,
            meeting_link=appt.meeting_link,
            ics_uid=appt.ics_uid,
            created_at=appt.created_at,
            updated_at=appt.updated_at,
        )


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    appointment_id: int, user: UserModel = Depends(get_user)
):
    """Cancel / delete an appointment."""
    org_id = _get_org_id(user)
    async with async_session() as session:
        stmt = select(ScheduledAppointmentModel).where(
            ScheduledAppointmentModel.id == appointment_id,
            ScheduledAppointmentModel.organization_id == org_id,
        )
        res = await session.execute(stmt)
        appt = res.scalars().first()
        if not appt:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

        appt.status = "cancelled"
        appt.updated_at = datetime.utcnow()
        await session.commit()
