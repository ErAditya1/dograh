from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import cast, exists, func, Numeric
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    APIKeyModel,
    OrganizationConfigurationModel,
    OrganizationModel,
    PlatformInventoryNumberModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
    organization_users_association,
)
from api.utils.api_key import generate_api_key


class OrganizationClient(BaseDBClient):
    async def get_organization_by_id(
        self, organization_id: int
    ) -> Optional[OrganizationModel]:
        """Get an organization by its ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            return result.scalars().first()

    async def get_organization_users(self, organization_id: int) -> list[UserModel]:
        """Get all users linked to an organization (many-to-many)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel)
                .join(
                    organization_users_association,
                    organization_users_association.c.user_id == UserModel.id,
                )
                .where(
                    organization_users_association.c.organization_id == organization_id
                )
                .order_by(UserModel.id)
            )
            return list(result.scalars().all())

    async def get_or_create_organization_by_provider_id(
        self, org_provider_id: str, user_id: int
    ) -> tuple[OrganizationModel, bool]:
        """Get an existing organization by provider_id or create a new one.

        Returns:
            A tuple of (organization, was_created) where was_created is True if the organization
            was created in this call, False if it already existed.
        """
        async with self.async_session() as session:
            # First try to get existing organization
            result = await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.provider_id == org_provider_id
                )
            )
            organization = result.scalars().first()

            if organization is None:
                # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
                # This is atomic and handles race conditions at the database level

                stmt = insert(OrganizationModel.__table__).values(
                    provider_id=org_provider_id, created_at=datetime.now(timezone.utc)
                )
                # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
                stmt = stmt.on_conflict_do_nothing(index_elements=["provider_id"])

                result = await session.execute(stmt)
                await session.commit()

                # Check if we actually inserted (rowcount > 0) or if there was a conflict (rowcount == 0)
                was_created = result.rowcount > 0

                # Now fetch the organization (either the one we just created or the one that existed)
                result = await session.execute(
                    select(OrganizationModel).where(
                        OrganizationModel.provider_id == org_provider_id
                    )
                )
                organization = result.scalars().first()

                if organization is None:
                    # This should never happen, but handle it just in case
                    error_msg = f"Failed to create or fetch organization with provider_id {org_provider_id}"
                    raise ValueError(error_msg)

                # Only create API key if we actually created the organization
                if was_created:
                    # Create a default API key for the new organization
                    _, key_hash, key_prefix = generate_api_key()

                    api_key = APIKeyModel(
                        organization_id=organization.id,
                        name="Default API Key",
                        key_hash=key_hash,
                        key_prefix=key_prefix,
                        is_active=True,
                        created_by=user_id,
                    )
                    session.add(api_key)
                    await session.commit()

                await session.refresh(organization)
                return organization, was_created
            return organization, False

    async def is_user_member_of_organization(
        self, user_id: int, organization_id: int
    ) -> bool:
        """Return True if the user belongs to the given organization."""
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    exists().where(
                        (organization_users_association.c.user_id == user_id)
                        & (
                            organization_users_association.c.organization_id
                            == organization_id
                        )
                    )
                )
            )
            return bool(result.scalar())

    async def add_user_to_organization(
        self, user_id: int, organization_id: int
    ) -> None:
        """Ensure that a user is linked to an organization (many-to-many).

        The association is created only if it does not already exist.
        Uses INSERT ... ON CONFLICT DO NOTHING to handle race conditions.
        """
        async with self.async_session() as session:
            # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
            # This handles race conditions at the database level

            stmt = insert(organization_users_association).values(
                user_id=user_id, organization_id=organization_id
            )
            # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
            # The primary key constraint on (user_id, organization_id) will trigger the conflict
            stmt = stmt.on_conflict_do_nothing()

            await session.execute(stmt)
            await session.commit()

    async def get_platform_stats(self) -> dict:
        """Get aggregate real platform statistics from the PostgreSQL database."""
        async with self.async_session() as session:
            total_orgs = (
                await session.execute(select(func.count(OrganizationModel.id)))
            ).scalar() or 0
            total_users = (
                await session.execute(select(func.count(UserModel.id)))
            ).scalar() or 0
            total_runs = (
                await session.execute(select(func.count(WorkflowRunModel.id)))
            ).scalar() or 0

            # Sum call_duration_seconds from usage_info
            duration_res = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                cast(
                                    func.nullif(
                                        WorkflowRunModel.usage_info.op("->>")(
                                            "call_duration_seconds"
                                        ),
                                        "",
                                    ),
                                    Numeric,
                                )
                            ),
                            0,
                        )
                    )
                )
            ).scalar() or 0
            total_seconds = float(duration_res)
            total_minutes = round(total_seconds / 60.0, 2)

            total_workflows = (
                await session.execute(select(func.count(WorkflowModel.id)))
            ).scalar() or 0
            total_configs = (
                await session.execute(
                    select(func.count(TelephonyConfigurationModel.id))
                )
            ).scalar() or 0
            total_phone_numbers = (
                await session.execute(
                    select(func.count(TelephonyPhoneNumberModel.id))
                )
            ).scalar() or 0
            total_inventory = (
                await session.execute(
                    select(func.count(PlatformInventoryNumberModel.id))
                )
            ).scalar() or 0

            return {
                "total_clients": total_orgs,
                "total_users": total_users,
                "total_calls": total_runs,
                "total_seconds": total_seconds,
                "total_minutes": total_minutes,
                "total_workflows": total_workflows,
                "total_telephony_configs": total_configs,
                "total_phone_numbers": total_phone_numbers,
                "platform_inventory_count": total_inventory,
            }

    async def get_all_organizations_with_stats(self) -> list[dict]:
        """Fetch all client organizations with associated users, call metrics, and configuration counts."""
        async with self.async_session() as session:
            orgs_res = await session.execute(
                select(OrganizationModel).order_by(OrganizationModel.id.asc())
            )
            orgs = orgs_res.scalars().all()

            result = []
            for org in orgs:
                # Associated users
                users_res = await session.execute(
                    select(UserModel)
                    .join(
                        organization_users_association,
                        organization_users_association.c.user_id == UserModel.id,
                    )
                    .where(organization_users_association.c.organization_id == org.id)
                )
                users = list(users_res.scalars().all())

                if not users:
                    u_res = await session.execute(
                        select(UserModel).where(
                            UserModel.selected_organization_id == org.id
                        )
                    )
                    users = list(u_res.scalars().all())

                # Workflow runs for this org
                runs_query = (
                    select(
                        func.count(WorkflowRunModel.id),
                        func.coalesce(
                            func.sum(
                                cast(
                                    func.nullif(
                                        WorkflowRunModel.usage_info.op("->>")(
                                            "call_duration_seconds"
                                        ),
                                        "",
                                    ),
                                    Numeric,
                                )
                            ),
                            0,
                        ),
                    )
                    .join(
                        WorkflowModel,
                        WorkflowRunModel.workflow_id == WorkflowModel.id,
                    )
                    .where(WorkflowModel.organization_id == org.id)
                )
                run_stats = (await session.execute(runs_query)).first()
                call_count = run_stats[0] if run_stats else 0
                total_sec = float(run_stats[1]) if run_stats and run_stats[1] else 0.0
                total_min = round(total_sec / 60.0, 2)

                # Telephony configs count
                configs_count = (
                    await session.execute(
                        select(func.count(TelephonyConfigurationModel.id)).where(
                            TelephonyConfigurationModel.organization_id == org.id
                        )
                    )
                ).scalar() or 0

                # Phone numbers count
                numbers_count = (
                    await session.execute(
                        select(func.count(TelephonyPhoneNumberModel.id)).where(
                            TelephonyPhoneNumberModel.organization_id == org.id
                        )
                    )
                ).scalar() or 0

                # Workflows count
                workflows_count = (
                    await session.execute(
                        select(func.count(WorkflowModel.id)).where(
                            WorkflowModel.organization_id == org.id
                        )
                    )
                ).scalar() or 0

                # Organization credits from organization_configurations
                credit_conf = (
                    await session.execute(
                        select(OrganizationConfigurationModel).where(
                            OrganizationConfigurationModel.organization_id == org.id,
                            OrganizationConfigurationModel.key == "ORGANIZATION_CREDITS",
                        )
                    )
                ).scalars().first()

                credits_balance = 5.0
                if credit_conf and isinstance(credit_conf.value, dict):
                    credits_balance = float(credit_conf.value.get("balance_usd", 5.0))

                primary_email = (
                    users[0].email
                    if users and users[0].email
                    else "workspace@callio.ai"
                )
                display_name = (
                    f"{primary_email.split('@')[0].capitalize()} Workspace"
                    if users and users[0].email
                    else f"Organization {org.id}"
                )

                result.append(
                    {
                        "id": org.id,
                        "provider_id": org.provider_id,
                        "name": display_name,
                        "email": primary_email,
                        "plan": (
                            "Superadmin"
                            if any(u.is_superuser for u in users)
                            else "Growth"
                        ),
                        "status": "active",
                        "created_at": (
                            org.created_at.isoformat() if org.created_at else None
                        ),
                        "total_calls": call_count,
                        "total_seconds": total_sec,
                        "total_minutes": total_min,
                        "telephony_configs_count": configs_count,
                        "phone_numbers_count": numbers_count,
                        "workflows_count": workflows_count,
                        "credits_balance": credits_balance,
                        "price_per_second_usd": org.price_per_second_usd,
                        "users": [
                            {
                                "id": u.id,
                                "email": u.email,
                                "is_superuser": u.is_superuser,
                                "created_at": (
                                    u.created_at.isoformat()
                                    if u.created_at
                                    else None
                                ),
                            }
                            for u in users
                        ],
                    }
                )

            return result

    async def grant_organization_credits(
        self, organization_id: int, amount_usd: float
    ) -> float:
        """Add credits to an organization's balance in organization_configurations."""
        async with self.async_session() as session:
            credit_conf = (
                await session.execute(
                    select(OrganizationConfigurationModel).where(
                        OrganizationConfigurationModel.organization_id
                        == organization_id,
                        OrganizationConfigurationModel.key == "ORGANIZATION_CREDITS",
                    )
                )
            ).scalars().first()

            if credit_conf:
                val = dict(credit_conf.value) if isinstance(credit_conf.value, dict) else {}
                current = float(val.get("balance_usd", 5.0))
                new_bal = round(current + amount_usd, 2)
                val["balance_usd"] = new_bal
                history = val.get("history", [])
                history.append(
                    {
                        "amount": amount_usd,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "new_balance": new_bal,
                    }
                )
                val["history"] = history
                credit_conf.value = val
                await session.commit()
                return new_bal
            else:
                new_bal = round(5.0 + amount_usd, 2)
                new_conf = OrganizationConfigurationModel(
                    organization_id=organization_id,
                    key="ORGANIZATION_CREDITS",
                    value={
                        "balance_usd": new_bal,
                        "history": [
                            {
                                "amount": amount_usd,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "new_balance": new_bal,
                            }
                        ],
                    },
                )
                session.add(new_conf)
                await session.commit()
                return new_bal

