import base64
import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger

from api.constants import (
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


class RazorpayService:
    """Service to interact with the Razorpay REST API and verify signatures."""

    def __init__(
        self,
        key_id: str = RAZORPAY_KEY_ID,
        key_secret: str = RAZORPAY_KEY_SECRET,
        webhook_secret: str = RAZORPAY_WEBHOOK_SECRET,
    ):
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret

    def _get_auth_header(self) -> Dict[str, str]:
        auth_bytes = f"{self.key_id}:{self.key_secret}".encode("utf-8")
        encoded_auth = base64.b64encode(auth_bytes).decode("utf-8")
        return {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def generate_receipt_id(organization_id: int) -> str:
        """Generate a receipt identifier for Razorpay orders.

        Max length allowed by Razorpay is 40 characters.
        Format: rcpt_org{org_id}_{unix_timestamp}
        Example: rcpt_org1_1726404123
        This allows direct filtering in the Razorpay dashboard by typing 'rcpt_org1'.
        """
        timestamp = int(time.time())
        return f"rcpt_org{organization_id}_{timestamp}"

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a Razorpay order.

        Args:
            amount_paise: Amount in smallest currency unit (e.g. paise for INR).
            currency: Currency code (e.g. 'INR').
            receipt: Receipt identifier string (max 40 chars).
            notes: Key-value metadata pairs.

        Returns:
            Dict containing order details including 'id', 'amount', 'currency', etc.
        """
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,  # Auto-capture payment upon authorization
            "notes": notes or {},
        }

        url = f"{RAZORPAY_API_BASE}/orders"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                resp_data = await response.json()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Razorpay] Order creation failed HTTP {response.status}: {resp_data}"
                    )
                    error_msg = resp_data.get("error", {}).get(
                        "description", "Failed to create Razorpay order"
                    )
                    raise RuntimeError(f"Razorpay error: {error_msg}")
                logger.info(
                    f"[Razorpay] Created order {resp_data.get('id')} with receipt {receipt} for {amount_paise} paise"
                )
                return resp_data

    async def create_plan(
        self,
        name: str,
        amount_paise: int,
        currency: str = "INR",
        period: str = "monthly",
        interval: int = 1,
        description: Optional[str] = None,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a recurring plan in Razorpay for subscriptions."""
        payload = {
            "period": period,
            "interval": interval,
            "item": {
                "name": name,
                "amount": amount_paise,
                "currency": currency,
                "description": description or f"{name} ({period})",
            },
            "notes": notes or {},
        }
        url = f"{RAZORPAY_API_BASE}/plans"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                resp_data = await response.json()
                if response.status not in (200, 201):
                    logger.error(f"[Razorpay] Plan creation failed HTTP {response.status}: {resp_data}")
                    error_msg = resp_data.get("error", {}).get("description", "Failed to create Razorpay plan")
                    raise RuntimeError(f"Razorpay error: {error_msg}")
                logger.info(f"[Razorpay] Created plan {resp_data.get('id')} for {name} ({amount_paise} paise)")
                return resp_data

    async def create_subscription(
        self,
        plan_id: str,
        total_count: int = 120,
        quantity: int = 1,
        customer_notify: int = 1,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a recurring subscription (e-Mandate) in Razorpay."""
        payload = {
            "plan_id": plan_id,
            "total_count": total_count,
            "quantity": quantity,
            "customer_notify": customer_notify,
            "notes": notes or {},
        }
        url = f"{RAZORPAY_API_BASE}/subscriptions"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                resp_data = await response.json()
                if response.status not in (200, 201):
                    logger.error(f"[Razorpay] Subscription creation failed HTTP {response.status}: {resp_data}")
                    error_msg = resp_data.get("error", {}).get("description", "Failed to create Razorpay subscription")
                    raise RuntimeError(f"Razorpay error: {error_msg}")
                logger.info(f"[Razorpay] Created subscription {resp_data.get('id')} with plan {plan_id}")
                return resp_data

    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Fetch subscription details from Razorpay."""
        url = f"{RAZORPAY_API_BASE}/subscriptions/{subscription_id}"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                resp_data = await response.json()
                if response.status != 200:
                    logger.error(f"[Razorpay] Get subscription failed HTTP {response.status}: {resp_data}")
                    raise RuntimeError(f"Razorpay error: {resp_data}")
                return resp_data

    async def get_subscription_invoices(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Fetch all invoices belonging to a subscription from Razorpay."""
        url = f"{RAZORPAY_API_BASE}/invoices?subscription_id={subscription_id}"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return []
                resp_data = await response.json()
                return resp_data.get("items", [])

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Fetch order details from Razorpay."""
        url = f"{RAZORPAY_API_BASE}/orders/{order_id}"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                resp_data = await response.json()
                if response.status != 200:
                    logger.error(f"[Razorpay] Get order failed HTTP {response.status}: {resp_data}")
                    raise RuntimeError(f"Razorpay error: {resp_data}")
                return resp_data

    async def get_payment(self, payment_id: str) -> Dict[str, Any]:
        """Fetch payment details from Razorpay."""
        url = f"{RAZORPAY_API_BASE}/payments/{payment_id}"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                resp_data = await response.json()
                if response.status != 200:
                    logger.error(f"[Razorpay] Get payment failed HTTP {response.status}: {resp_data}")
                    raise RuntimeError(f"Razorpay error: {resp_data}")
                return resp_data


    async def cancel_subscription(
        self,
        subscription_id: str,
        cancel_at_cycle_end: bool = True,
    ) -> Dict[str, Any]:
        """Cancel an active Razorpay subscription."""
        payload = {
            "cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0,
        }
        url = f"{RAZORPAY_API_BASE}/subscriptions/{subscription_id}/cancel"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                resp_data = await response.json()
                if response.status not in (200, 201):
                    logger.error(f"[Razorpay] Cancel subscription failed HTTP {response.status}: {resp_data}")
                    error_msg = resp_data.get("error", {}).get("description", "Failed to cancel Razorpay subscription")
                    raise RuntimeError(f"Razorpay error: {error_msg}")
                logger.info(f"[Razorpay] Cancelled subscription {subscription_id}")
                return resp_data

    def verify_payment_signature(
        self,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> bool:
        """Verify Razorpay checkout payment signature using HMAC SHA256.

        Signature string format: order_id + "|" + payment_id
        """
        if not signature or not order_id or not payment_id:
            return False

        message = f"{order_id}|{payment_id}".encode("utf-8")
        secret_bytes = self.key_secret.encode("utf-8")
        generated_signature = hmac.new(
            secret_bytes, message, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)

    def verify_subscription_signature(
        self,
        subscription_id: str,
        payment_id: str,
        signature: str,
    ) -> bool:
        """Verify Razorpay recurring subscription e-Mandate signature using HMAC SHA256.

        Signature string format: payment_id + "|" + subscription_id
        """
        if not signature or not subscription_id or not payment_id:
            return False

        message = f"{payment_id}|{subscription_id}".encode("utf-8")
        secret_bytes = self.key_secret.encode("utf-8")
        generated_signature = hmac.new(
            secret_bytes, message, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Verify Razorpay webhook payload signature using HMAC SHA256."""
        if not signature or not raw_body:
            return False

        secret_bytes = self.webhook_secret.encode("utf-8")
        generated_signature = hmac.new(
            secret_bytes, raw_body, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)


razorpay_service = RazorpayService()
