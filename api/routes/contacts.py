import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(
    prefix="/contacts",
    tags=["contacts"],
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS organization_contacts (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    company VARCHAR(255),
    city VARCHAR(100),
    status VARCHAR(50) DEFAULT 'valid',
    called BOOLEAN DEFAULT FALSE,
    last_called_at TIMESTAMPTZ,
    intent VARCHAR(50),
    campaign_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""
CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_org_contacts_org_id ON organization_contacts(organization_id)
"""

# Unique constraint to prevent duplicate phone numbers per organization
CREATE_UNIQUE_INDEX_SQL = """
DO $$
BEGIN
    -- Delete existing duplicate phone numbers, keeping the latest row
    DELETE FROM organization_contacts a USING organization_contacts b
    WHERE a.id < b.id AND a.organization_id = b.organization_id AND a.phone = b.phone;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_org_contacts_org_phone'
    ) THEN
        ALTER TABLE organization_contacts
        ADD CONSTRAINT uq_org_contacts_org_phone UNIQUE (organization_id, phone);
    END IF;
END;
$$;
"""

CREATE_GROUPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS contact_groups (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    color VARCHAR(30) DEFAULT '#0F6E6E',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_org_group_name UNIQUE (organization_id, name)
)
"""

CREATE_GROUPS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_contact_groups_org_id ON contact_groups(organization_id)
"""

CREATE_GROUP_MEMBERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS contact_group_members (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL REFERENCES contact_groups(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES organization_contacts(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_group_contact UNIQUE (group_id, contact_id)
)
"""

CREATE_GROUP_MEMBERS_IDX_GROUP_SQL = """
CREATE INDEX IF NOT EXISTS idx_group_members_group_id ON contact_group_members(group_id)
"""

CREATE_GROUP_MEMBERS_IDX_CONTACT_SQL = """
CREATE INDEX IF NOT EXISTS idx_group_members_contact_id ON contact_group_members(contact_id)
"""

CREATE_GROUP_MEMBERS_IDX_ORG_SQL = """
CREATE INDEX IF NOT EXISTS idx_group_members_org_id ON contact_group_members(organization_id)
"""

DEFAULT_CONTACTS = []


def normalize_phone_number(raw: str) -> str:
    """Normalize phone number to standard E.164 (+91 for 10-digit Indian numbers)."""
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        local = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        local = digits[1:]
    elif len(digits) == 10:
        local = digits
    else:
        raw_str = str(raw).strip()
        return raw_str if raw_str.startswith("+") else f"+{raw_str}"
    return f"+91{local}"


async def ensure_table():
    try:
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_TABLE_SQL))
            await session.commit()
    except Exception as e:
        logger.warning(f"ensure_table CREATE_TABLE_SQL error: {e}")

    try:
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_INDEX_SQL))
            await session.commit()
    except Exception as e:
        logger.warning(f"ensure_table CREATE_INDEX_SQL error: {e}")

    # Enforce uniqueness of phone per organization
    try:
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_UNIQUE_INDEX_SQL))
            await session.commit()
    except Exception as ce:
        logger.debug(f"uq_org_contacts_org_phone note: {ce}")

    # Ensure groups and group_members tables exist (each executed separately for asyncpg compatibility)
    group_ddls = [
        ("contact_groups", CREATE_GROUPS_TABLE_SQL),
        ("idx_contact_groups_org_id", CREATE_GROUPS_INDEX_SQL),
        ("contact_group_members", CREATE_GROUP_MEMBERS_TABLE_SQL),
        ("idx_group_members_group_id", CREATE_GROUP_MEMBERS_IDX_GROUP_SQL),
        ("idx_group_members_contact_id", CREATE_GROUP_MEMBERS_IDX_CONTACT_SQL),
        ("idx_group_members_org_id", CREATE_GROUP_MEMBERS_IDX_ORG_SQL),
    ]
    for name, stmt in group_ddls:
        try:
            async with db_client.async_session() as session:
                await session.execute(text(stmt))
                await session.commit()
        except Exception as ge:
            logger.warning(f"ensure_table {name} DDL error: {ge}")


class ContactGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = "#0F6E6E"


class ContactGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = None


class AddContactsToGroupRequest(BaseModel):
    contact_ids: List[int]


class AddNumbersToGroupRequest(BaseModel):
    numbers: Optional[List[str]] = None
    text: Optional[str] = None
    contact_ids: Optional[List[int]] = None


class ContactCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5)
    email: Optional[str] = None
    company: Optional[str] = None
    city: Optional[str] = None
    status: Optional[str] = "valid"
    campaign_id: Optional[int] = None
    group_id: Optional[int] = None


class BulkContactsRequest(BaseModel):
    contacts: List[ContactCreateRequest]
    group_id: Optional[int] = None
    new_group_name: Optional[str] = None
    new_group_color: Optional[str] = "#0F6E6E"


class BulkDeleteRequest(BaseModel):
    ids: List[int]


class AssignCampaignRequest(BaseModel):
    ids: List[int]
    campaign_id: int


def _row_to_contact(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name or "—",
        "phone": row.phone,
        "email": row.email or "",
        "company": row.company or "—",
        "city": row.city or "",
        "status": row.status or "valid",
        "called": bool(row.called),
        "lastCalledAt": row.last_called_at.isoformat() if row.last_called_at else None,
        "intent": row.intent,
        "campaignId": str(row.campaign_id) if row.campaign_id else None,
    }


def _row_to_group(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "name": row.name,
        "description": row.description or "",
        "color": row.color or "#0F6E6E",
        "member_count": getattr(row, "member_count", 0),
        "created_at": row.created_at.isoformat() if hasattr(row, "created_at") and row.created_at else None,
        "updated_at": row.updated_at.isoformat() if hasattr(row, "updated_at") and row.updated_at else None,
    }


@router.get("")
@router.get("/")
async def list_organization_contacts(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
    group_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user),
):
    """
    Get phonebook contacts for the user's organization with server-side pagination & filtering.
    """
    await ensure_table()
    org_id = user.selected_organization_id or 1

    where_clauses = ["organization_id = :org_id"]
    params: Dict[str, Any] = {"org_id": org_id, "limit": limit, "offset": offset}

    if campaign_id is not None:
        where_clauses.append("campaign_id = :campaign_id")
        params["campaign_id"] = campaign_id

    if group_id is not None:
        where_clauses.append(
            "id IN (SELECT contact_id FROM contact_group_members WHERE group_id = :group_id AND organization_id = :org_id)"
        )
        params["group_id"] = group_id

    if status and status != "all":
        if status == "called":
            where_clauses.append("called = TRUE")
        elif status == "not-called":
            where_clauses.append("called = FALSE")
        else:
            where_clauses.append("status = :status")
            params["status"] = status

    if q and q.strip():
        where_clauses.append(
            "(name ILIKE :q OR phone ILIKE :q OR email ILIKE :q OR company ILIKE :q OR city ILIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"

    where_sql = " AND ".join(where_clauses)

    async with db_client.async_session() as session:
        query = text(
            f"""
            SELECT id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
            FROM organization_contacts
            WHERE {where_sql}
            ORDER BY id ASC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await session.execute(query, params)
        rows = result.fetchall()

        count_query = text(
            f"""
            SELECT COUNT(*) FROM organization_contacts
            WHERE {where_sql}
            """
        )
        count_result = await session.execute(count_query, params)
        total_count = count_result.scalar() or 0

        page = (offset // limit) + 1 if limit > 0 else 1
        total_pages = max(1, (total_count + limit - 1) // limit) if limit > 0 else 1
        return {
            "contacts": [_row_to_contact(r) for r in rows],
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }


@router.post("")
@router.post("/")
async def create_organization_contact(
    req: ContactCreateRequest,
    user: UserModel = Depends(get_user),
):
    """Add a new contact to the organization's calling list, deduplicating by phone."""
    await ensure_table()
    org_id = user.selected_organization_id or 1
    norm_phone = normalize_phone_number(req.phone)
    if not norm_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    async with db_client.async_session() as session:
        res = await session.execute(
            text(
                """
                INSERT INTO organization_contacts 
                (organization_id, name, phone, email, company, city, status, called, campaign_id, created_at, updated_at)
                VALUES (:org_id, :name, :phone, :email, :company, :city, :status, FALSE, :campaign_id, NOW(), NOW())
                ON CONFLICT (organization_id, phone) DO UPDATE
                    SET name = EXCLUDED.name,
                        email = COALESCE(EXCLUDED.email, organization_contacts.email),
                        company = COALESCE(EXCLUDED.company, organization_contacts.company),
                        city = COALESCE(EXCLUDED.city, organization_contacts.city),
                        updated_at = NOW()
                RETURNING id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
                """
            ),
            {
                "org_id": org_id,
                "name": req.name,
                "phone": norm_phone,
                "email": req.email,
                "company": req.company,
                "city": req.city,
                "status": req.status or "valid",
                "campaign_id": req.campaign_id,
            },
        )
        row = res.fetchone()
        if req.group_id and row:
            await session.execute(
                text(
                    """
                    INSERT INTO contact_group_members (organization_id, group_id, contact_id, created_at)
                    VALUES (:org_id, :group_id, :contact_id, NOW())
                    ON CONFLICT (group_id, contact_id) DO NOTHING
                    """
                ),
                {"org_id": org_id, "group_id": req.group_id, "contact_id": row.id},
            )
        await session.commit()
        return _row_to_contact(row)


@router.post("/bulk")
async def bulk_import_organization_contacts(
    req: BulkContactsRequest,
    user: UserModel = Depends(get_user),
):
    """Bulk import contacts from CSV or external list into organization phonebook without duplicates."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    inserted: List[Dict[str, Any]] = []
    seen_in_batch = set()
    async with db_client.async_session() as session:
        target_group_id = req.group_id
        if req.new_group_name and req.new_group_name.strip():
            g_res = await session.execute(
                text(
                    """
                    INSERT INTO contact_groups (organization_id, name, color, created_at, updated_at)
                    VALUES (:org_id, :name, :color, NOW(), NOW())
                    ON CONFLICT (organization_id, name) DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """
                ),
                {"org_id": org_id, "name": req.new_group_name.strip(), "color": req.new_group_color or "#0F6E6E"},
            )
            g_row = g_res.fetchone()
            if g_row:
                target_group_id = g_row.id

        for c in req.contacts:
            norm_phone = normalize_phone_number(c.phone)
            if not norm_phone or norm_phone in seen_in_batch:
                continue
            seen_in_batch.add(norm_phone)
            res = await session.execute(
                text(
                    """
                    INSERT INTO organization_contacts 
                    (organization_id, name, phone, email, company, city, status, called, campaign_id, created_at, updated_at)
                    VALUES (:org_id, :name, :phone, :email, :company, :city, :status, FALSE, :campaign_id, NOW(), NOW())
                    ON CONFLICT (organization_id, phone) DO UPDATE
                        SET name = EXCLUDED.name,
                            email = COALESCE(EXCLUDED.email, organization_contacts.email),
                            company = COALESCE(EXCLUDED.company, organization_contacts.company),
                            city = COALESCE(EXCLUDED.city, organization_contacts.city),
                            updated_at = NOW()
                    RETURNING id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
                    """
                ),
                {
                    "org_id": org_id,
                    "name": c.name,
                    "phone": norm_phone,
                    "email": c.email,
                    "company": c.company,
                    "city": c.city,
                    "status": c.status or "valid",
                    "campaign_id": c.campaign_id,
                },
            )
            row = res.fetchone()
            if row:
                inserted.append(_row_to_contact(row))
                if target_group_id:
                    await session.execute(
                        text(
                            """
                            INSERT INTO contact_group_members (organization_id, group_id, contact_id, created_at)
                            VALUES (:org_id, :group_id, :contact_id, NOW())
                            ON CONFLICT (group_id, contact_id) DO NOTHING
                            """
                        ),
                        {"org_id": org_id, "group_id": target_group_id, "contact_id": row.id},
                    )
        await session.commit()

    return {"count": len(inserted), "contacts": inserted, "group_id": target_group_id}


@router.post("/delete-bulk")
async def delete_contacts_bulk(
    req: BulkDeleteRequest,
    user: UserModel = Depends(get_user),
):
    """Delete selected contacts belonging to the user's organization."""
    if not req.ids:
        return {"deleted": 0}
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        await session.execute(
            text(
                """
                DELETE FROM organization_contacts
                WHERE organization_id = :org_id AND id = ANY(:ids)
                """
            ),
            {"org_id": org_id, "ids": req.ids},
        )
        await session.commit()

    return {"deleted": len(req.ids)}


@router.post("/assign-campaign")
async def assign_contacts_to_campaign(
    req: AssignCampaignRequest,
    user: UserModel = Depends(get_user),
):
    """Assign selected contacts to a specific campaign."""
    if not req.ids:
        return {"updated": 0}
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        await session.execute(
            text(
                """
                UPDATE organization_contacts
                SET campaign_id = :campaign_id, updated_at = NOW()
                WHERE organization_id = :org_id AND id = ANY(:ids)
                """
            ),
            {"org_id": org_id, "ids": req.ids, "campaign_id": req.campaign_id},
        )
        await session.commit()

    return {"updated": len(req.ids), "campaign_id": req.campaign_id}


# -------------------- CONTACT GROUPS ENDPOINTS --------------------

@router.get("/groups")
async def list_contact_groups(
    user: UserModel = Depends(get_user),
):
    """List all contact groups with real-time member counts for the organization."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        query = text(
            """
            SELECT g.id, g.organization_id, g.name, g.description, g.color, g.created_at, g.updated_at,
                   COUNT(m.contact_id) as member_count
            FROM contact_groups g
            LEFT JOIN contact_group_members m ON g.id = m.group_id AND m.organization_id = :org_id
            WHERE g.organization_id = :org_id
            GROUP BY g.id, g.organization_id, g.name, g.description, g.color, g.created_at, g.updated_at
            ORDER BY g.name ASC
            """
        )
        res = await session.execute(query, {"org_id": org_id})
        rows = res.fetchall()
        return [_row_to_group(r) for r in rows]


@router.post("/groups")
async def create_contact_group(
    req: ContactGroupCreate,
    user: UserModel = Depends(get_user),
):
    """Create a new contact group."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    try:
        async with db_client.async_session() as session:
            exist_res = await session.execute(
                text("SELECT id FROM contact_groups WHERE organization_id = :org_id AND name ILIKE :name"),
                {"org_id": org_id, "name": req.name.strip()},
            )
            if exist_res.fetchone():
                raise HTTPException(status_code=400, detail=f"Group '{req.name}' already exists.")

            res = await session.execute(
                text(
                    """
                    INSERT INTO contact_groups (organization_id, name, description, color, created_at, updated_at)
                    VALUES (:org_id, :name, :description, :color, NOW(), NOW())
                    RETURNING id, organization_id, name, description, color, created_at, updated_at
                    """
                ),
                {
                    "org_id": org_id,
                    "name": req.name.strip(),
                    "description": req.description or "",
                    "color": req.color or "#0F6E6E",
                },
            )
            row = res.fetchone()
            await session.commit()
            return _row_to_group(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating contact group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create group: {str(e)}")


@router.get("/groups/{group_id}")
async def get_contact_group(
    group_id: int,
    user: UserModel = Depends(get_user),
):
    """Get group details by ID."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        res = await session.execute(
            text(
                """
                SELECT g.id, g.organization_id, g.name, g.description, g.color, g.created_at, g.updated_at,
                       COUNT(m.contact_id) as member_count
                FROM contact_groups g
                LEFT JOIN contact_group_members m ON g.id = m.group_id AND m.organization_id = :org_id
                WHERE g.organization_id = :org_id AND g.id = :group_id
                GROUP BY g.id, g.organization_id, g.name, g.description, g.color, g.created_at, g.updated_at
                """
            ),
            {"org_id": org_id, "group_id": group_id},
        )
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Contact group not found")
        return _row_to_group(row)


@router.put("/groups/{group_id}")
async def update_contact_group(
    group_id: int,
    req: ContactGroupUpdate,
    user: UserModel = Depends(get_user),
):
    """Update contact group name, description, or color."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    updates = []
    params: Dict[str, Any] = {"org_id": org_id, "group_id": group_id}

    if req.name is not None:
        updates.append("name = :name")
        params["name"] = req.name.strip()
    if req.description is not None:
        updates.append("description = :description")
        params["description"] = req.description.strip()
    if req.color is not None:
        updates.append("color = :color")
        params["color"] = req.color.strip()

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    updates.append("updated_at = NOW()")
    set_sql = ", ".join(updates)

    async with db_client.async_session() as session:
        res = await session.execute(
            text(
                f"""
                UPDATE contact_groups
                SET {set_sql}
                WHERE organization_id = :org_id AND id = :group_id
                RETURNING id, organization_id, name, description, color, created_at, updated_at
                """
            ),
            params,
        )
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Contact group not found")
        await session.commit()
        return _row_to_group(row)


@router.delete("/groups/{group_id}")
async def delete_contact_group(
    group_id: int,
    user: UserModel = Depends(get_user),
):
    """Delete a contact group. Note: contacts themselves remain intact in directory."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        res = await session.execute(
            text("DELETE FROM contact_groups WHERE organization_id = :org_id AND id = :group_id RETURNING id"),
            {"org_id": org_id, "group_id": group_id},
        )
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Contact group not found")
        await session.commit()
        return {"success": True, "deleted_group_id": group_id}


@router.get("/groups/{group_id}/contacts")
async def get_group_contacts(
    group_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: UserModel = Depends(get_user),
):
    """Get contacts belonging to a specific group."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        query = text(
            """
            SELECT c.id, c.name, c.phone, c.email, c.company, c.city, c.status, c.called, c.last_called_at, c.intent, c.campaign_id
            FROM organization_contacts c
            INNER JOIN contact_group_members m ON c.id = m.contact_id
            WHERE m.organization_id = :org_id AND m.group_id = :group_id
            ORDER BY c.name ASC
            LIMIT :limit OFFSET :offset
            """
        )
        res = await session.execute(query, {"org_id": org_id, "group_id": group_id, "limit": limit, "offset": offset})
        rows = res.fetchall()

        count_res = await session.execute(
            text("SELECT COUNT(*) FROM contact_group_members WHERE organization_id = :org_id AND group_id = :group_id"),
            {"org_id": org_id, "group_id": group_id},
        )
        total_count = count_res.scalar() or 0

        return {
            "contacts": [_row_to_contact(r) for r in rows],
            "total_count": total_count,
            "group_id": group_id,
        }


@router.post("/groups/{group_id}/contacts")
async def add_contacts_to_group(
    group_id: int,
    req: AddContactsToGroupRequest,
    user: UserModel = Depends(get_user),
):
    """Add existing contacts to a group."""
    if not req.contact_ids:
        return {"added": 0}
    await ensure_table()
    org_id = user.selected_organization_id or 1

    added = 0
    async with db_client.async_session() as session:
        g_check = await session.execute(
            text("SELECT id FROM contact_groups WHERE organization_id = :org_id AND id = :group_id"),
            {"org_id": org_id, "group_id": group_id},
        )
        if not g_check.fetchone():
            raise HTTPException(status_code=404, detail="Contact group not found")

        for cid in req.contact_ids:
            res = await session.execute(
                text(
                    """
                    INSERT INTO contact_group_members (organization_id, group_id, contact_id, created_at)
                    VALUES (:org_id, :group_id, :contact_id, NOW())
                    ON CONFLICT (group_id, contact_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {"org_id": org_id, "group_id": group_id, "contact_id": cid},
            )
            if res.fetchone():
                added += 1
        await session.commit()

    return {"added": added, "group_id": group_id}


@router.delete("/groups/{group_id}/contacts")
async def remove_contacts_from_group(
    group_id: int,
    req: AddContactsToGroupRequest,
    user: UserModel = Depends(get_user),
):
    """Remove contacts from a group."""
    if not req.contact_ids:
        return {"removed": 0}
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        await session.execute(
            text(
                """
                DELETE FROM contact_group_members
                WHERE organization_id = :org_id AND group_id = :group_id AND contact_id = ANY(:ids)
                """
            ),
            {"org_id": org_id, "group_id": group_id, "ids": req.contact_ids},
        )
        await session.commit()

    return {"removed": len(req.contact_ids), "group_id": group_id}


@router.post("/groups/{group_id}/add-numbers")
async def add_numbers_to_group(
    group_id: int,
    req: AddNumbersToGroupRequest,
    user: UserModel = Depends(get_user),
):
    """
    Add phone numbers directly into an existing group with strict deduplication:
    - If number is already in general directory, reuses existing contact without creating a duplicate.
    - If number is new, creates contact in general directory.
    - Links contact to the group. If already linked, gracefully skips without error.
    """
    await ensure_table()
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        # Check group exists
        g_check = await session.execute(
            text("SELECT id, name FROM contact_groups WHERE organization_id = :org_id AND id = :group_id"),
            {"org_id": org_id, "group_id": group_id},
        )
        group_row = g_check.fetchone()
        if not group_row:
            raise HTTPException(status_code=404, detail="Contact group not found")

        # Parse raw input into candidate items: list of (phone, name)
        candidates: List[tuple[str, str]] = []

        if req.numbers:
            for num in req.numbers:
                if num and str(num).strip():
                    candidates.append((str(num).strip(), "Customer"))

        if req.text:
            lines = [l.strip() for l in req.text.splitlines() if l.strip()]
            for line in lines:
                parts = [p.strip() for p in line.replace("\t", ",").split(",") if p.strip()]
                if len(parts) >= 2:
                    p0 = normalize_phone_number(parts[0])
                    p1 = normalize_phone_number(parts[1])
                    if p0:
                        candidates.append((parts[0], parts[1]))
                    elif p1:
                        candidates.append((parts[1], parts[0]))
                    else:
                        candidates.append((parts[0], parts[1]))
                else:
                    candidates.append((line, "Customer"))

        target_contact_ids: List[int] = list(req.contact_ids or [])
        seen_phones: set[str] = set()
        invalid_count = 0
        existing_linked = 0
        new_created = 0

        for raw_phone, raw_name in candidates:
            norm_phone = normalize_phone_number(raw_phone)
            if not norm_phone or len(norm_phone) < 10:
                invalid_count += 1
                continue
            if norm_phone in seen_phones:
                continue
            seen_phones.add(norm_phone)

            # Check if contact already exists in directory
            c_res = await session.execute(
                text("SELECT id FROM organization_contacts WHERE organization_id = :org_id AND phone = :phone"),
                {"org_id": org_id, "phone": norm_phone},
            )
            c_row = c_res.fetchone()
            if c_row:
                target_contact_ids.append(c_row.id)
                existing_linked += 1
            else:
                ins_res = await session.execute(
                    text(
                        """
                        INSERT INTO organization_contacts
                        (organization_id, name, phone, status, called, created_at, updated_at)
                        VALUES (:org_id, :name, :phone, 'valid', FALSE, NOW(), NOW())
                        RETURNING id
                        """
                    ),
                    {"org_id": org_id, "name": raw_name or "Customer", "phone": norm_phone},
                )
                ins_row = ins_res.fetchone()
                if ins_row:
                    target_contact_ids.append(ins_row.id)
                    new_created += 1

        # Now link all unique target_contact_ids into contact_group_members
        unique_ids = list(dict.fromkeys(target_contact_ids))
        added_count = 0
        already_in_group_count = 0

        for cid in unique_ids:
            link_res = await session.execute(
                text(
                    """
                    INSERT INTO contact_group_members (organization_id, group_id, contact_id, created_at)
                    VALUES (:org_id, :group_id, :contact_id, NOW())
                    ON CONFLICT (group_id, contact_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {"org_id": org_id, "group_id": group_id, "contact_id": cid},
            )
            if link_res.fetchone():
                added_count += 1
            else:
                already_in_group_count += 1

        await session.commit()

        return {
            "success": True,
            "group_id": group_id,
            "group_name": group_row.name,
            "added": added_count,
            "already_in_group": already_in_group_count,
            "existing_directory_linked": existing_linked,
            "new_contacts_created": new_created,
            "invalid_count": invalid_count,
            "total_processed": len(seen_phones) + len(req.contact_ids or []),
        }
