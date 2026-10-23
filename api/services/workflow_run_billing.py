"""Workflow-run billing hooks.

Dograh does not rate or deduct credits locally. MPS owns credit accounting.
For hosted deployments, Dograh reports completed platform usage to MPS.
When a server-minted MPS correlation id exists, MPS uses model-service usage
as the canonical duration. Otherwise Dograh reports the completed run duration.
"""

from typing import Any

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.managed_model_services import get_mps_correlation_id
from api.services.mps_service_key_client import mps_service_key_client


def _workflow_run_organization_id(workflow_run) -> int | None:
    workflow = getattr(workflow_run, "workflow", None)
    return getattr(workflow, "organization_id", None)


def _duration_seconds_from_usage_info(workflow_run) -> float | None:
    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    duration = usage_info.get("call_duration_seconds")
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        return None

    return duration_seconds if duration_seconds > 0 else None


def _is_usage_not_ready_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 409:
        return False
    return "usage_not_ready" in (getattr(response, "text", "") or "")


async def _report_text_chat_platform_usage(workflow_run) -> None:
    """Calculate token-based LLM cost + platform service charge and deduct from plan credits and wallet."""
    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping text chat billing report for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    usage_info = getattr(workflow_run, "usage_info", None) or {}
    duration_seconds = _duration_seconds_from_usage_info(workflow_run) or 0.0

    # 1. Resolve workflow configurations to find configured LLM and BYOK status
    workflow = getattr(workflow_run, "workflow", None)
    workflow_config = {}
    definition = None
    try:
        definition = getattr(workflow_run, "definition", None)
    except Exception:
        pass

    if not definition and getattr(workflow_run, "definition_id", None):
        try:
            definition = await db_client.get_workflow_definition_by_id(
                workflow_run.definition_id
            )
        except Exception:
            pass

    if definition and getattr(definition, "workflow_configurations", None):
        workflow_config = definition.workflow_configurations
    elif workflow and getattr(workflow, "workflow_configurations", None):
        workflow_config = workflow.workflow_configurations

    model_overrides = (workflow_config or {}).get("model_overrides") or {}
    llm_info = model_overrides.get("llm") or {}

    if not llm_info.get("provider"):
        try:
            from api.services.configuration.ai_model_configuration import (
                get_organization_ai_model_configuration_v2,
            )
            org_config = await get_organization_ai_model_configuration_v2(organization_id)
            if org_config and getattr(org_config, "byok", None) and getattr(org_config.byok, "pipeline", None):
                pipe = org_config.byok.pipeline
                if getattr(pipe, "llm", None):
                    llm_info = {
                        "provider": getattr(pipe.llm, "provider", ""),
                        "model": getattr(pipe.llm, "model", ""),
                    }
        except Exception as e:
            logger.warning("Could not load organization global model config for text chat billing: {}", e)

    is_byok_run = False
    try:
        from api.services.configuration.ai_model_configuration import (
            get_effective_ai_model_configuration_for_workflow,
            uses_managed_model_services_v2,
        )
        user_ai_config = await get_effective_ai_model_configuration_for_workflow(
            organization_id=organization_id,
            workflow_configurations=workflow_config,
        )
        is_byok_run = not uses_managed_model_services_v2(user_ai_config)
    except Exception:
        pass

    # 2. Sum tokens from usage_info["llm"]
    llm_usage = usage_info.get("llm") or {}
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cached_tokens = 0
    total_tokens = 0

    from api.services.platform_keys import calculate_llm_token_cost

    llm_costs = []
    default_provider = llm_info.get("provider") or "openai"
    default_model = llm_info.get("model") or "gpt-4o-mini"

    if isinstance(llm_usage, dict) and llm_usage:
        for key, entry in llm_usage.items():
            if not isinstance(entry, dict):
                continue
            p_tok = int(entry.get("prompt_tokens") or 0)
            c_tok = int(entry.get("completion_tokens") or 0)
            cache_tok = int(entry.get("cache_read_input_tokens") or 0)
            t_tok = int(entry.get("total_tokens") or (p_tok + c_tok))

            total_prompt_tokens += p_tok
            total_completion_tokens += c_tok
            total_cached_tokens += cache_tok
            total_tokens += t_tok

            model_name = key.split("|||")[-1] if "|||" in key else default_model
            provider_name = default_provider
            if "gemini" in model_name.lower():
                provider_name = "google"
            elif "claude" in model_name.lower():
                provider_name = "anthropic"
            elif "gpt" in model_name.lower():
                provider_name = "openai"

            cost_res = calculate_llm_token_cost(
                provider=provider_name,
                model=model_name,
                prompt_tokens=p_tok,
                completion_tokens=c_tok,
                cached_tokens=cache_tok,
                is_byok_llm=is_byok_run,
            )
            llm_costs.append(cost_res)
    else:
        cost_res = calculate_llm_token_cost(
            provider=default_provider,
            model=default_model,
            prompt_tokens=0,
            completion_tokens=0,
            is_byok_llm=is_byok_run,
        )
        llm_costs.append(cost_res)

    total_llm_cost_usd = sum(c["total_llm_cost_usd"] for c in llm_costs)

    # 3. Calculate Platform Service Charge
    from api.services.plan_service import plan_service
    limits = await plan_service.get_effective_limits(organization_id)
    byok_fee_rate = limits.byok_platform_fee_per_minute_usd or 0.04

    turns_count = 1
    try:
        text_session = await db_client.get_workflow_run_text_session(
            workflow_run.id, organization_id=organization_id
        )
        if text_session and text_session.session_data:
            turns = text_session.session_data.get("turns") or []
            turns_count = max(len(turns), 1)
    except Exception:
        pass

    duration_minutes = duration_seconds / 60.0

    if is_byok_run:
        # BYOK: Platform orchestration fee for routing, tools, and session state
        platform_fee_usd = round(
            max(0.005 * turns_count, duration_minutes * byok_fee_rate), 4
        )
    else:
        # Managed Key: Platform fee + LLM base cost
        base_fee = duration_minutes * (byok_fee_rate * 0.5)
        platform_fee_usd = round(max(0.002 * turns_count, base_fee), 4)

    total_cost_usd = round(total_llm_cost_usd + platform_fee_usd, 4)

    # 4. Multi-tier credit-based plan accounting (Plan credits first, wallet second)
    billing_result = await plan_service.record_text_chat_billing(
        organization_id=organization_id,
        total_cost_usd=total_cost_usd,
        duration_seconds=duration_seconds,
        total_tokens=total_tokens,
    )

    cost_info = getattr(workflow_run, "cost_info", None) or {}
    cost_info.update(
        {
            "total_cost_usd": total_cost_usd,
            "charge_usd": billing_result.get("wallet_cost_usd", 0.0),
            "llm_cost_usd": round(total_llm_cost_usd, 4),
            "platform_fee_usd": platform_fee_usd,
            "call_duration_seconds": duration_seconds,
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "rates": {
                "llm_provider": default_provider,
                "llm_model": default_model,
                "platform_fee_usd": platform_fee_usd,
                "is_byok": is_byok_run,
                "turns_count": turns_count,
            },
            "charged_to": billing_result.get("charged_to", "wallet"),
            "plan_credits_deducted": billing_result.get("plan_credits_deducted", 0.0),
            "plan_credits_remaining": billing_result.get("plan_credits_remaining", 0.0),
            "wallet_cost_usd": billing_result.get("wallet_cost_usd", 0.0),
            "wallet_balance_after": billing_result.get("wallet_balance_after", 0.0),
            "effective_tier": billing_result.get("effective_tier", "pay_as_you_go"),
        }
    )

    if hasattr(db_client, "update_workflow_run"):
        await db_client.update_workflow_run(
            workflow_run.id, cost_info=cost_info
        )

    logger.info(
        "Text chat usage recorded for run {} (tokens: {}, LLM: ${}, Platform: ${}, Total: ${}, charged to {}): plan_credits_deducted={}, wallet_charge=${}, balance_after={}",
        workflow_run.id,
        total_tokens,
        round(total_llm_cost_usd, 4),
        platform_fee_usd,
        total_cost_usd,
        billing_result.get("charged_to", "wallet"),
        billing_result.get("plan_credits_deducted", 0.0),
        billing_result.get("wallet_cost_usd", 0.0),
        billing_result.get("wallet_balance_after", 0.0),
    )


async def report_workflow_run_platform_usage(workflow_run) -> None:
    """Report hosted platform usage for a completed workflow run and deduct platform credits."""
    if getattr(workflow_run, "mode", None) == WorkflowRunMode.TEXTCHAT.value:
        try:
            await _report_text_chat_platform_usage(workflow_run)
        except Exception as e:
            logger.error("Failed to bill text chat workflow run {}: {}", workflow_run.id, e)
        return


    if not getattr(workflow_run, "is_completed", False):
        logger.warning(
            "Workflow run is not completed in report_workflow_run_platform_usage"
        )
        return

    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    duration_seconds = _duration_seconds_from_usage_info(workflow_run)

    # 1. Platform wallet deduction and cost_info breakdown
    if duration_seconds and duration_seconds > 0:
        try:
            workflow = getattr(workflow_run, "workflow", None)
            custom_price_per_sec = getattr(workflow, "price_per_second", None) if workflow else None

            if custom_price_per_sec and float(custom_price_per_sec) > 0:
                cost_usd = round(float(custom_price_per_sec) * duration_seconds, 4)
                rate_per_min = round(float(custom_price_per_sec) * 60.0, 4)
                rates = {
                    "rate_per_minute": rate_per_min,
                    "custom_price_per_second": float(custom_price_per_sec),
                }
            else:
                workflow_config = {}
                definition = None
                try:
                    definition = getattr(workflow_run, "definition", None)
                except Exception:
                    pass

                if not definition and getattr(workflow_run, "definition_id", None):
                    try:
                        definition = await db_client.get_workflow_definition_by_id(
                            workflow_run.definition_id
                        )
                    except Exception:
                        pass

                if definition and getattr(definition, "workflow_configurations", None):
                    workflow_config = definition.workflow_configurations
                elif workflow and getattr(workflow, "workflow_configurations", None):
                    workflow_config = workflow.workflow_configurations

                model_overrides = (workflow_config or {}).get("model_overrides") or {}
                llm_info = model_overrides.get("llm") or {}
                stt_info = model_overrides.get("stt") or {}
                tts_info = model_overrides.get("tts") or {}

                if not (llm_info.get("provider") or stt_info.get("provider") or tts_info.get("provider")):
                    try:
                        from api.services.configuration.ai_model_configuration import (
                            get_organization_ai_model_configuration_v2,
                        )
                        org_config = await get_organization_ai_model_configuration_v2(organization_id)
                        if org_config and getattr(org_config, "byok", None) and getattr(org_config.byok, "pipeline", None):
                            pipe = org_config.byok.pipeline
                            if getattr(pipe, "llm", None):
                                llm_info = {"provider": getattr(pipe.llm, "provider", ""), "model": getattr(pipe.llm, "model", "")}
                            if getattr(pipe, "stt", None):
                                stt_info = {"provider": getattr(pipe.stt, "provider", ""), "model": getattr(pipe.stt, "model", "")}
                            if getattr(pipe, "tts", None):
                                tts_info = {"provider": getattr(pipe.tts, "provider", ""), "model": getattr(pipe.tts, "model", "")}
                    except Exception as e:
                        logger.warning("Could not load organization global model config for billing: {}", e)

                is_platform_telephony = True
                telephony_cfg_id = None
                init_ctx = getattr(workflow_run, "initial_context", None) or {}
                extra_ctx = getattr(workflow_run, "extra", None) or {}
                if isinstance(init_ctx, dict) and "telephony_configuration_id" in init_ctx:
                    telephony_cfg_id = init_ctx.get("telephony_configuration_id")
                elif isinstance(extra_ctx, dict) and "telephony_configuration_id" in extra_ctx:
                    telephony_cfg_id = extra_ctx.get("telephony_configuration_id")

                if telephony_cfg_id:
                    try:
                        telephony_cfg = await db_client.get_telephony_configuration(int(telephony_cfg_id))
                        if telephony_cfg:
                            is_platform_telephony = bool(getattr(telephony_cfg, "is_platform_inventory", False))
                    except Exception as e:
                        logger.debug("Could not determine telephony configuration inventory status: {}", e)

                from api.services.plan_service import plan_service
                limits = await plan_service.get_effective_limits(organization_id)

                is_byok_run = False
                try:
                    from api.services.configuration.ai_model_configuration import (
                        get_effective_ai_model_configuration_for_workflow,
                        uses_managed_model_services_v2,
                    )
                    user_ai_config = await get_effective_ai_model_configuration_for_workflow(
                        organization_id=organization_id,
                        workflow_configurations=workflow_config,
                    )
                    is_byok_run = not uses_managed_model_services_v2(user_ai_config)
                except Exception:
                    pass

                from api.services.platform_keys import calculate_run_composite_rate

                rates = calculate_run_composite_rate(
                    llm_provider=llm_info.get("provider", ""),
                    llm_model=llm_info.get("model", ""),
                    stt_provider=stt_info.get("provider", ""),
                    stt_model=stt_info.get("model", ""),
                    tts_provider=tts_info.get("provider", ""),
                    tts_model=tts_info.get("model", ""),
                    telephony_rate=0.02,
                    is_platform_telephony=is_platform_telephony,
                    is_byok_llm=is_byok_run,
                    is_byok_stt=is_byok_run,
                    is_byok_tts=is_byok_run,
                    byok_platform_fee_per_minute=limits.byok_platform_fee_per_minute_usd,
                )
                rate_per_min = rates["rate_per_minute"]
                cost_usd = round((duration_seconds / 60.0) * rate_per_min, 4)

            # Multi-tier credit-based plan accounting (Plan credits deduction first, wallet overflow second)
            from api.services.plan_service import plan_service
            billing_result = await plan_service.record_run_billing(
                organization_id=organization_id,
                duration_seconds=duration_seconds,
                rate_per_min=rate_per_min,
            )

            cost_info = getattr(workflow_run, "cost_info", None) or {}
            cost_info.update(
                {
                    "total_cost_usd": cost_usd,
                    "charge_usd": billing_result.get("wallet_cost_usd", 0.0),
                    "call_duration_seconds": duration_seconds,
                    "rate_per_minute": rate_per_min,
                    "rates": rates,
                    "charged_to": billing_result.get("charged_to", "wallet"),
                    "plan_credits_deducted": billing_result.get("plan_credits_deducted", 0.0),
                    "plan_credits_remaining": billing_result.get("plan_credits_remaining", 0.0),
                    "wallet_cost_usd": billing_result.get("wallet_cost_usd", 0.0),
                    "effective_tier": billing_result.get("effective_tier", "pay_as_you_go"),
                    "wallet_balance_after": billing_result.get("wallet_balance_after", 0.0),
                }
            )

            if hasattr(db_client, "update_workflow_run"):
                await db_client.update_workflow_run(
                    workflow_run.id, cost_info=cost_info
                )

            logger.info(
                "Usage recorded for run {} ({}s at ${}/min, charged to {}): plan_credits_deducted={}, wallet_charge=${}, balance_after={}",
                workflow_run.id,
                duration_seconds,
                rate_per_min,
                billing_result.get("charged_to", "wallet"),
                billing_result.get("plan_credits_deducted", 0.0),
                billing_result.get("wallet_cost_usd", 0.0),
                billing_result.get("wallet_balance_after", 0.0),
            )
        except Exception as e:
            logger.warning(
                "Failed to record platform wallet deduction for run {}: {}",
                workflow_run.id,
                e,
            )

    # 2. Report hosted platform usage to MPS if configured
    if DEPLOYMENT_MODE == "oss":
        return

    correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    report_duration = (
        None if correlation_id else duration_seconds
    )
    if not correlation_id and report_duration is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no billable duration",
            workflow_run.id,
        )
        return

    try:
        result = await mps_service_key_client.report_platform_usage(
            organization_id=organization_id,
            correlation_id=correlation_id,
            duration_seconds=report_duration,
            workflow_run_id=workflow_run.id,
            metadata={
                "source": "workflow_run_completion",
                "workflow_id": getattr(workflow_run, "workflow_id", None),
                "duration_source": (
                    "mps_correlation" if correlation_id else "dograh_usage_info"
                ),
            },
        )
        logger.info(
            "Reported platform usage for workflow run {} to MPS: {}",
            workflow_run.id,
            result,
        )
    except Exception as e:
        if _is_usage_not_ready_error(e):
            # A run can start and receive an MPS correlation id, then fail or end
            # before billable STT usage is recorded. MPS returns usage_not_ready
            # for that no-platform-fee path, so keep it out of error alerts.
            logger.warning(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )
        else:
            logger.error(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )
            raise


async def report_completed_workflow_run_platform_usage(workflow_run_id: int) -> None:
    """Load a completed workflow run and report platform usage to MPS."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Skipping platform usage report: workflow run {} not found",
            workflow_run_id,
        )
        return

    await report_workflow_run_platform_usage(workflow_run)
