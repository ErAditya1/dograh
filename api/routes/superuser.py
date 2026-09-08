import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser
from api.services.auth.stack_auth import (
    StackAuthSessionError,
    StackAuthUserSearchError,
    stackauth,
)

router = APIRouter(prefix="/superuser", tags=["superuser"])


class ImpersonateRequest(BaseModel):
    """Request payload for superadmin impersonation.

    ``provider_user_id``, ``user_id``, or ``email`` may be supplied. If more
    than one is provided, ``provider_user_id`` takes precedence, followed by
    ``user_id`` and then ``email``.
    """

    provider_user_id: str | None = None
    user_id: int | None = None
    email: str | None = None


class ImpersonateResponse(BaseModel):
    refresh_token: str
    access_token: str


class SuperuserWorkflowRunResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: Optional[str]
    user_id: Optional[int]
    organization_id: Optional[int]
    organization_name: Optional[str]
    mode: str
    is_completed: bool
    recording_url: Optional[str]
    transcript_url: Optional[str]
    usage_info: Optional[dict]
    cost_info: Optional[dict]
    initial_context: Optional[dict]
    gathered_context: Optional[dict]
    created_at: datetime


class SuperuserWorkflowRunsListResponse(BaseModel):
    workflow_runs: List[SuperuserWorkflowRunResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


@router.post("/impersonate")
async def impersonate(
    request: ImpersonateRequest, user: UserModel = Depends(get_superuser)
) -> ImpersonateResponse:
    """Impersonate a user as a super-admin.
    Internally, Stack Auth requires the **provider user ID** (a UUID-ish string)
    to create an impersonation session.
    """

    provider_user_id = (
        request.provider_user_id.strip() if request.provider_user_id else None
    ) or None
    email = request.email.strip().lower() if request.email else None

    # ------------------------------------------------------------------
    # Fallback: resolve provider_user_id from internal ``user_id`` or email.
    # ------------------------------------------------------------------
    if provider_user_id is None:
        if request.user_id is not None:
            db_user = await db_client.get_user_by_id(request.user_id)

            if db_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with ID {request.user_id} not found.",
                )

            provider_user_id = db_user.provider_id
        elif email:
            db_user = await db_client.get_user_by_email(email)

            if db_user is not None:
                provider_user_id = db_user.provider_id
            else:
                try:
                    stack_users = await stackauth.find_users_by_email(email)
                except StackAuthUserSearchError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to search Stack Auth users.",
                    ) from exc

                if len(stack_users) == 1 and isinstance(stack_users[0].get("id"), str):
                    provider_user_id = stack_users[0]["id"]
                elif len(stack_users) > 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Multiple Stack Auth users matched that email.",
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"User with email {email} not found.",
                    )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "One of 'provider_user_id', 'user_id', or 'email' must be provided."
                ),
            )

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    try:
        session = await stackauth.impersonate(provider_user_id)
    except StackAuthSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        ) from exc

    if (
        not isinstance(session, dict)
        or "refresh_token" not in session
        or "access_token" not in session
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        )

    return ImpersonateResponse(
        refresh_token=session["refresh_token"],
        access_token=session["access_token"],
    )


@router.get("/workflow-runs")
async def get_workflow_runs(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page"),
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_superuser),
) -> SuperuserWorkflowRunsListResponse:
    """
    Get paginated list of all workflow runs with organization information.
    Requires superuser privileges.

    Filters should be provided as a JSON-encoded array of filter criteria.
    Example: [{"field": "id", "type": "number", "value": {"value": 680}}]
    """
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = None
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

    # Validate sort_order
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    workflow_runs, total_count = await db_client.get_workflow_runs_for_superadmin(
        limit=limit,
        offset=offset,
        filters=filter_criteria,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    total_pages = (total_count + limit - 1) // limit  # Ceiling division

    return SuperuserWorkflowRunsListResponse(
        workflow_runs=[SuperuserWorkflowRunResponse(**run) for run in workflow_runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get("/stats")
async def get_stats(
    user: UserModel = Depends(get_superuser),
):
    """Get live platform-wide KPIs and stats from the PostgreSQL database."""
    return await db_client.get_platform_stats()


@router.get("/clients")
async def get_clients(
    user: UserModel = Depends(get_superuser),
):
    """Get all registered client organizations with real member emails and call metrics."""
    return await db_client.get_all_organizations_with_stats()


class GrantCreditsRequest(BaseModel):
    amount: float


@router.post("/clients/{client_id}/grant-credits")
async def grant_credits(
    client_id: int,
    request: GrantCreditsRequest,
    user: UserModel = Depends(get_superuser),
):
    """Grant credits to a specific client organization."""
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Credit amount must be greater than 0")
    new_balance = await db_client.grant_organization_credits(client_id, request.amount)
    return {"status": "success", "organization_id": client_id, "new_balance": new_balance}


@router.get("/provider-keys")
async def get_provider_keys(
    user: UserModel = Depends(get_superuser),
):
    """Get live configured AI and Telephony providers across the platform."""
    from sqlalchemy.future import select
    from api.db.models import OrganizationConfigurationModel, TelephonyConfigurationModel

    keys = []
    async with db_client.async_session() as session:
        # 1. Model configuration (STT, LLM, TTS)
        model_conf = (
            await session.execute(
                select(OrganizationConfigurationModel).where(
                    OrganizationConfigurationModel.key == "MODEL_CONFIGURATION_V2"
                )
            )
        ).scalars().first()

        if model_conf and isinstance(model_conf.value, dict):
            pipe = model_conf.value.get("byok", {}).get("pipeline", {})
            stt = pipe.get("stt", {})
            if stt:
                raw_k = stt.get("api_key", [""])[0] if isinstance(stt.get("api_key"), list) else str(stt.get("api_key", ""))
                masked = (raw_k[:6] + "..." + raw_k[-4:]) if len(raw_k) > 10 else (raw_k or "Configured in Environment")
                keys.append({
                    "id": "key_stt",
                    "provider": f"{stt.get('provider', 'Deepgram').capitalize()} STT",
                    "category": "Speech-to-Text",
                    "maskedKey": masked,
                    "model": stt.get("model", "nova-3-general"),
                    "isConfigured": True,
                    "isActive": True,
                })

            llm = pipe.get("llm", {})
            if llm:
                raw_k = llm.get("api_key", [""])[0] if isinstance(llm.get("api_key"), list) else str(llm.get("api_key", ""))
                masked = (raw_k[:6] + "..." + raw_k[-4:]) if len(raw_k) > 10 else (raw_k or "Configured in Environment")
                keys.append({
                    "id": "key_llm",
                    "provider": f"{llm.get('provider', 'Groq').capitalize()} LLM",
                    "category": "Language Model",
                    "maskedKey": masked,
                    "model": llm.get("model", "openai/gpt-oss-120b"),
                    "isConfigured": True,
                    "isActive": True,
                })

            tts = pipe.get("tts", {})
            if tts:
                raw_k = tts.get("api_key", [""])[0] if isinstance(tts.get("api_key"), list) else str(tts.get("api_key", ""))
                masked = (raw_k[:6] + "..." + raw_k[-4:]) if len(raw_k) > 10 else (raw_k or "Configured in Environment")
                keys.append({
                    "id": "key_tts",
                    "provider": f"{tts.get('provider', 'Rumik').capitalize()} Silk TTS",
                    "category": "Text-to-Speech",
                    "maskedKey": masked,
                    "model": tts.get("model", "muga"),
                    "voice": tts.get("voice", "friendly conversational male, Indian accent"),
                    "isConfigured": True,
                    "isActive": True,
                })

        # 2. Telephony Configurations
        telephony_res = await session.execute(select(TelephonyConfigurationModel))
        telephony_configs = telephony_res.scalars().all()
        for t_cfg in telephony_configs:
            creds = t_cfg.credentials or {}
            key_preview = ""
            if "auth_token" in creds:
                tok = str(creds["auth_token"])
                key_preview = tok[:5] + "..." + tok[-3:] if len(tok) > 8 else tok
            elif "jwt_token" in creds:
                tok = str(creds["jwt_token"])
                key_preview = tok[:8] + "..." + tok[-4:] if len(tok) > 12 else tok
            elif "api_key" in creds:
                tok = str(creds["api_key"])
                key_preview = tok[:6] + "..." + tok[-3:] if len(tok) > 9 else tok
            else:
                key_preview = "Active Credentials"

            keys.append({
                "id": f"key_telephony_{t_cfg.id}",
                "provider": f"{t_cfg.name} ({t_cfg.provider.capitalize()})",
                "category": "Telephony Carrier",
                "maskedKey": key_preview,
                "model": "Default Outbound" if t_cfg.is_default_outbound else "Secondary",
                "isConfigured": not t_cfg.inactive,
                "isActive": not t_cfg.inactive,
            })

    return keys


@router.get("/phone-numbers")
async def get_all_phone_numbers(
    user: UserModel = Depends(get_superuser),
):
    """Get all phone numbers: both platform inventory numbers and tenant-registered numbers."""
    from sqlalchemy.future import select
    from api.db.models import (
        PlatformInventoryNumberModel,
        TelephonyPhoneNumberModel,
        TelephonyConfigurationModel,
        OrganizationModel,
    )

    async with db_client.async_session() as session:
        # Platform inventory numbers
        inv_res = await session.execute(
            select(PlatformInventoryNumberModel).order_by(PlatformInventoryNumberModel.created_at.desc())
        )
        inv_numbers = inv_res.scalars().all()
        inventory_list = [
            {
                "id": str(n.id),
                "phone_number": n.phone_number,
                "carrier": n.carrier,
                "number_type": n.number_type,
                "monthly_cost": n.monthly_cost / 100.0 if n.monthly_cost else 0.0,
                "status": n.status,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "assigned_organization_id": n.assigned_organization_id,
                "provider_credentials": n.provider_credentials or {},
            }
            for n in inv_numbers
        ]

        # Tenant numbers
        tenant_query = (
            select(
                TelephonyPhoneNumberModel,
                TelephonyConfigurationModel.name.label("config_name"),
                TelephonyConfigurationModel.provider.label("provider_name"),
                OrganizationModel.provider_id.label("org_provider_id"),
            )
            .join(
                TelephonyConfigurationModel,
                TelephonyPhoneNumberModel.telephony_configuration_id == TelephonyConfigurationModel.id,
            )
            .join(
                OrganizationModel,
                TelephonyPhoneNumberModel.organization_id == OrganizationModel.id,
            )
        )
        tenant_res = await session.execute(tenant_query)
        tenant_list = []
        for row in tenant_res.all():
            phone, cfg_name, prov, org_pid = row
            tenant_list.append({
                "id": str(phone.id),
                "phone_number": phone.address,
                "carrier": prov,
                "configuration_name": cfg_name,
                "organization_id": phone.organization_id,
                "organization_name": org_pid,
                "is_active": phone.is_active,
                "is_default_caller_id": phone.is_default_caller_id,
                "created_at": phone.created_at.isoformat() if phone.created_at else None,
            })

    return {
        "inventory": inventory_list,
        "tenant_numbers": tenant_list,
    }

