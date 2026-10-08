"""Smartflo credential resolution and security masking.

Resolution priority:
1. Request Body (call_details)
2. Agent configuration (workflow_configurations)
3. Organization configuration (telephony_configurations / organization_configurations)
4. Environment variables
"""

import os
import re
from typing import Any, Dict, Optional, Tuple


def mask_phone_number(phone: Optional[str]) -> str:
    """Mask phone number for safe logging (e.g. 919999999999 -> 91******9999)."""
    if not phone:
        return "UNKNOWN"
    phone_str = str(phone).strip()
    if len(phone_str) <= 6:
        return "***"
    return f"{phone_str[:2]}******{phone_str[-4:]}"


def resolve_smartflo_credentials(
    call_details: Optional[Dict[str, Any]] = None,
    agent_config: Optional[Dict[str, Any]] = None,
    org_config: Optional[Dict[str, Any]] = None,
    from_number: Optional[str] = None,
) -> Tuple[str, str, str, str]:
    """
    Resolve Smartflo credentials according to strict priority order.

    Resolution priority:
    1. Request Body (call_details)
    2. Per-number extra_metadata (if a specific caller ID is dialed from)
    3. Agent configuration (workflow_configurations)
    4. Organization configuration (telephony_configurations)
    5. Environment variables

    Returns:
        Tuple of (click_to_call_api_key, did_number, jwt_token, api_domain)

    Raises:
        ValueError: If click_to_call_api_key or did_number is missing.
    """
    call_details = call_details or {}
    agent_config = agent_config or {}
    org_config = org_config or {}

    # Extract target number to check for per-number credentials
    phone_metadata = org_config.get("phone_numbers_metadata") or {}
    target_meta: Dict[str, Any] = {}

    number_candidates = [
        str(from_number or "").strip(),
        str(call_details.get("caller_id") or "").strip(),
        str(call_details.get("from_number") or "").strip(),
    ]
    for num in number_candidates:
        if not num:
            continue
        clean_d = re.sub(r"[^\d]", "", num)
        if num in phone_metadata:
            target_meta = phone_metadata[num] or {}
            break
        if clean_d and clean_d in phone_metadata:
            target_meta = phone_metadata[clean_d] or {}
            break

    # 1. Click-to-Call API Key (Request > Per-Number Metadata > Agent Config > Org Config > Environment)
    click_to_call_api_key = (
        call_details.get("smartflo_api_key")
        or call_details.get("click_to_call_api_key")
        or target_meta.get("click_to_call_api_key")
        or target_meta.get("smartflo_api_key")
        or target_meta.get("api_key")
        or agent_config.get("smartflo_api_key")
        or agent_config.get("click_to_call_api_key")
        or org_config.get("click_to_call_api_key")
        or os.getenv("SMARTFLO_CLICK_TO_CALL_API_KEY")
    )

    # 2. DID Number / Caller ID (Explicit from_number > Request > Org Default Phone > Environment)
    from_numbers = org_config.get("from_numbers") or []
    first_from_number = from_numbers[0] if isinstance(from_numbers, list) and from_numbers else None

    did_number = (
        from_number
        or call_details.get("caller_id")
        or call_details.get("from_number")
        or call_details.get("smartflo_did_number")
        or org_config.get("default_from_number")
        or org_config.get("smartflo_did_number")
        or first_from_number
        or os.getenv("SMARTFLO_DID_NUMBER")
    )

    # 3. JWT Token (Request > Per-Number Metadata > Agent Config > Org Config > Environment)
    jwt_token = (
        call_details.get("smartflo_jwt_token")
        or target_meta.get("smartflo_jwt_token")
        or target_meta.get("jwt_token")
        or agent_config.get("smartflo_jwt_token")
        or org_config.get("smartflo_jwt_token")
        or os.getenv("SMARTFLO_JWT_TOKEN")
    )

    # 4. API Domain
    api_domain = (
        call_details.get("smartflo_api_domain")
        or target_meta.get("smartflo_api_domain")
        or org_config.get("smartflo_api_domain")
        or os.getenv("SMARTFLO_API_DOMAIN", "https://api-smartflo.tatateleservices.com")
    )

    if not click_to_call_api_key:
        raise ValueError("Missing required Smartflo Click-to-Call API Key")

    if not did_number:
        raise ValueError("Missing required Smartflo DID Number (caller_id)")

    api_domain = api_domain.rstrip("/")

    return (
        str(click_to_call_api_key).strip(),
        str(did_number).strip(),
        str(jwt_token or "").strip(),
        str(api_domain).strip(),
    )
