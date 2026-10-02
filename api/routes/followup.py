from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from api.db.models import UserModel
from api.schemas.followup import (
    DispatchFollowupRequest,
    DispatchFollowupResponse,
    DispatchResultItem,
    FollowupConfigRequest,
    FollowupConfigResponse,
    MessagingChannelsConfigResponse,
    NotificationLogResponse,
    NotificationLogsListResponse,
    UpdateMessagingChannelsConfigRequest,
)
from api.services.auth.depends import get_user
from api.services.notifications.dispatcher import notification_dispatcher

router = APIRouter(
    tags=["followup"],
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


@router.get("/campaigns/{campaign_id}/follow-up/config", response_model=FollowupConfigResponse)
async def get_campaign_followup_config(
    campaign_id: int, user: UserModel = Depends(get_user)
):
    """Fetch automated follow-up rules for a campaign."""
    org_id = _get_org_id(user)
    cfg = await notification_dispatcher.get_or_create_config(campaign_id, org_id)
    return FollowupConfigResponse(
        id=cfg.id,
        campaign_id=cfg.campaign_id,
        organization_id=cfg.organization_id,
        is_auto_enabled=cfg.is_auto_enabled,
        trigger_intents=cfg.trigger_intents,
        min_lead_score=cfg.min_lead_score,
        channels=cfg.channels,
        whatsapp_template=cfg.whatsapp_template,
        sms_template=cfg.sms_template,
        email_subject=cfg.email_subject,
        email_body_template=cfg.email_body_template,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.put("/campaigns/{campaign_id}/follow-up/config", response_model=FollowupConfigResponse)
async def update_campaign_followup_config(
    campaign_id: int, req: FollowupConfigRequest, user: UserModel = Depends(get_user)
):
    """Save or update automated follow-up rules for a campaign."""
    org_id = _get_org_id(user)
    updates = req.model_dump()
    cfg = await notification_dispatcher.update_config(campaign_id, org_id, updates)
    return FollowupConfigResponse(
        id=cfg.id,
        campaign_id=cfg.campaign_id,
        organization_id=cfg.organization_id,
        is_auto_enabled=cfg.is_auto_enabled,
        trigger_intents=cfg.trigger_intents,
        min_lead_score=cfg.min_lead_score,
        channels=cfg.channels,
        whatsapp_template=cfg.whatsapp_template,
        sms_template=cfg.sms_template,
        email_subject=cfg.email_subject,
        email_body_template=cfg.email_body_template,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.post("/campaigns/{campaign_id}/follow-up/dispatch", response_model=DispatchFollowupResponse)
async def dispatch_campaign_followup(
    campaign_id: int, req: DispatchFollowupRequest, user: UserModel = Depends(get_user)
):
    """Batch dispatch multi-channel (WhatsApp, SMS, Email) follow-ups to selected leads."""
    org_id = _get_org_id(user)
    contacts_data = [c.model_dump() for c in req.contacts]

    result = await notification_dispatcher.dispatch_followup(
        organization_id=org_id,
        campaign_id=campaign_id,
        channels=req.channels,
        contacts=contacts_data,
        whatsapp_message=req.whatsapp_message,
        sms_message=req.sms_message,
        email_subject=req.email_subject,
        email_body=req.email_body,
    )

    items = [
        DispatchResultItem(
            contact_id=r.get("contact_id"),
            phone=r.get("phone", ""),
            email=r.get("email"),
            channel=r.get("channel", "whatsapp"),
            status=r.get("status", "sent"),
            message_id=r.get("message_id"),
        )
        for r in result["results"]
    ]

    return DispatchFollowupResponse(
        total_dispatched=result["total_dispatched"],
        successful_count=result["successful_count"],
        failed_count=result["failed_count"],
        results=items,
    )


@router.get("/notifications/logs", response_model=NotificationLogsListResponse)
async def get_notification_logs(
    campaign_id: Optional[int] = None,
    channel: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: UserModel = Depends(get_user),
):
    """View audit history of sent notifications."""
    org_id = _get_org_id(user)
    logs, total = await notification_dispatcher.list_notification_logs(
        organization_id=org_id,
        campaign_id=campaign_id,
        channel=channel,
        limit=limit,
        offset=offset,
    )

    items = [
        NotificationLogResponse(
            id=log.id,
            organization_id=log.organization_id,
            campaign_id=log.campaign_id,
            contact_id=log.contact_id,
            run_id=log.run_id,
            channel=log.channel,
            recipient=log.recipient,
            content_sent=log.content_sent,
            status=log.status,
            provider_message_id=log.provider_message_id,
            error_reason=log.error_reason,
            created_at=log.created_at,
        )
        for log in logs
    ]

    return NotificationLogsListResponse(total_count=total, logs=items)


@router.get("/messaging-configurations", response_model=MessagingChannelsConfigResponse)
async def get_messaging_configurations(user: UserModel = Depends(get_user)):
    """Retrieve organization messaging channel credentials and settings."""
    org_id = _get_org_id(user)
    cfg = await notification_dispatcher.get_messaging_channel_config(org_id)
    return MessagingChannelsConfigResponse(
        organization_id=org_id,
        whatsapp=cfg.get("whatsapp", {"mode": "byok", "provider": "meta_whatsapp", "credentials": {}, "is_active": True}),
        sms=cfg.get("sms", {"mode": "byok", "provider": "twilio_sms", "credentials": {}, "is_active": True}),
        email=cfg.get("email", {"mode": "byok", "provider": "resend", "credentials": {}, "is_active": True}),
    )


@router.put("/messaging-configurations", response_model=MessagingChannelsConfigResponse)
async def update_messaging_configurations(
    req: UpdateMessagingChannelsConfigRequest,
    user: UserModel = Depends(get_user),
):
    """Update organization messaging channel credentials and settings."""
    org_id = _get_org_id(user)
    updates = {}
    if req.whatsapp is not None:
        updates["whatsapp"] = req.whatsapp.model_dump()
    if req.sms is not None:
        updates["sms"] = req.sms.model_dump()
    if req.email is not None:
        updates["email"] = req.email.model_dump()

    cfg = await notification_dispatcher.save_messaging_channel_config(org_id, updates)
    return MessagingChannelsConfigResponse(
        organization_id=org_id,
        whatsapp=cfg.get("whatsapp", {"mode": "byok", "provider": "meta_whatsapp", "credentials": {}, "is_active": True}),
        sms=cfg.get("sms", {"mode": "byok", "provider": "twilio_sms", "credentials": {}, "is_active": True}),
        email=cfg.get("email", {"mode": "byok", "provider": "resend", "credentials": {}, "is_active": True}),
    )
