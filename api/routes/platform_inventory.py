from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from api.db import db_client
from api.db.models import (
    PlatformInventoryNumberModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
)
from api.services.auth.depends import get_superuser, get_user

router = APIRouter(prefix="/platform/numbers", tags=["platform-inventory"])


class PlatformNumberCreateRequest(BaseModel):
    phone_number: str
    provider: str  # twilio, smartflo, telnyx, vonage, plivo
    carrier: str   # Twilio, Tata Smartflo, Telnyx, etc.
    number_type: str = "shared_trial"  # shared_trial, dedicated
    country_code: str = "US"
    monthly_cost: float = 0.0
    status: str = "available"
    provider_config: Dict[str, Any] = {}


class PlatformNumberResponse(BaseModel):
    id: int
    phone_number: str
    provider: str
    carrier: str
    number_type: str
    country_code: str
    monthly_cost: float
    status: str
    assigned_organization_id: Optional[int] = None
    created_at: Optional[datetime] = None
    provider_config: Optional[Dict[str, Any]] = None


class ClaimNumberResponse(BaseModel):
    success: bool
    message: str
    telephony_configuration_id: int
    phone_number: str
    number_type: str


@router.get("", response_model=List[PlatformNumberResponse])
async def list_platform_numbers(
    user: UserModel = Depends(get_user),
) -> List[PlatformNumberResponse]:
    """List platform inventory numbers.

    Superadmins see all numbers and full credentials. Normal tenant users see
    claimable and shared trial numbers with credentials masked.
    """
    async with db_client.async_session() as session:
        if user.is_superuser:
            query = select(PlatformInventoryNumberModel).order_by(
                PlatformInventoryNumberModel.created_at.desc()
            )
        else:
            query = select(PlatformInventoryNumberModel).where(
                PlatformInventoryNumberModel.status.in_(["available", "shared_pool", "in_use"])
            ).order_by(PlatformInventoryNumberModel.created_at.desc())

        result = await session.execute(query)
        rows = result.scalars().all()

        response: List[PlatformNumberResponse] = []
        for row in rows:
            # If not superuser, hide from catalog if dedicated and assigned to someone else
            if not user.is_superuser and row.number_type == "dedicated":
                if row.status == "in_use" and row.assigned_organization_id != user.selected_organization_id:
                    continue

            response.append(
                PlatformNumberResponse(
                    id=row.id,
                    phone_number=row.phone_number,
                    provider=row.provider,
                    carrier=row.carrier,
                    number_type=row.number_type,
                    country_code=row.country_code,
                    monthly_cost=row.monthly_cost,
                    status=row.status,
                    assigned_organization_id=row.assigned_organization_id,
                    created_at=row.created_at,
                    # Mask sensitive credentials unless superuser
                    provider_config=row.provider_config if user.is_superuser else None,
                )
            )

        return response


@router.post("", response_model=PlatformNumberResponse)
async def create_platform_number(
    payload: PlatformNumberCreateRequest,
    user: UserModel = Depends(get_superuser),
) -> PlatformNumberResponse:
    """Superadmin adds a new phone number to platform inventory with provider credentials."""
    async with db_client.async_session() as session:
        # Check if number already exists
        existing = await session.execute(
            select(PlatformInventoryNumberModel).where(
                PlatformInventoryNumberModel.phone_number == payload.phone_number.strip()
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Phone number {payload.phone_number} already exists in inventory.",
            )

        row_status = "shared_pool" if payload.number_type == "shared_trial" else payload.status

        row = PlatformInventoryNumberModel(
            phone_number=payload.phone_number.strip(),
            provider=payload.provider.strip().lower(),
            carrier=payload.carrier.strip(),
            number_type=payload.number_type.strip(),
            country_code=payload.country_code.strip().upper(),
            monthly_cost=payload.monthly_cost,
            status=row_status,
            provider_config=payload.provider_config,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        return PlatformNumberResponse(
            id=row.id,
            phone_number=row.phone_number,
            provider=row.provider,
            carrier=row.carrier,
            number_type=row.number_type,
            country_code=row.country_code,
            monthly_cost=row.monthly_cost,
            status=row.status,
            assigned_organization_id=row.assigned_organization_id,
            created_at=row.created_at,
            provider_config=row.provider_config,
        )


@router.delete("/{number_id}")
async def delete_platform_number(
    number_id: int,
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Superadmin removes a phone number from inventory."""
    async with db_client.async_session() as session:
        row = await session.get(PlatformInventoryNumberModel, number_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Platform inventory number not found.",
            )
        await session.delete(row)
        await session.commit()
        return {"success": True, "message": f"Number {row.phone_number} removed from inventory."}


@router.post("/{number_id}/claim", response_model=ClaimNumberResponse)
async def claim_platform_number(
    number_id: int,
    user: UserModel = Depends(get_user),
) -> ClaimNumberResponse:
    """User/Client activates a shared trial number for testing or purchases a dedicated number.

    Automatically provisions the telephony configuration and phone number
    under the user's current organization.
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active organization selected in workspace.",
        )

    org_id = user.selected_organization_id

    async with db_client.async_session() as session:
        row = await session.get(PlatformInventoryNumberModel, number_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Platform inventory number not found.",
            )

        if row.number_type == "dedicated" and row.status == "in_use" and row.assigned_organization_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This dedicated number has already been claimed by another workspace.",
            )

        # 1. Provision or find Telephony Configuration in the client's organization
        config_name = f"Callio {row.carrier} ({row.number_type.replace('_', ' ').title()})"
        
        # Check if org already has a config for this provider or with this name
        config_result = await session.execute(
            select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == org_id,
                TelephonyConfigurationModel.name == config_name,
            )
        )
        existing_config = config_result.scalar_one_or_none()

        if existing_config:
            config = existing_config
        else:
            config = TelephonyConfigurationModel(
                organization_id=org_id,
                name=config_name,
                provider=row.provider,
                credentials=row.provider_config or {},
                is_default_outbound=True,
            )
            session.add(config)
            await session.commit()
            await session.refresh(config)

        # 2. Provision Phone Number in the client's organization under this configuration
        num_result = await session.execute(
            select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.organization_id == org_id,
                TelephonyPhoneNumberModel.address == row.phone_number,
            )
        )
        existing_number = num_result.scalar_one_or_none()

        if not existing_number:
            from api.utils.telephony_address import normalize_telephony_address
            normalized = normalize_telephony_address(row.phone_number, country_hint=row.country_code)

            phone_row = TelephonyPhoneNumberModel(
                organization_id=org_id,
                telephony_configuration_id=config.id,
                address=row.phone_number,
                address_normalized=normalized.canonical,
                address_type=normalized.address_type,
                country_code=row.country_code or normalized.country_code,
                label=f"Callio {row.carrier}",
                is_active=True,
                is_default_caller_id=True,
                extra_metadata={"platform_inventory_id": row.id, "type": row.number_type},
            )
            session.add(phone_row)

        # 3. Update inventory item status if dedicated
        if row.number_type == "dedicated":
            row.status = "in_use"
            row.assigned_organization_id = org_id

        await session.commit()

        message = (
            f"Shared trial number {row.phone_number} successfully linked to your workspace for free testing!"
            if row.number_type == "shared_trial"
            else f"Dedicated number {row.phone_number} successfully activated for your workspace!"
        )

        return ClaimNumberResponse(
            success=True,
            message=message,
            telephony_configuration_id=config.id,
            phone_number=row.phone_number,
            number_type=row.number_type,
        )
