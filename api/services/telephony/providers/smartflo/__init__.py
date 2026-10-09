"""Smartflo telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ConfigurationSetupState,
    ProviderSetupChecklist,
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    SetupStep,
    register,
)

from .config import SmartfloConfigurationRequest
from .provider import SmartfloProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    did = value.get("smartflo_did_number")
    from_numbers = list(value.get("from_numbers") or [])
    if did and did not in from_numbers:
        from_numbers.append(did)
    return {
        "provider": "smartflo",
        "click_to_call_api_key": value.get("click_to_call_api_key"),
        "smartflo_jwt_token": value.get("smartflo_jwt_token"),
        "smartflo_did_number": did,
        "smartflo_api_domain": value.get(
            "smartflo_api_domain", "https://api-smartflo.tatateleservices.com"
        ),
        "from_numbers": from_numbers,
        "phone_numbers_metadata": value.get("phone_numbers_metadata", {}),
    }


def resolve_smartflo_setup_checklist(
    credentials: Dict[str, Any],
    state: ConfigurationSetupState,
) -> ProviderSetupChecklist:
    has_api_key = bool(
        credentials.get("click_to_call_api_key")
        or credentials.get("api_key")
        or credentials.get("smartflo_api_key")
    )
    has_caller_id = bool(
        credentials.get("smartflo_did_number") or state.active_phone_number_count > 0
    )
    steps = [
        SetupStep(
            key="api_key",
            title="Tata Smartflo API Key",
            description="Tata Smartflo Click-to-Call Support API Key is required.",
            complete=has_api_key,
            blocks_outbound=True,
        ),
        SetupStep(
            key="caller_id",
            title="Caller ID / DID Number",
            description="Outbound calls need a number to dial from. Add a DID number in configuration credentials or under Phone numbers.",
            complete=has_caller_id,
            blocks_outbound=True,
        ),
    ]
    return ProviderSetupChecklist.from_steps(
        steps, docs_url="https://www.tatateleservices.com/smartflo"
    )


_UI_METADATA = ProviderUIMetadata(
    display_name="Tata Smartflo",
    docs_url="https://www.tatateleservices.com/smartflo",
    fields=[
        ProviderUIField(
            name="click_to_call_api_key",
            label="Click-to-Call API Key",
            type="password",
            sensitive=True,
            description="Tata Smartflo Click-to-Call Support API Key",
        ),
        ProviderUIField(
            name="smartflo_jwt_token",
            label="JWT Bearer Token",
            type="password",
            sensitive=True,
            required=False,
            description="Smartflo API authorization token",
        ),
        ProviderUIField(
            name="smartflo_did_number",
            label="Caller ID / DID Number",
            type="text",
            required=False,
            description="Smartflo Virtual / DID phone number used for caller ID",
        ),
        ProviderUIField(
            name="smartflo_api_domain",
            label="API Domain",
            type="text",
            required=False,
            description="Smartflo API base domain (e.g. https://api-smartflo.tatateleservices.com)",
        ),
    ],
)


SPEC = ProviderSpec(
    name="smartflo",
    provider_cls=SmartfloProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=SmartfloConfigurationRequest,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="click_to_call_api_key",
    setup_checklist_resolver=resolve_smartflo_setup_checklist,
)


register(SPEC)


__all__ = [
    "SPEC",
    "SmartfloConfigurationRequest",
    "SmartfloProvider",
    "create_transport",
]
