"""Platform master keys cache and resolution service.

Maintains an in-memory cache of master provider keys configured by Superadmins in
the database, allowing synchronous resolution during Pipecat service construction
without async DB query overhead or extra proxy network latency.
"""

from typing import Any, Dict, Optional, Tuple
from loguru import logger

# Structure: {service_type: {provider: {"api_key": str, "is_default": bool, "pricing": dict}}}
_MASTER_KEYS_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {
    "llm": {},
    "stt": {},
    "tts": {},
}


async def refresh_master_keys_cache() -> None:
    """Load or reload all active platform master keys from the database into memory."""
    from api.db import db_client

    try:
        keys = await db_client.list_master_keys()
        new_cache: Dict[str, Dict[str, Dict[str, Any]]] = {
            "llm": {},
            "stt": {},
            "tts": {},
        }
        for k in keys:
            if not k.is_active:
                continue
            st = k.service_type.lower()
            prov = k.provider.lower()
            if st not in new_cache:
                new_cache[st] = {}
            new_cache[st][prov] = {
                "api_key": k.api_key,
                "is_default": k.is_default,
                "default_model": getattr(k, "default_model", None),
                "default_voice": getattr(k, "default_voice", None),
                "models_pricing": k.models_pricing or {},
            }

        global _MASTER_KEYS_CACHE
        _MASTER_KEYS_CACHE = new_cache
        logger.info("Refreshed platform master keys cache: {} keys loaded", len(keys))
    except Exception as e:
        logger.warning("Failed to refresh platform master keys cache: {}", e)


def get_platform_master_key(service_type: str, provider: str) -> Optional[str]:
    """Resolve an API key for a specific provider from the platform master keys cache."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry and entry.get("api_key"):
        return entry["api_key"]
    return None


def get_platform_default_model(service_type: str, provider: str) -> Optional[str]:
    """Get the default model configured for a specific platform provider."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry:
        return entry.get("default_model")
    return None


def get_platform_default_voice(service_type: str, provider: str) -> Optional[str]:
    """Get the default voice configured for a specific platform provider."""
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov)
    if entry:
        return entry.get("default_voice")
    return None


def get_default_platform_provider(service_type: str) -> Optional[Tuple[str, str]]:
    """Return (provider, api_key) for the marked default master provider of a service type."""
    st = service_type.lower()
    providers_map = _MASTER_KEYS_CACHE.get(st, {})
    for prov, data in providers_map.items():
        if data.get("is_default") and data.get("api_key"):
            return prov, data["api_key"]
    # If no default explicitly marked, return the first available active provider
    if providers_map:
        first_prov = next(iter(providers_map))
        return first_prov, providers_map[first_prov]["api_key"]
    return None


def get_default_platform_provider_and_model(
    service_type: str,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Return (provider, api_key, default_model) for the marked default master provider of a service type."""
    st = service_type.lower()
    providers_map = _MASTER_KEYS_CACHE.get(st, {})
    for prov, data in providers_map.items():
        if data.get("is_default") and data.get("api_key"):
            return prov, data["api_key"], data.get("default_model")
    if providers_map:
        first_prov = next(iter(providers_map))
        return (
            first_prov,
            providers_map[first_prov]["api_key"],
            providers_map[first_prov].get("default_model"),
        )
    return None


def get_default_platform_provider_and_settings(
    service_type: str,
) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """Return (provider, api_key, default_model, default_voice) for the marked default master provider."""
    st = service_type.lower()
    providers_map = _MASTER_KEYS_CACHE.get(st, {})
    for prov, data in providers_map.items():
        if data.get("is_default") and data.get("api_key"):
            return (
                prov,
                data["api_key"],
                data.get("default_model"),
                data.get("default_voice"),
            )
    if providers_map:
        first_prov = next(iter(providers_map))
        return (
            first_prov,
            providers_map[first_prov]["api_key"],
            providers_map[first_prov].get("default_model"),
            providers_map[first_prov].get("default_voice"),
        )
    return None


def get_all_platform_defaults() -> dict:
    """Return dictionary of current default provider, model, voice across services."""
    res = {}
    for st in ("tts", "llm", "stt"):
        providers_map = _MASTER_KEYS_CACHE.get(st, {})
        def_prov = None
        def_model = None
        def_voice = None
        for prov, data in providers_map.items():
            if data.get("is_default") and data.get("api_key"):
                def_prov = prov
                def_model = data.get("default_model")
                def_voice = data.get("default_voice")
                break
        if not def_prov and providers_map:
            first_prov = next(iter(providers_map))
            def_prov = first_prov
            def_model = providers_map[first_prov].get("default_model")
            def_voice = providers_map[first_prov].get("default_voice")
        res[st] = {
            "provider": def_prov,
            "default_model": def_model,
            "default_voice": def_voice,
        }
    return res


def get_model_rate_per_minute(service_type: str, provider: str, model: str) -> float:
    """Get the configured rate per minute for a model, or return a sensible default."""
    defaults = {
        "llm": 0.01,
        "stt": 0.01,
        "tts": 0.02,
    }
    st = service_type.lower()
    prov = provider.lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov, {})
    pricing = entry.get("models_pricing", {})

    # Check model-specific rate
    if model in pricing:
        val = pricing[model]
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict) and "price_per_minute_usd" in val:
            return float(val["price_per_minute_usd"])

    # Fallback to provider default if defined
    if "default" in pricing:
        val = pricing["default"]
        if isinstance(val, (int, float)):
            return float(val)

    return defaults.get(st, 0.01)


def calculate_run_composite_rate(
    llm_provider: str = "",
    llm_model: str = "",
    stt_provider: str = "",
    stt_model: str = "",
    tts_provider: str = "",
    tts_model: str = "",
    telephony_rate: float = 0.02,
    is_platform_telephony: bool = True,
    is_byok_llm: bool = False,
    is_byok_stt: bool = False,
    is_byok_tts: bool = False,
    byok_platform_fee_per_minute: float = 0.04,
) -> dict:
    """Calculate the composite rate per minute from model-specific pricing, BYOK platform fees, and telephony.

    Rules:
    - If is_platform_telephony is False (BYOT): telephony usage rate is $0.00/min.
    - If is_byok is True for a model service: that model's platform rate is $0.00/min (user pays provider directly).
    - If any BYOK service is used: a platform orchestration fee (byok_platform_fee_per_minute) is charged.
    """
    raw_llm = get_model_rate_per_minute("llm", llm_provider, llm_model) if llm_provider else 0.015
    llm_rate = 0.0 if is_byok_llm else raw_llm

    raw_stt = get_model_rate_per_minute("stt", stt_provider, stt_model) if stt_provider else 0.005
    stt_rate = 0.0 if is_byok_stt else raw_stt

    raw_tts = get_model_rate_per_minute("tts", tts_provider, tts_model) if tts_provider else 0.020
    tts_rate = 0.0 if is_byok_tts else raw_tts

    has_any_byok = is_byok_llm or is_byok_stt or is_byok_tts
    byok_fee = byok_platform_fee_per_minute if has_any_byok else 0.0

    effective_telephony_rate = telephony_rate if is_platform_telephony else 0.0

    composite_rate = round(llm_rate + stt_rate + tts_rate + effective_telephony_rate + byok_fee, 4)
    return {
        "rate_per_minute": composite_rate,
        "llm_rate": llm_rate,
        "stt_rate": stt_rate,
        "tts_rate": tts_rate,
        "telephony_rate": effective_telephony_rate,
        "byok_fee": byok_fee,
        "is_platform_telephony": is_platform_telephony,
        "is_byok": has_any_byok,
    }


def get_llm_token_rates(provider: str = "", model: str = "") -> dict:
    """Get per-million and per-thousand token pricing for an LLM model from platform master keys pricing cache."""
    st = "llm"
    prov = (provider or "").lower()
    model_name = (model or "").lower()
    entry = _MASTER_KEYS_CACHE.get(st, {}).get(prov, {})
    pricing = entry.get("models_pricing", {})

    # Check for direct or case-insensitive model match
    val = None
    if model in pricing:
        val = pricing[model]
    elif model_name in pricing:
        val = pricing[model_name]
    else:
        for k, v in pricing.items():
            if k.lower() == model_name:
                val = v
                break

    if val is None and "default" in pricing:
        val = pricing["default"]

    # Known standard fallback rates (price in USD per 1M tokens)
    default_prompt_per_1m = 0.50
    default_completion_per_1m = 1.50
    if "4o-mini" in model_name:
        default_prompt_per_1m = 0.15
        default_completion_per_1m = 0.60
    elif "4o" in model_name:
        default_prompt_per_1m = 2.50
        default_completion_per_1m = 10.00
    elif "flash" in model_name:
        default_prompt_per_1m = 0.10
        default_completion_per_1m = 0.40
    elif "claude-3-5-sonnet" in model_name:
        default_prompt_per_1m = 3.00
        default_completion_per_1m = 15.00
    elif "haiku" in model_name:
        default_prompt_per_1m = 0.25
        default_completion_per_1m = 1.25

    if isinstance(val, dict):
        p_rate = (
            val.get("prompt_rate_per_1m")
            or val.get("input_rate_per_1m")
            or val.get("prompt")
            or val.get("input")
        )
        c_rate = (
            val.get("completion_rate_per_1m")
            or val.get("output_rate_per_1m")
            or val.get("completion")
            or val.get("output")
        )
        if val.get("prompt_rate_per_1k") is not None:
            p_rate = float(val["prompt_rate_per_1k"]) * 1000.0
        if val.get("completion_rate_per_1k") is not None:
            c_rate = float(val["completion_rate_per_1k"]) * 1000.0
        if val.get("price_per_1k_tokens") is not None:
            p_rate = float(val["price_per_1k_tokens"]) * 1000.0
            c_rate = float(val["price_per_1k_tokens"]) * 2000.0

        prompt_per_1m = float(p_rate) if p_rate is not None else default_prompt_per_1m
        completion_per_1m = float(c_rate) if c_rate is not None else default_completion_per_1m
    elif isinstance(val, (int, float)) and float(val) > 0:
        rate_val = float(val)
        # If rate_val < 0.05 (e.g. 0.01 per 1K tokens):
        if rate_val < 0.05:
            prompt_per_1m = rate_val * 1000.0
            completion_per_1m = rate_val * 2000.0
        else:
            prompt_per_1m = rate_val
            completion_per_1m = rate_val * 3.0
    else:
        prompt_per_1m = default_prompt_per_1m
        completion_per_1m = default_completion_per_1m

    return {
        "prompt_rate_per_1m": round(prompt_per_1m, 6),
        "completion_rate_per_1m": round(completion_per_1m, 6),
        "prompt_rate_per_1k": round(prompt_per_1m / 1000.0, 6),
        "completion_rate_per_1k": round(completion_per_1m / 1000.0, 6),
    }


def calculate_llm_token_cost(
    provider: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    is_byok_llm: bool = False,
) -> dict:
    """Calculate the USD cost for LLM token consumption."""
    if is_byok_llm:
        return {
            "total_llm_cost_usd": 0.0,
            "prompt_cost_usd": 0.0,
            "completion_cost_usd": 0.0,
            "cached_discount_usd": 0.0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "rates": {},
            "is_byok": True,
        }

    rates = get_llm_token_rates(provider=provider, model=model)
    prompt_rate = rates["prompt_rate_per_1m"]
    completion_rate = rates["completion_rate_per_1m"]

    uncached_prompt = max(prompt_tokens - cached_tokens, 0)
    prompt_cost = (uncached_prompt / 1_000_000.0) * prompt_rate
    cached_cost = (cached_tokens / 1_000_000.0) * (prompt_rate * 0.5)
    completion_cost = (completion_tokens / 1_000_000.0) * completion_rate

    total_cost = round(prompt_cost + cached_cost + completion_cost, 6)

    return {
        "total_llm_cost_usd": total_cost,
        "prompt_cost_usd": round(prompt_cost + cached_cost, 6),
        "completion_cost_usd": round(completion_cost, 6),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "rates": rates,
        "is_byok": False,
    }

