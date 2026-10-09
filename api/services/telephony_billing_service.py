"""Telephony Number Claiming, Rental Billing, and Inventory Lifecycle Service.

Manages platform inventory numbers, plan-included allowances, monthly rental deductions,
and automatic number release when rental renewals lapse.
"""

from datetime import datetime, UTC, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger
from sqlalchemy import func, select, update

from api.db import db_client
from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)
from api.services.plan_service import plan_service


DEFAULT_MONTHLY_NUMBER_RENT_USD = 2.50


class TelephonyBillingService:
    async def get_org_claimed_numbers_count(self, organization_id: int) -> int:
        """Count how many platform inventory phone numbers this organization currently holds."""
        async with db_client.async_session() as session:
            stmt = select(func.count(func.distinct(TelephonyPhoneNumberModel.address_normalized))).where(
                TelephonyPhoneNumberModel.organization_id == organization_id,
                TelephonyPhoneNumberModel.is_platform_inventory == True,
                TelephonyPhoneNumberModel.pool_type != "shared_trial",
                TelephonyPhoneNumberModel.rental_status != "released",
            )
            res = await session.execute(stmt)
            return res.scalar() or 0

    async def claim_platform_number_with_plan_check(
        self,
        phone_number_id: int,
        organization_id: int,
        set_as_default: bool = True,
    ) -> Dict[str, Any]:
        """Claim a platform inventory number for an organization with plan quota and rental accounting."""
        now = datetime.now(UTC)
        limits = await plan_service.get_effective_limits(organization_id)
        current_claimed_count = await self.get_org_claimed_numbers_count(organization_id)

        async with db_client.async_session() as session:
            num = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not num or not num.is_platform_inventory:
                raise ValueError("Platform number not found")

            # Check if this specific organization has already claimed this number
            stmt_existing = select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.organization_id == organization_id,
                TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
                TelephonyPhoneNumberModel.is_active == True,
            )
            existing_claimed = (await session.execute(stmt_existing)).scalars().first()
            if existing_claimed:
                raise ValueError("This number has already been claimed by your organization")

            # Dedicated numbers: only one organization can claim
            if num.pool_type not in ("shared_trial", "shared_multi_org"):
                if num.assigned_organization_id is not None and num.assigned_organization_id != organization_id:
                    raise ValueError("This number has already been claimed by another organization")

            # Determine rental pricing
            number_rent_usd = (
                round(float(num.monthly_price_cents) / 100.0, 2)
                if num.monthly_price_cents and num.monthly_price_cents > 0
                else DEFAULT_MONTHLY_NUMBER_RENT_USD
            )

            # Check if this number fits within plan's included phone numbers
            is_included_in_plan = current_claimed_count < limits.included_phone_numbers
            rental_cost_charged_usd = 0.0

            if not is_included_in_plan:
                # Additional number: must pay monthly rent from wallet
                if limits.wallet_balance_usd < number_rent_usd:
                    raise ValueError(
                        f"Insufficient wallet balance (${limits.wallet_balance_usd:.2f}) for monthly number rental (${number_rent_usd:.2f}). "
                        f"Your {limits.tier_name} plan includes {limits.included_phone_numbers} phone number(s). "
                        f"Please recharge your wallet to claim additional phone numbers."
                    )
                # Deduct first month rental from wallet
                rental_cost_charged_usd = number_rent_usd
                await db_client.update_wallet_balance(organization_id, -rental_cost_charged_usd)

            # Mark platform inventory number assigned
            if num.pool_type not in ("shared_trial", "shared_multi_org"):
                num.assigned_organization_id = organization_id
                num.claimed_at = now
                num.next_rental_billing_at = now + timedelta(days=30)
                num.rental_status = "active"
            elif num.pool_type == "shared_multi_org":
                meta = dict(num.extra_metadata or {})
                claimed_orgs = list(meta.get("claimed_org_ids", []))
                if organization_id not in claimed_orgs:
                    claimed_orgs.append(organization_id)
                    meta["claimed_org_ids"] = claimed_orgs
                    num.extra_metadata = meta
                if num.assigned_organization_id is None:
                    num.assigned_organization_id = organization_id
                num.rental_status = "active"

            # Find source configuration
            source_config = await session.get(
                TelephonyConfigurationModel, num.telephony_configuration_id
            )
            if not source_config:
                raise ValueError("Source telephony configuration not found")

            # Update default outbound flags if requested
            if set_as_default:
                await session.execute(
                    update(TelephonyConfigurationModel)
                    .where(TelephonyConfigurationModel.organization_id == organization_id)
                    .values(is_default_outbound=False)
                )

            # Find or create linked configuration in organization
            stmt_cfg = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.name == f"Platform - {source_config.name}",
            )
            existing_org_config = (await session.execute(stmt_cfg)).scalar_one_or_none()

            if not existing_org_config:
                existing_org_config = TelephonyConfigurationModel(
                    organization_id=organization_id,
                    name=f"Platform - {source_config.name}",
                    provider=source_config.provider,
                    credentials=source_config.credentials,
                    is_default_outbound=set_as_default,
                    is_platform_inventory=True,
                )
                session.add(existing_org_config)
                await session.flush()
            elif set_as_default:
                existing_org_config.is_default_outbound = True

            if set_as_default:
                await session.execute(
                    update(TelephonyPhoneNumberModel)
                    .where(TelephonyPhoneNumberModel.telephony_configuration_id == existing_org_config.id)
                    .values(is_default_caller_id=False)
                )

            # Ensure cloned number exists in organization's config
            stmt_num = select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.organization_id == organization_id,
                TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
            )
            org_num = (await session.execute(stmt_num)).scalar_one_or_none()
            org_meta = dict(num.extra_metadata or {})
            org_meta["is_claimed_platform_number"] = True
            org_meta["source_platform_number_id"] = num.id

            if not org_num:
                org_num = TelephonyPhoneNumberModel(
                    organization_id=organization_id,
                    telephony_configuration_id=existing_org_config.id,
                    address=num.address,
                    address_normalized=num.address_normalized,
                    address_type=num.address_type,
                    country_code=num.country_code,
                    label=f"Claimed {num.address}",
                    is_active=True,
                    is_default_caller_id=set_as_default,
                    pool_type=num.pool_type,
                    is_platform_inventory=False,
                    claimed_at=now,
                    next_rental_billing_at=now + timedelta(days=30),
                    rental_status="active",
                    extra_metadata=org_meta,
                )
                session.add(org_num)
            else:
                org_num.telephony_configuration_id = existing_org_config.id
                org_num.is_active = True
                org_num.is_platform_inventory = False
                org_num.rental_status = "active"
                org_num.pool_type = num.pool_type
                org_num.claimed_at = now
                org_num.next_rental_billing_at = now + timedelta(days=30)
                org_num.extra_metadata = org_meta
                if set_as_default:
                    org_num.is_default_caller_id = True

            await session.flush()
            claimed_address = str(num.address)
            num_id = int(org_num.id)
            config_id = int(existing_org_config.id)

            await session.commit()

        updated_limits = await plan_service.get_effective_limits(organization_id)
        logger.info(
            "Org {} claimed platform number {} (included_in_plan={}, charged=${}, next_billing={})",
            organization_id,
            claimed_address,
            is_included_in_plan,
            rental_cost_charged_usd,
            now + timedelta(days=30),
        )

        return {
            "success": True,
            "phone_number_id": num_id,
            "address": claimed_address,
            "telephony_configuration_id": config_id,
            "is_included_in_plan": is_included_in_plan,
            "monthly_rent_usd": number_rent_usd,
            "rental_cost_charged_usd": rental_cost_charged_usd,
            "next_rental_billing_at": (now + timedelta(days=30)).isoformat(),
            "wallet_balance_after": updated_limits.wallet_balance_usd,
            "plan_included_numbers": limits.included_phone_numbers,
            "total_claimed_numbers": current_claimed_count + 1,
        }

    async def process_daily_number_rentals(self) -> Dict[str, Any]:
        """Daily background job: re-bill active rented numbers and release delinquent numbers."""
        now = datetime.now(UTC)
        billed_count = 0
        released_count = 0
        total_rent_collected_usd = 0.0

        async with db_client.async_session() as session:
            stmt = select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.is_platform_inventory == True,
                TelephonyPhoneNumberModel.assigned_organization_id.isnot(None),
                TelephonyPhoneNumberModel.rental_status == "active",
                TelephonyPhoneNumberModel.next_rental_billing_at <= now,
            )
            res = await session.execute(stmt)
            due_numbers = list(res.scalars().all())

            for num in due_numbers:
                org_id = num.assigned_organization_id
                limits = await plan_service.get_effective_limits(org_id)
                claimed_count = await self.get_org_claimed_numbers_count(org_id)

                # If covered under active plan allowance:
                if claimed_count <= limits.included_phone_numbers:
                    num.next_rental_billing_at = now + timedelta(days=30)
                    continue

                # Extra number: renew from wallet
                rent_usd = (
                    round(float(num.monthly_price_cents) / 100.0, 2)
                    if num.monthly_price_cents and num.monthly_price_cents > 0
                    else DEFAULT_MONTHLY_NUMBER_RENT_USD
                )

                if limits.wallet_balance_usd >= rent_usd:
                    await db_client.update_wallet_balance(org_id, -rent_usd)
                    num.next_rental_billing_at = now + timedelta(days=30)
                    billed_count += 1
                    total_rent_collected_usd += rent_usd
                else:
                    # Delinquent: release back to inventory
                    logger.warning(
                        "Releasing delinquent platform number {} from org {} (insufficient wallet balance ${:.2f} for rent ${:.2f})",
                        num.address,
                        org_id,
                        limits.wallet_balance_usd,
                        rent_usd,
                    )
                    num.assigned_organization_id = None
                    num.rental_status = "released"
                    num.next_rental_billing_at = None
                    released_count += 1

            await session.commit()

        return {
            "processed_at": now.isoformat(),
            "renewed_count": billed_count,
            "released_count": released_count,
            "rent_collected_usd": round(total_rent_collected_usd, 2),
        }

    async def release_platform_number(
        self,
        phone_number_id: int,
        organization_id: int,
    ) -> bool:
        """Explicitly unclaim/release a platform number from an organization."""
        async with db_client.async_session() as session:
            num = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not num:
                raise ValueError("Phone number not found")

            # Resolve to the master inventory record
            master_num = num
            if not num.is_platform_inventory or (num.configuration and num.configuration.name.startswith("Platform - ")):
                stmt_master = (
                    select(TelephonyPhoneNumberModel)
                    .join(
                        TelephonyConfigurationModel,
                        TelephonyPhoneNumberModel.telephony_configuration_id == TelephonyConfigurationModel.id,
                    )
                    .where(
                        TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
                        TelephonyPhoneNumberModel.is_platform_inventory == True,
                        ~TelephonyConfigurationModel.name.like("Platform - %"),
                    )
                )
                master_num = (await session.execute(stmt_master)).scalars().first()

            if master_num:
                if master_num.pool_type == "shared_multi_org":
                    meta = dict(master_num.extra_metadata or {})
                    claimed_orgs = list(meta.get("claimed_org_ids", []))
                    if organization_id in claimed_orgs:
                        claimed_orgs.remove(organization_id)
                        meta["claimed_org_ids"] = claimed_orgs
                        master_num.extra_metadata = meta
                    if master_num.assigned_organization_id == organization_id:
                        master_num.assigned_organization_id = claimed_orgs[0] if claimed_orgs else None
                    if not claimed_orgs:
                        master_num.rental_status = "available"
                elif master_num.pool_type != "shared_trial":
                    if master_num.assigned_organization_id == organization_id:
                        master_num.assigned_organization_id = None
                        master_num.rental_status = "available"
                        master_num.next_rental_billing_at = None

            # Deactivate cloned number in tenant organization
            stmt_clone = (
                update(TelephonyPhoneNumberModel)
                .where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
                )
                .values(is_active=False, rental_status="released")
            )
            await session.execute(stmt_clone)
            await session.commit()
            return True


telephony_billing_service = TelephonyBillingService()
