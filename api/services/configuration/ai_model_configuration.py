"""Read an organization's effective model configuration from the catalog."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from api.constants import MPS_API_URL
from api.db import db_client
from api.db.models import OrganizationConfigurationModel
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import (
    DOGRAH_DEFAULT_LANGUAGE,
    DOGRAH_DEFAULT_VOICE,
    DOGRAH_SPEED_MAX,
    DOGRAH_SPEED_MIN,
    DOGRAH_SPEED_OPTIONS,
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    BYOKRealtimeAIModelConfiguration,
    DograhManagedAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.masking import (
    SERVICE_SECRET_FIELDS,
    contains_masked_key,
    mask_key,
    resolve_masked_api_keys,
)
from api.services.configuration.registry import ServiceProviders
from api.services.configuration.resolve import resolve_effective_config

AIModelConfigurationSource = Literal[
    "organization_v3", "organization_v2", "platform_defaults", "legacy_user_v1", "empty"
]
# Retired inline workflow key. Rows converted by the catalog backfill keep it
# as audit data; no code reads it, and saves drop it.
WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY = "model_configuration_v2_override"


@dataclass
class ResolvedAIModelConfiguration:
    effective: EffectiveAIModelConfiguration
    source: AIModelConfigurationSource
    organization_configuration: OrganizationAIModelConfigurationV2 | None = None


@dataclass
class WorkflowAIModelConfigurationMigrationResult:
    workflow_count: int = 0
    definition_count: int = 0
    workflow_ids: list[int] | None = None


def populate_effective_config_from_platform_defaults(
    effective: EffectiveAIModelConfiguration,
) -> EffectiveAIModelConfiguration:
    """If any service section (llm, stt, tts) is not configured or uses legacy Dograh proxy, fill from active default platform master key."""
    from api.services.platform_keys import (
        get_default_platform_provider_and_model,
        get_platform_default_voice,
    )
    from api.services.configuration.registry import REGISTRY, ServiceProviders, ServiceType

    replaced_any = False

    # 1. LLM
    is_dograh_llm = effective.llm is not None and getattr(effective.llm, "provider", None) in (
        ServiceProviders.DOGRAH,
        ServiceProviders.DOGRAH.value,
        "dograh",
    )
    if effective.llm is None or is_dograh_llm:
        def_llm = get_default_platform_provider_and_model("llm")
        if def_llm:
            prov, key, def_model = def_llm
            cls = REGISTRY.get(ServiceType.LLM, {}).get(prov)
            if cls:
                try:
                    kwargs: dict = {"provider": prov, "api_key": key}
                    if def_model:
                        if prov == "groq" and def_model == "llama-3.3-70b-versatile":
                            def_model = "openai/gpt-oss-120b"
                        kwargs["model"] = def_model
                    effective.llm = cls(**kwargs)
                    replaced_any = True
                except Exception as e:
                    logger.warning("Could not auto-populate default LLM service: {}", e)

    # 2. STT
    is_dograh_stt = effective.stt is not None and getattr(effective.stt, "provider", None) in (
        ServiceProviders.DOGRAH,
        ServiceProviders.DOGRAH.value,
        "dograh",
    )
    if effective.stt is None or is_dograh_stt:
        def_stt = get_default_platform_provider_and_model("stt")
        if def_stt:
            prov, key, def_model = def_stt
            cls = REGISTRY.get(ServiceType.STT, {}).get(prov)
            if cls:
                try:
                    kwargs = {"provider": prov, "api_key": key}
                    if def_model:
                        kwargs["model"] = def_model
                    effective.stt = cls(**kwargs)
                    replaced_any = True
                except Exception as e:
                    logger.warning("Could not auto-populate default STT service: {}", e)

    # 3. TTS
    is_dograh_tts = effective.tts is not None and getattr(effective.tts, "provider", None) in (
        ServiceProviders.DOGRAH,
        ServiceProviders.DOGRAH.value,
        "dograh",
    )
    if effective.tts is None or is_dograh_tts:
        def_tts = get_default_platform_provider_and_model("tts")
        if def_tts:
            prov, key, def_model = def_tts
            cls = REGISTRY.get(ServiceType.TTS, {}).get(prov)
            if cls:
                try:
                    kwargs = {"provider": prov, "api_key": key}
                    if def_model:
                        kwargs["model"] = def_model
                    plat_voice = get_platform_default_voice("tts", prov)
                    if plat_voice:
                        kwargs["voice"] = plat_voice
                    elif prov == "cartesia":
                        kwargs["voice"] = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
                    effective.tts = cls(**kwargs)
                    replaced_any = True
                except Exception as e:
                    logger.warning("Could not auto-populate default TTS service: {}", e)

    # If any Dograh services were replaced by platform master keys, turn off managed MPS v2 proxy mode
    if replaced_any and getattr(effective, "managed_service_version", None):
        effective.managed_service_version = None

    return effective


async def get_resolved_ai_model_configuration(
    *,
    organization_id: int | None,
) -> ResolvedAIModelConfiguration:
    """Resolve the effective model configuration for an organization."""
    organization_configuration_row = (
        await _get_organization_ai_model_configuration_v2_row(organization_id)
    )
    organization_configuration = _parse_organization_ai_model_configuration_v2(
        organization_configuration_row,
        organization_id,
    )

    if organization_id is not None:
        try:
            from api.services.configuration.model_connections import (
                DEFAULT_KEY,
                get_default_model_configuration,
                resolve_model_configuration,
            )

            default = await get_default_model_configuration(organization_id)
            if default is not None:
                default_pointer = await db_client.get_configuration(organization_id, DEFAULT_KEY)
                v2_updated = (
                    organization_configuration_row.updated_at or organization_configuration_row.last_validated_at
                    if organization_configuration_row
                    else None
                )
                v3_updated = default_pointer.updated_at if default_pointer else getattr(default, "updated_at", None)

                # If V2 was updated after V3 default was set/updated, V2 takes precedence
                if (
                    v2_updated
                    and v3_updated
                    and v2_updated > v3_updated
                    and organization_configuration is not None
                ):
                    effective = compile_ai_model_configuration_v2(organization_configuration)
                    effective = populate_effective_config_from_platform_defaults(effective)
                    if organization_configuration_row is not None:
                        effective.last_validated_at = (
                            organization_configuration_row.last_validated_at
                        )
                    return ResolvedAIModelConfiguration(
                        effective=effective,
                        source="organization_v2",
                        organization_configuration=organization_configuration,
                    )

                resolved = await resolve_model_configuration(
                    organization_id, default_row=default
                )
                return ResolvedAIModelConfiguration(
                    effective=resolved.effective,
                    source="organization_v3",
                    organization_configuration=organization_configuration,
                )
        except Exception as e:
            logger.debug("Could not resolve v3 model configuration: {}", e)

    if organization_configuration is not None:
        effective = compile_ai_model_configuration_v2(organization_configuration)
        effective = populate_effective_config_from_platform_defaults(effective)
        if organization_configuration_row is not None:
            effective.last_validated_at = (
                organization_configuration_row.last_validated_at
            )
        return ResolvedAIModelConfiguration(
            effective=effective,
            source="organization_v2",
            organization_configuration=organization_configuration,
        )

    effective = populate_effective_config_from_platform_defaults(EffectiveAIModelConfiguration())
    return ResolvedAIModelConfiguration(
        effective=effective,
        source="platform_defaults" if (effective.llm or effective.stt or effective.tts) else "empty",
    )


async def get_effective_ai_model_configuration_for_workflow(
    *,
    organization_id: int | None,
    workflow_configurations: dict | None,
) -> EffectiveAIModelConfiguration:
    """The definition's catalog binding layered on the organization default."""
    workflow_configurations = workflow_configurations or {}
    if workflow_configurations.get("model_configuration_override") is not None:
        from api.services.configuration.model_connections import (
            resolve_model_configuration,
        )

        if organization_id is None:
            raise HTTPException(
                status_code=400,
                detail="Organization is required for model configuration.",
            )
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=workflow_configurations["model_configuration_override"],
        )
        return resolved.effective
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=organization_id,
    )
    return resolve_effective_config(
        resolved_config.effective,
        workflow_configurations.get("model_overrides"),
    )


async def get_organization_ai_model_configuration_v2(
    organization_id: int | None,
) -> OrganizationAIModelConfigurationV2 | None:
    row = await _get_organization_ai_model_configuration_v2_row(organization_id)
    return _parse_organization_ai_model_configuration_v2(row, organization_id)


async def update_organization_ai_model_configuration_last_validated_at(
    organization_id: int,
) -> None:
    await db_client.mark_configuration_validated(
        organization_id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
    )


async def _get_organization_ai_model_configuration_v2_row(
    organization_id: int | None,
) -> OrganizationConfigurationModel | None:
    if organization_id is None:
        return None
    return await db_client.get_configuration(
        organization_id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
    )


def _parse_organization_ai_model_configuration_v2(
    row: OrganizationConfigurationModel | None,
    organization_id: int | None,
) -> OrganizationAIModelConfigurationV2 | None:
    if row is None or not row.value:
        return None
    try:
        return OrganizationAIModelConfigurationV2.model_validate(row.value)
    except ValidationError as exc:
        logger.warning(
            "Invalid org AI model configuration v2 for organization "
            f"{organization_id}: {exc}. Falling back to legacy configuration."
        )
        return None


async def upsert_organization_ai_model_configuration_v2(
    organization_id: int,
    configuration: OrganizationAIModelConfigurationV2,
) -> OrganizationAIModelConfigurationV2:
    await db_client.upsert_configuration(
        organization_id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
        configuration.model_dump(mode="json", exclude_none=True),
        last_validated_at=datetime.now(UTC),
    )
    return configuration


async def migrate_workflow_model_configurations_to_v2(
    *,
    organization_id: int,
    fallback_user_config: EffectiveAIModelConfiguration,
) -> WorkflowAIModelConfigurationMigrationResult:
    workflows = await db_client.list_workflows_for_model_configuration_migration(
        organization_id
    )
    workflow_updates: list[tuple[int, dict]] = []
    definition_updates: list[tuple[int, dict]] = []
    migrated_workflow_ids: set[int] = set()

    for workflow in workflows:
        base_config = fallback_user_config

        workflow_configs, workflow_changed = (
            migrate_workflow_configuration_model_override_to_v2(
                workflow.workflow_configurations,
                base_config,
            )
        )
        if workflow_changed:
            workflow_updates.append((workflow.id, workflow_configs))
            migrated_workflow_ids.add(workflow.id)

        for definition in workflow.definitions:
            definition_configs, definition_changed = (
                migrate_workflow_configuration_model_override_to_v2(
                    definition.workflow_configurations,
                    base_config,
                )
            )
            if definition_changed:
                definition_updates.append((definition.id, definition_configs))
                migrated_workflow_ids.add(workflow.id)

    if workflow_updates or definition_updates:
        await db_client.bulk_update_workflow_model_configurations(
            organization_id=organization_id,
            workflow_updates=workflow_updates,
            definition_updates=definition_updates,
        )

    return WorkflowAIModelConfigurationMigrationResult(
        workflow_count=len(migrated_workflow_ids),
        definition_count=len(definition_updates),
        workflow_ids=sorted(migrated_workflow_ids),
    )


def migrate_workflow_configuration_model_override_to_v2(
    workflow_configurations: dict | None,
    base_config: EffectiveAIModelConfiguration,
) -> tuple[dict, bool]:
    if not isinstance(workflow_configurations, dict):
        return {}, False

    migrated = copy.deepcopy(workflow_configurations)
    model_overrides = migrated.get("model_overrides")
    existing_v2_override = migrated.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    if not isinstance(model_overrides, dict):
        if "model_overrides" in migrated:
            migrated.pop("model_overrides", None)
            return migrated, True
        return migrated, False

    if not existing_v2_override:
        effective = resolve_effective_config(base_config, model_overrides)
        v2_override = convert_legacy_ai_model_configuration_to_v2(effective)
        migrated[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = v2_override.model_dump(
            mode="json", exclude_none=True
        )
    migrated.pop("model_overrides", None)
    return migrated, True


def merge_ai_model_configuration_v2_secrets(
    incoming: OrganizationAIModelConfigurationV2,
    existing: OrganizationAIModelConfigurationV2 | None,
) -> OrganizationAIModelConfigurationV2:
    incoming_dict = incoming.model_dump(mode="json", exclude_none=True)
    existing_dict = existing.model_dump(mode="json", exclude_none=True) if existing is not None else {}
    use_platform_credentials = bool(incoming_dict.get("use_platform_credentials"))

    if incoming_dict.get("mode") == "dograh" and existing_dict.get("mode") == "dograh":
        incoming_dograh = incoming_dict.get("dograh") or {}
        existing_dograh = existing_dict.get("dograh") or {}
        incoming_key = incoming_dograh.get("api_key")
        existing_key = existing_dograh.get("api_key")
        if incoming_key and existing_key and contains_masked_key(incoming_key):
            incoming_dograh["api_key"] = resolve_masked_api_keys(
                incoming_key,
                existing_key,
            )

    if incoming_dict.get("mode") == "byok":
        _merge_byok_secret_fields(
            incoming_dict.get("byok"),
            existing_dict.get("byok") if existing_dict.get("mode") == "byok" else None,
            use_platform_credentials=use_platform_credentials,
        )

    return OrganizationAIModelConfigurationV2.model_validate(incoming_dict)


def check_for_masked_keys_in_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2,
) -> None:
    data = configuration.model_dump(mode="json", exclude_none=True)
    _raise_if_masked_secret(data)


def mask_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2 | None,
) -> dict | None:
    if configuration is None:
        return None
    data = configuration.model_dump(mode="json", exclude_none=True)
    _mask_secret_fields(data)
    return data


def convert_legacy_ai_model_configuration_to_v2(
    configuration: EffectiveAIModelConfiguration,
) -> OrganizationAIModelConfigurationV2:
    dograh_key = _first_dograh_api_key(configuration)
    if dograh_key:
        return _convert_any_dograh_legacy_configuration(configuration, dograh_key)

    if configuration.is_realtime:
        if configuration.realtime is None or configuration.llm is None:
            raise ValueError("Realtime legacy configuration is incomplete")
        return OrganizationAIModelConfigurationV2(
            mode="byok",
            use_platform_credentials=getattr(configuration, "use_platform_credentials", False),
            byok=BYOKAIModelConfiguration(
                mode="realtime",
                realtime=BYOKRealtimeAIModelConfiguration(
                    realtime=configuration.realtime,
                    llm=configuration.llm,
                    embeddings=configuration.embeddings,
                ),
            ),
        )

    if (
        configuration.llm is None
        or configuration.tts is None
        or configuration.stt is None
    ):
        raise ValueError("Pipeline legacy configuration is incomplete")
    return OrganizationAIModelConfigurationV2(
        mode="byok",
        use_platform_credentials=getattr(configuration, "use_platform_credentials", False),
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=configuration.llm,
                tts=configuration.tts,
                stt=configuration.stt,
                embeddings=configuration.embeddings,
            ),
        ),
    )



def dograh_embeddings_base_url() -> str:
    # AsyncOpenAI appends "/embeddings"; MPS exposes that under /api/v1/llm.
    return f"{MPS_API_URL}/api/v1/llm"


def apply_managed_embeddings_base_url(
    *,
    provider: str | None,
    base_url: str | None,
) -> str | None:
    if provider == ServiceProviders.DOGRAH.value or provider == ServiceProviders.DOGRAH:
        return dograh_embeddings_base_url()
    return base_url


def _merge_byok_secret_fields(
    incoming_byok: dict | None,
    existing_byok: dict | None,
    use_platform_credentials: bool = False,
):
    if not isinstance(incoming_byok, dict):
        return
    incoming_mode = incoming_byok.get("mode")
    existing_mode = existing_byok.get("mode") if isinstance(existing_byok, dict) else None
    section_names = (
        ("llm", "tts", "stt", "embeddings")
        if incoming_mode == "pipeline"
        else ("realtime", "llm", "embeddings")
    )
    incoming_container = incoming_byok.get(incoming_mode)
    existing_container = (
        existing_byok.get(existing_mode)
        if isinstance(existing_byok, dict) and incoming_mode == existing_mode
        else None
    )
    if not isinstance(incoming_container, dict):
        return
    for section_name in section_names:
        incoming_section = incoming_container.get(section_name)
        existing_section = (
            existing_container.get(section_name)
            if isinstance(existing_container, dict)
            else None
        )
        if isinstance(incoming_section, dict):
            _merge_service_secret_fields(
                incoming_section,
                existing_section,
                service_name=section_name,
                use_platform_credentials=use_platform_credentials,
            )


def _merge_service_secret_fields(
    incoming: dict,
    existing: dict | None,
    service_name: str | None = None,
    use_platform_credentials: bool = False,
):
    from api.services.platform_keys import get_platform_master_key

    provider = incoming.get("provider")
    has_master_key = bool(
        provider and service_name and get_platform_master_key(service_name, str(provider))
    )

    if (
        not isinstance(existing, dict)
        or (
            incoming.get("provider") is not None
            and existing.get("provider") is not None
            and incoming.get("provider") != existing.get("provider")
        )
    ):
        # Provider changed or no existing config for this service
        for secret_field in SERVICE_SECRET_FIELDS:
            incoming_secret = incoming.get(secret_field)
            if contains_masked_key(incoming_secret):
                if use_platform_credentials or has_master_key:
                    incoming[secret_field] = ""
        return

    for secret_field in SERVICE_SECRET_FIELDS:
        if secret_field not in existing:
            incoming_secret = incoming.get(secret_field)
            if contains_masked_key(incoming_secret) and (use_platform_credentials or has_master_key):
                incoming[secret_field] = ""
            continue
        incoming_secret = incoming.get(secret_field)
        existing_secret = existing[secret_field]
        if incoming_secret is None:
            incoming[secret_field] = existing_secret
        elif contains_masked_key(incoming_secret):
            resolved = resolve_masked_api_keys(
                incoming_secret,
                existing_secret,
            )
            if contains_masked_key(resolved) and (use_platform_credentials or has_master_key):
                incoming[secret_field] = ""
            else:
                incoming[secret_field] = resolved


def _raise_if_masked_secret(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SERVICE_SECRET_FIELDS and contains_masked_key(nested):
                raise ValueError(
                    f"The {key} appears to be masked. Please provide the actual "
                    "value, not the masked value."
                )
            _raise_if_masked_secret(nested)
    elif isinstance(value, list):
        for item in value:
            _raise_if_masked_secret(item)


def _mask_secret_fields(value):
    if isinstance(value, dict):
        for key, nested in list(value.items()):
            if key in SERVICE_SECRET_FIELDS and nested:
                value[key] = _mask_secret_value(nested)
            else:
                _mask_secret_fields(nested)
    elif isinstance(value, list):
        for item in value:
            _mask_secret_fields(item)


def _mask_secret_value(value):
    if isinstance(value, list):
        return [mask_key(item) for item in value]
    return mask_key(value)


def _convert_any_dograh_legacy_configuration(
    configuration: EffectiveAIModelConfiguration,
    dograh_key: str,
) -> OrganizationAIModelConfigurationV2:
    speed = getattr(configuration.tts, "speed", 1.0)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    if not DOGRAH_SPEED_MIN <= speed <= DOGRAH_SPEED_MAX:
        speed = 1.0
    return OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(
            api_key=dograh_key,
            temperature=getattr(configuration.llm, "temperature", None),
            voice=getattr(configuration.tts, "voice", DOGRAH_DEFAULT_VOICE)
            or DOGRAH_DEFAULT_VOICE,
            speed=speed,
            language=getattr(configuration.stt, "language", DOGRAH_DEFAULT_LANGUAGE)
            or DOGRAH_DEFAULT_LANGUAGE,
        ),
    )


def _first_dograh_api_key(configuration: EffectiveAIModelConfiguration) -> str | None:
    for service in (
        configuration.llm,
        configuration.tts,
        configuration.stt,
        configuration.embeddings,
        configuration.realtime,
    ):
        if service is None or _provider(service) != ServiceProviders.DOGRAH:
            continue
        try:
            return _single_api_key(service)
        except ValueError:
            continue
    return None


def _provider(service):
    return getattr(service, "provider", None)


def _single_api_key(service) -> str:
    if hasattr(service, "get_all_api_keys"):
        keys = service.get_all_api_keys()
        if len(keys) != 1:
            raise ValueError("Expected exactly one API key")
        return keys[0]
    key = getattr(service, "api_key", None)
    if not key:
        raise ValueError("Expected an API key")
    return key

