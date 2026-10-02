from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CalendarSettingsResponse(BaseModel):
    organization_id: int
    timezone: str = "Asia/Kolkata"
    weekly_schedule: Dict[str, List[str]]
    slot_duration_mins: int = 30
    buffer_mins: int = 10
    max_advance_days: int = 14
    meeting_title_template: str = "Consultation with {lead_name}"
    location_type: str = "phone_call"
    static_meeting_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UpdateCalendarSettingsRequest(BaseModel):
    timezone: Optional[str] = None
    weekly_schedule: Optional[Dict[str, List[str]]] = None
    slot_duration_mins: Optional[int] = Field(None, ge=5, le=120)
    buffer_mins: Optional[int] = Field(None, ge=0, le=60)
    max_advance_days: Optional[int] = Field(None, ge=1, le=90)
    meeting_title_template: Optional[str] = None
    location_type: Optional[str] = None
    static_meeting_url: Optional[str] = None


class AvailableSlot(BaseModel):
    start_time: str  # ISO string or HH:MM format
    end_time: str
    datetime_start: datetime
    datetime_end: datetime


class AvailableSlotsResponse(BaseModel):
    date: str  # YYYY-MM-DD
    timezone: str
    slot_duration_mins: int
    slots: List[AvailableSlot]


class CreateAppointmentRequest(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    scheduled_start: datetime
    scheduled_end: Optional[datetime] = None
    campaign_id: Optional[int] = None
    contact_id: Optional[int] = None
    run_id: Optional[int] = None
    notes: Optional[str] = None
    meeting_link: Optional[str] = None


class UpdateAppointmentRequest(BaseModel):
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    status: Optional[str] = Field(None, pattern="^(confirmed|rescheduled|cancelled|completed|no_show)$")
    notes: Optional[str] = None
    meeting_link: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: int
    organization_id: int
    campaign_id: Optional[int] = None
    contact_id: Optional[int] = None
    run_id: Optional[int] = None
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    scheduled_start: datetime
    scheduled_end: datetime
    status: str
    notes: Optional[str] = None
    meeting_link: Optional[str] = None
    ics_uid: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AppointmentsListResponse(BaseModel):
    total_count: int
    appointments: List[AppointmentResponse]
