from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FollowupConfigRequest(BaseModel):
    is_auto_enabled: bool = False
    trigger_intents: List[str] = Field(default_factory=lambda: ["interested", "appointment"])
    min_lead_score: int = Field(default=60, ge=0, le=100)
    channels: Dict[str, bool] = Field(default_factory=lambda: {"whatsapp": True, "sms": True, "email": False})
    whatsapp_template: Optional[str] = None
    sms_template: Optional[str] = None
    email_subject: Optional[str] = None
    email_body_template: Optional[str] = None


class FollowupConfigResponse(FollowupConfigRequest):
    id: Optional[int] = None
    campaign_id: int
    organization_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DispatchContactInput(BaseModel):
    contact_id: Optional[int] = None
    name: str
    phone: str
    email: Optional[str] = None
    custom_variables: Optional[Dict[str, Any]] = None


class DispatchFollowupRequest(BaseModel):
    channels: List[str] = Field(default_factory=lambda: ["whatsapp"])  # 'whatsapp', 'sms', 'email'
    contacts: List[DispatchContactInput]
    whatsapp_message: Optional[str] = None
    sms_message: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None


class DispatchResultItem(BaseModel):
    contact_id: Optional[int] = None
    phone: str
    email: Optional[str] = None
    channel: str
    status: str
    message_id: Optional[str] = None
    error: Optional[str] = None


class DispatchFollowupResponse(BaseModel):
    total_dispatched: int
    successful_count: int
    failed_count: int
    results: List[DispatchResultItem]


class NotificationLogResponse(BaseModel):
    id: int
    organization_id: int
    campaign_id: Optional[int] = None
    contact_id: Optional[int] = None
    run_id: Optional[int] = None
    channel: str
    recipient: str
    content_sent: str
    status: str
    provider_message_id: Optional[str] = None
    error_reason: Optional[str] = None
    created_at: datetime


class NotificationLogsListResponse(BaseModel):
    total_count: int
    logs: List[NotificationLogResponse]


class ChannelModeConfig(BaseModel):
    mode: str = "byok"  # 'byok'
    provider: str = "default"  # e.g. 'meta_whatsapp', 'twilio_sms', 'resend'
    credentials: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class MessagingChannelsConfigResponse(BaseModel):
    organization_id: int
    whatsapp: ChannelModeConfig
    sms: ChannelModeConfig
    email: ChannelModeConfig


class UpdateMessagingChannelsConfigRequest(BaseModel):
    whatsapp: Optional[ChannelModeConfig] = None
    sms: Optional[ChannelModeConfig] = None
    email: Optional[ChannelModeConfig] = None
