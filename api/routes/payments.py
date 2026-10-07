import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import (
    GST_PERCENTAGE,
    RAZORPAY_KEY_ID,
)
from api.db import db_client
from api.db.models import UserModel
from api.db.payment_client import payment_client
from api.services.auth.depends import get_user_with_selected_organization
from api.services.platform_settings import get_gst_percentage, get_usd_to_inr_rate
from api.services.razorpay_client import razorpay_service

router = APIRouter(prefix="/payments", tags=["payments"])


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class PaymentConfigResponse(BaseModel):
    key_id: str
    usd_to_inr_rate: float
    gst_percentage: float
    currency: str = "INR"
    min_recharge_usd: float = 1.0


class CreatePaymentOrderRequest(BaseModel):
    amount_usd: Optional[float] = Field(
        None,
        description="Amount in USD to add to organization wallet balance",
    )
    amount_inr: Optional[float] = Field(
        None,
        description="Amount in INR to add to organization wallet balance",
    )


class CreatePaymentOrderResponse(BaseModel):
    order_id: str
    key_id: str
    amount_paise: int
    amount_inr: float
    subtotal_inr: float
    gst_amount_inr: float
    gst_percentage: float
    amount_usd: float
    usd_to_inr_rate: float
    currency: str = "INR"
    receipt: str
    organization_id: int
    org_name: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class VerifyPaymentResponse(BaseModel):
    success: bool
    was_newly_credited: bool
    new_balance_usd: float
    amount_credited_usd: float
    receipt: str
    message: str


class PaymentTransactionItem(BaseModel):
    id: int
    organization_id: int
    receipt: str
    description: Optional[str] = None
    amount_usd: float
    amount_inr: float = 0.0
    currency: str = "INR"
    status: str
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/config",
    response_model=PaymentConfigResponse,
    summary="Get Payment Configuration and Conversion Rates",
)
async def get_payment_config(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Return current exchange rate, GST percentage, and public Razorpay Key ID."""
    return PaymentConfigResponse(
        key_id=RAZORPAY_KEY_ID,
        usd_to_inr_rate=get_usd_to_inr_rate(),
        gst_percentage=get_gst_percentage(),
        currency="INR",
        min_recharge_usd=1.0,
    )


@router.post(
    "/razorpay/create-order",
    response_model=CreatePaymentOrderResponse,
    summary="Create Razorpay Order for Wallet Recharge",
)
async def create_razorpay_order(
    request: CreatePaymentOrderRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Create a new Razorpay order in INR with GST and register a pending transaction.

    The order is converted from USD to INR using the live USD to INR rate with GST.
    The receipt ID is uniquely formatted as 'rcpt_org{org_id}_{timestamp}'
    so it can easily be searched and filtered in the Razorpay Dashboard.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization selected",
        )

    usd_rate = get_usd_to_inr_rate()
    if request.amount_inr is not None and request.amount_inr > 0:
        amount_usd = round(request.amount_inr / usd_rate, 4)
        subtotal_inr = round(float(request.amount_inr), 2)
    elif request.amount_usd is not None and request.amount_usd > 0:
        amount_usd = float(request.amount_usd)
        subtotal_inr = round(amount_usd * usd_rate, 2)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid amount_inr or amount_usd is required",
        )

    # Fetch organization details for display name
    org = await db_client.get_organization_by_id(organization_id)
    org_name = getattr(org, "name", None) or f"Organization #{organization_id}"

    # Calculate INR conversion and GST using dynamic platform rates
    gst = get_gst_percentage()
    gst_amount_inr = round(subtotal_inr * (gst / 100.0), 2)
    total_inr = round(subtotal_inr + gst_amount_inr, 2)
    amount_paise = int(round(total_inr * 100))

    # Generate proper receipt identifier (max 40 chars)
    # Allows filtering in Razorpay dashboard by 'rcpt_org{id}'
    receipt = razorpay_service.generate_receipt_id(organization_id)

    notes: Dict[str, Any] = {
        "platform": "CallioAI",
        "receipt": receipt,
        "organization_id": str(organization_id),
        "organization_name": org_name[:40] if org_name else "",
        "user_id": str(user.id),
        "user_email": str(user.email or ""),
        "purpose": "calling_wallet_recharge",
        "amount_usd": str(amount_usd),
        "usd_to_inr_rate": str(usd_rate),
        "subtotal_inr": str(subtotal_inr),
        "gst_percentage": str(gst),
        "gst_amount_inr": str(gst_amount_inr),
        "total_inr": str(total_inr),
    }

    # Create order directly via Razorpay API
    order_data = await razorpay_service.create_order(
        amount_paise=amount_paise,
        currency="INR",
        receipt=receipt,
        notes=notes,
    )

    razorpay_order_id = order_data["id"]

    # Register the pending transaction in database
    await payment_client.create_pending_transaction(
        organization_id=organization_id,
        user_id=user.id,
        amount_usd=amount_usd,
        amount_inr=total_inr,
        receipt=receipt,
        razorpay_order_id=razorpay_order_id,
        notes=notes,
    )

    return CreatePaymentOrderResponse(
        order_id=razorpay_order_id,
        key_id=RAZORPAY_KEY_ID,
        amount_paise=amount_paise,
        amount_inr=total_inr,
        subtotal_inr=subtotal_inr,
        gst_amount_inr=gst_amount_inr,
        gst_percentage=gst,
        amount_usd=amount_usd,
        usd_to_inr_rate=usd_rate,
        currency="INR",
        receipt=receipt,
        organization_id=organization_id,
        org_name=org_name,
    )



@router.post(
    "/razorpay/verify",
    response_model=VerifyPaymentResponse,
    summary="Verify Razorpay Payment and Credit Wallet",
)
@router.post(
    "/razorpay/verify-payment",
    response_model=VerifyPaymentResponse,
    summary="Verify Razorpay Payment and Credit Wallet (Alias)",
)
async def verify_razorpay_payment(
    request: VerifyPaymentRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Verify payment signature from checkout and atomically credit the wallet balance."""
    # 1. Verify HMAC SHA256 signature
    is_valid = razorpay_service.verify_payment_signature(
        order_id=request.razorpay_order_id,
        payment_id=request.razorpay_payment_id,
        signature=request.razorpay_signature,
    )

    if not is_valid:
        logger.warning(
            f"[Razorpay] Invalid signature received for order {request.razorpay_order_id}"
        )
        await payment_client.mark_transaction_failed(
            request.razorpay_order_id, reason="Invalid signature"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payment verification signature",
        )

    # 2. Idempotently mark as paid and credit wallet balance
    try:
        was_newly_credited, new_bal, tx = (
            await payment_client.mark_transaction_paid_and_credit_wallet(
                razorpay_order_id=request.razorpay_order_id,
                razorpay_payment_id=request.razorpay_payment_id,
                razorpay_signature=request.razorpay_signature,
            )
        )
    except Exception as exc:
        logger.error(f"Error processing payment verification in DB: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    amount_credited = tx.amount_usd if tx else 0.0
    receipt = tx.receipt if tx else ""

    return VerifyPaymentResponse(
        success=True,
        was_newly_credited=was_newly_credited,
        new_balance_usd=new_bal,
        amount_credited_usd=amount_credited,
        receipt=receipt,
        message=(
            f"Successfully credited ${amount_credited:.2f} USD to wallet"
            if was_newly_credited
            else "Payment already verified and credited"
        ),
    )


@router.post(
    "/razorpay/webhook",
    summary="Razorpay Webhook for Payment Confirmation",
)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None, alias="X-Razorpay-Signature"),
):
    """Webhook listener for server-to-server payment capture notifications from Razorpay."""
    raw_body = await request.body()

    if not x_razorpay_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Razorpay-Signature header",
        )

    is_valid = razorpay_service.verify_webhook_signature(
        raw_body=raw_body,
        signature=x_razorpay_signature,
    )

    if not is_valid:
        logger.warning("[Razorpay Webhook] Invalid signature received")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    try:
        event_data = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        logger.error(f"[Razorpay Webhook] JSON parse error: {exc}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = event_data.get("event")
    logger.info(f"[Razorpay Webhook] Received event: {event_type}")

    if event_type in ("payment.captured", "order.paid"):
        payment_entity = (
            event_data.get("payload", {}).get("payment", {}).get("entity", {})
        )
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")

        if not order_id and event_type == "order.paid":
            order_entity = (
                event_data.get("payload", {}).get("order", {}).get("entity", {})
            )
            order_id = order_entity.get("id")

        if order_id and payment_id:
            try:
                was_newly_credited, new_bal, tx = (
                    await payment_client.mark_transaction_paid_and_credit_wallet(
                        razorpay_order_id=order_id,
                        razorpay_payment_id=payment_id,
                    )
                )
                logger.info(
                    f"[Razorpay Webhook] Order {order_id} handled. "
                    f"Newly credited: {was_newly_credited}, New balance: ${new_bal:.2f}"
                )
            except Exception as exc:
                logger.error(f"[Razorpay Webhook] Error updating wallet balance: {exc}")

    elif event_type == "subscription.charged":
        subscription_entity = (
            event_data.get("payload", {}).get("subscription", {}).get("entity", {})
        )
        payment_entity = (
            event_data.get("payload", {}).get("payment", {}).get("entity", {})
        )
        sub_id = subscription_entity.get("id")
        payment_id = payment_entity.get("id")
        notes = subscription_entity.get("notes", {}) or {}
        org_id_val = notes.get("organization_id")
        plan_slug = notes.get("plan_slug")

        logger.info(
            f"[Razorpay Webhook] subscription.charged: sub_id={sub_id}, payment_id={payment_id}, org_id={org_id_val}"
        )

        from api.db.models import OrganizationModel, PaymentTransactionModel
        from api.services.plan_service import plan_service
        from sqlalchemy import select
        from datetime import datetime, UTC, timedelta
        import time

        async with db_client.async_session() as session:
            org = None
            if org_id_val and str(org_id_val).isdigit():
                org = await session.get(OrganizationModel, int(org_id_val))
            if not org and sub_id:
                stmt = select(OrganizationModel).where(OrganizationModel.razorpay_subscription_id == sub_id)
                res = await session.execute(stmt)
                org = res.scalars().first()

            if org:
                now = datetime.now(UTC)
                org.billing_cycle_start = now
                org.billing_cycle_end = now + timedelta(days=30)
                org.monthly_minutes_used = 0.0
                org.subscription_status = "active"

                plan_to_use = plan_slug or org.subscription_tier
                plan = await plan_service.get_plan_by_slug(plan_to_use)
                if plan and getattr(plan, "monthly_credits_usd", 0.0):
                    org.plan_credits_monthly_usd = float(plan.monthly_credits_usd)
                    org.plan_credits_remaining_usd = float(plan.monthly_credits_usd)
                    org.plan_credits_reset_at = now + timedelta(days=30)

                amount_inr = float(payment_entity.get("amount", 0)) / 100.0
                usd_rate = get_usd_to_inr_rate()
                amount_usd = round(amount_inr / usd_rate, 2)
                receipt = f"sub_ren_{sub_id[-8:] if sub_id else 'unk'}_{int(time.time())}"

                tx_stmt = select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_payment_id == payment_id
                )
                tx_res = await session.execute(tx_stmt)
                existing_tx = tx_res.scalars().first()

                if not existing_tx:
                    tx = PaymentTransactionModel(
                        organization_id=org.id,
                        user_id=None,
                        amount_usd=amount_usd,
                        amount_inr=amount_inr,
                        currency="INR",
                        receipt=receipt,
                        razorpay_order_id=payment_entity.get("order_id") or (sub_id or ""),
                        razorpay_subscription_id=sub_id,
                        razorpay_payment_id=payment_id,
                        razorpay_signature=x_razorpay_signature,
                        status="paid",
                        notes={
                            "event": "subscription.charged",
                            "subscription_id": sub_id,
                            "plan_slug": plan_to_use,
                            "purpose": "subscription_recurring_charge",
                        },
                    )
                    session.add(tx)

                await session.commit()
                logger.info(
                    f"[Razorpay Webhook] Recurring subscription charged for Org {org.id}. "
                    f"New cycle ends at {org.billing_cycle_end}."
                )

    elif event_type == "subscription.halted":
        subscription_entity = (
            event_data.get("payload", {}).get("subscription", {}).get("entity", {})
        )
        sub_id = subscription_entity.get("id")
        notes = subscription_entity.get("notes", {}) or {}
        org_id_val = notes.get("organization_id")

        logger.warning(f"[Razorpay Webhook] subscription.halted for sub_id={sub_id}")
        from api.db.models import OrganizationModel
        from sqlalchemy import select

        async with db_client.async_session() as session:
            org = None
            if org_id_val and str(org_id_val).isdigit():
                org = await session.get(OrganizationModel, int(org_id_val))
            if not org and sub_id:
                stmt = select(OrganizationModel).where(OrganizationModel.razorpay_subscription_id == sub_id)
                res = await session.execute(stmt)
                org = res.scalars().first()

            if org:
                org.subscription_status = "halted"
                await session.commit()
                logger.warning(f"[Razorpay Webhook] Organization {org.id} subscription status set to 'halted'.")

    elif event_type == "subscription.cancelled":
        subscription_entity = (
            event_data.get("payload", {}).get("subscription", {}).get("entity", {})
        )
        sub_id = subscription_entity.get("id")
        notes = subscription_entity.get("notes", {}) or {}
        org_id_val = notes.get("organization_id")

        from api.db.models import OrganizationModel
        from sqlalchemy import select

        async with db_client.async_session() as session:
            org = None
            if org_id_val and str(org_id_val).isdigit():
                org = await session.get(OrganizationModel, int(org_id_val))
            if not org and sub_id:
                stmt = select(OrganizationModel).where(OrganizationModel.razorpay_subscription_id == sub_id)
                res = await session.execute(stmt)
                org = res.scalars().first()

            if org:
                org.subscription_status = "cancelled"
                org.razorpay_subscription_id = None
                await session.commit()
                logger.info(f"[Razorpay Webhook] Organization {org.id} subscription cancelled.")

    elif event_type in ("subscription.authenticated", "subscription.activated"):
        subscription_entity = (
            event_data.get("payload", {}).get("subscription", {}).get("entity", {})
        )
        sub_id = subscription_entity.get("id")
        notes = subscription_entity.get("notes", {}) or {}
        org_id_val = notes.get("organization_id")

        from api.db.models import OrganizationModel
        from sqlalchemy import select

        async with db_client.async_session() as session:
            org = None
            if org_id_val and str(org_id_val).isdigit():
                org = await session.get(OrganizationModel, int(org_id_val))
            if not org and sub_id:
                stmt = select(OrganizationModel).where(OrganizationModel.razorpay_subscription_id == sub_id)
                res = await session.execute(stmt)
                org = res.scalars().first()

            if org:
                org.subscription_status = "active"
                await session.commit()

    return {"status": "ok"}


@router.get(
    "/transactions",
    response_model=List[PaymentTransactionItem],
    summary="List Organization Payment Transactions",
)
async def list_transactions(
    limit: int = Query(20, ge=1, le=100),
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """List recent wallet recharge transactions for the selected organization."""
    organization_id = user.selected_organization_id
    if not organization_id:
        return []

    transactions = await payment_client.list_organization_transactions(
        organization_id=organization_id,
        limit=limit,
    )

    items = []
    for tx in transactions:
        notes = tx.notes if isinstance(tx.notes, dict) else (json.loads(tx.notes) if tx.notes else {})
        plan_name = notes.get("plan_name")
        purpose = notes.get("purpose")
        if plan_name:
            desc_text = f"Plan: {plan_name}"
        elif purpose == "calling_wallet_recharge":
            desc_text = f"Wallet Recharge (${tx.amount_usd:.2f} USD)"
        elif purpose == "subscription_recurring_charge":
            desc_text = f"Auto-Pay Renewal: {plan_name or 'Subscription'}"
        else:
            desc_text = tx.receipt or "Payment"

        items.append(
            PaymentTransactionItem(
                id=tx.id,
                organization_id=tx.organization_id,
                receipt=tx.receipt or "",
                description=desc_text,
                amount_usd=tx.amount_usd or 0.0,
                amount_inr=getattr(tx, "amount_inr", 0.0) or 0.0,
                currency=tx.currency or "INR",
                status=tx.status,
                razorpay_order_id=tx.razorpay_order_id,
                razorpay_payment_id=tx.razorpay_payment_id,
                created_at=tx.created_at,
            )
        )

    return items

