"""Direct provider voice catalog service.

Provides independent, direct voice fetching from all platform TTS providers:
1. Cartesia
2. ElevenLabs
3. Sarvam AI
4. Deepgram
5. OpenAI
6. Smallest AI
7. Microsoft Azure Speech
8. Google Cloud / Vertex AI
9. Speechify
10. Rime
11. LMNT

Resolution Hierarchy:
1. Organization BYOK (Bring Your Own Key) when configured by the user/organization.
2. Platform Master Keys (managed by Superadmin in platform_master_keys).
3. Environment variables as fallback.
4. Rich offline built-in fallback catalog to guarantee 100% uptime with ZERO
   external Dograh MPS (services.dograh.com) dependency.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
import httpx
from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.platform_keys import (
    get_platform_master_key,
    get_default_platform_provider_and_model,
)

HTTP_TIMEOUT = float(os.getenv("VOICE_CATALOG_TIMEOUT", "8.0"))


# ==============================================================================
# 1. CREDENTIAL RESOLUTION (BYOK -> Master Key -> Env -> None)
# ==============================================================================


async def resolve_tts_credentials(
    provider: str,
    organization_id: Optional[int] = None,
) -> Tuple[Optional[str], str]:
    """
    Resolve the active API key for a TTS provider.

    Resolution hierarchy:
    1. Organization BYOK: If org has BYOK mode enabled and configured for this provider.
    2. Platform Master Key: Superadmin configured master key from DB.
    3. Environment Variables: CARTESIA_API_KEY, ELEVENLABS_API_KEY, etc.
    4. None: Fallback to built-in curated library.

    Returns:
        Tuple of (api_key, key_source) where key_source is 'byok', 'platform_master', 'env', or 'none'.
    """
    norm_provider = provider.lower().strip()
    if norm_provider == "azure_speech":
        norm_provider = "azure"

    # 1. Check Organization BYOK if organization_id is provided
    if organization_id:
        try:
            org_row = await db_client.get_configuration(
                organization_id,
                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
            )
            if org_row and org_row.value:
                conf = org_row.value
                if isinstance(conf, dict) and conf.get("mode") == "byok":
                    byok_data = conf.get("byok", {})

                    # Check pipeline TTS
                    pipeline_tts = byok_data.get("pipeline", {}).get("tts", {})
                    p_prov = (pipeline_tts.get("provider") or "").lower()
                    if p_prov == "azure_speech":
                        p_prov = "azure"

                    if pipeline_tts and p_prov == norm_provider and pipeline_tts.get("api_key"):
                        byok_key = pipeline_tts["api_key"]
                        if not byok_key.startswith("sk-...") and "***" not in byok_key:
                            logger.info(
                                "Using organization {} BYOK key for provider {}",
                                organization_id,
                                norm_provider,
                            )
                            return byok_key, "byok"

                    # Check realtime TTS
                    realtime_data = byok_data.get("realtime", {}).get("realtime", {})
                    r_prov = (realtime_data.get("provider") or "").lower()
                    if r_prov == "azure_speech":
                        r_prov = "azure"

                    if realtime_data and r_prov == norm_provider and realtime_data.get("api_key"):
                        byok_key = realtime_data["api_key"]
                        if not byok_key.startswith("sk-...") and "***" not in byok_key:
                            logger.info(
                                "Using organization {} BYOK realtime key for provider {}",
                                organization_id,
                                norm_provider,
                            )
                            return byok_key, "byok"
        except Exception as e:
            logger.debug(
                "Error checking BYOK credentials for org {}: {}", organization_id, e
            )

    # 2. Check Platform Master Keys
    try:
        master_key = get_platform_master_key("tts", norm_provider)
        if not master_key and norm_provider == "azure":
            master_key = get_platform_master_key("tts", "azure_speech")

        if master_key and master_key.strip():
            logger.debug("Using Platform Master Key for TTS provider {}", norm_provider)
            return master_key.strip(), "platform_master"
    except Exception as e:
        logger.debug("Error checking master key for provider {}: {}", norm_provider, e)

    # 3. Check Environment Variables
    env_var_map = {
        "cartesia": ["CARTESIA_API_KEY"],
        "elevenlabs": ["ELEVENLABS_API_KEY", "XI_API_KEY"],
        "deepgram": ["DEEPGRAM_API_KEY"],
        "sarvam": ["SARVAM_API_KEY", "SARVAMAI_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "smallest": ["SMALLEST_API_KEY", "WAVES_API_KEY"],
        "azure": ["AZURE_SPEECH_KEY", "AZURE_SPEECH_API_KEY", "AZURE_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "speechify": ["SPEECHIFY_API_KEY"],
        "rime": ["RIME_API_KEY"],
        "lmnt": ["LMNT_API_KEY"],
    }
    candidates = env_var_map.get(norm_provider, [f"{norm_provider.upper()}_API_KEY"])
    for var_name in candidates:
        val = os.getenv(var_name)
        if val and val.strip():
            logger.debug("Using environment key {} for provider {}", var_name, norm_provider)
            return val.strip(), "env"

    return None, "none"


# ==============================================================================
# 2. DIRECT PROVIDER FETCHERS
# ==============================================================================


async def fetch_cartesia_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    """Fetch live voices directly from Cartesia REST API."""
    if not api_key:
        return get_cartesia_builtin_catalog()

    headers = {
        "X-API-Key": api_key,
        "Cartesia-Version": "2024-06-10",
        "Accept": "application/json",
    }
    url = "https://api.cartesia.ai/voices"

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get("voices", [])

                normalized: List[Dict[str, Any]] = []
                for v in items:
                    if not isinstance(v, dict):
                        continue
                    v_id = v.get("id") or v.get("voice_id")
                    if not v_id:
                        continue

                    name = v.get("name") or v_id
                    description = v.get("description") or "Cartesia Sonic Voice"
                    lang = v.get("language") or "en"
                    gender = v.get("gender") or "neutral"

                    normalized.append(
                        {
                            "voice_id": str(v_id),
                            "name": str(name),
                            "description": str(description),
                            "language": str(lang).lower(),
                            "gender": str(gender).lower(),
                            "accent": v.get("accent") or "neutral",
                            "preview_url": v.get("preview_url")
                            or f"https://api.cartesia.ai/voices/{v_id}/preview",
                        }
                    )
                if normalized:
                    return normalized
    except Exception as e:
        logger.warning("Cartesia direct fetch error: {}", e)

    return get_cartesia_builtin_catalog()


async def fetch_elevenlabs_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    """Fetch live voices directly from ElevenLabs REST API (includes custom cloned voices)."""
    if not api_key:
        return get_elevenlabs_builtin_catalog()

    headers = {
        "xi-api-key": api_key,
        "Accept": "application/json",
    }
    url = "https://api.elevenlabs.io/v1/voices"

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                voices_list = data.get("voices", [])

                normalized: List[Dict[str, Any]] = []
                for v in voices_list:
                    if not isinstance(v, dict):
                        continue
                    v_id = v.get("voice_id")
                    if not v_id:
                        continue

                    labels = v.get("labels") or {}
                    category = v.get("category") or "premade"
                    description = (
                        f"ElevenLabs {category.title()} voice"
                        if not labels.get("description")
                        else labels.get("description")
                    )

                    normalized.append(
                        {
                            "voice_id": str(v_id),
                            "name": str(v.get("name") or v_id),
                            "description": str(description),
                            "language": str(labels.get("language") or "en").lower(),
                            "gender": str(labels.get("gender") or "neutral").lower(),
                            "accent": str(labels.get("accent") or "american").lower(),
                            "preview_url": v.get("preview_url"),
                        }
                    )
                if normalized:
                    return normalized
    except Exception as e:
        logger.warning("ElevenLabs direct fetch error: {}", e)

    return get_elevenlabs_builtin_catalog()


async def fetch_sarvam_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    """Fetch or return Sarvam AI Indian regional voices catalog."""
    if api_key:
        try:
            url = "https://api.sarvam.ai/voices"
            headers = {"api-subscription-key": api_key, "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    voices_list = data if isinstance(data, list) else data.get("voices", [])
                    if voices_list:
                        normalized = []
                        for v in voices_list:
                            if not isinstance(v, dict):
                                continue
                            v_id = v.get("speaker") or v.get("voice_id") or v.get("name")
                            if v_id:
                                normalized.append({
                                    "voice_id": str(v_id).lower(),
                                    "name": f"{str(v_id).title()} (Sarvam AI)",
                                    "description": v.get("description") or f"Sarvam AI {v_id} voice",
                                    "language": v.get("language") or "hi-IN",
                                    "gender": v.get("gender") or "neutral",
                                    "accent": "indian",
                                    "preview_url": v.get("sample_audio_url"),
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("Sarvam API voice list call error: {}", e)

    return get_sarvam_builtin_catalog()


async def fetch_deepgram_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    return get_deepgram_builtin_catalog()


async def fetch_openai_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    return get_openai_builtin_catalog()


async def fetch_smallest_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    if api_key:
        try:
            url = "https://waves-api.smallest.ai/api/v1/lightning/get_voices"
            headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    voices_list = data if isinstance(data, list) else data.get("voices", [])
                    if voices_list:
                        normalized = []
                        for v in voices_list:
                            if not isinstance(v, dict):
                                continue
                            v_id = v.get("voice_id") or v.get("name")
                            if v_id:
                                normalized.append({
                                    "voice_id": str(v_id),
                                    "name": str(v.get("name") or v_id).title(),
                                    "description": v.get("description") or "Smallest Waves Voice",
                                    "language": str(v.get("language") or "en").lower(),
                                    "gender": str(v.get("gender") or "neutral").lower(),
                                    "accent": v.get("accent") or "neutral",
                                    "preview_url": v.get("preview_url") or v.get("sample_url"),
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("Smallest API voice list error: {}", e)

    return get_smallest_builtin_catalog()


async def fetch_azure_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    region = os.getenv("AZURE_SPEECH_REGION", "eastus")
    if api_key:
        try:
            url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
            headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    voices_list = resp.json()
                    if isinstance(voices_list, list) and voices_list:
                        normalized = []
                        for v in voices_list[:120]:
                            if not isinstance(v, dict):
                                continue
                            short_name = v.get("ShortName")
                            if short_name:
                                loc = v.get("Locale", "en-US")
                                accent = loc.split("-")[-1].lower() if "-" in loc else "neutral"
                                normalized.append({
                                    "voice_id": short_name,
                                    "name": v.get("LocalName") or v.get("DisplayName") or short_name,
                                    "description": f"Azure {v.get('VoiceType', 'Neural')} Voice ({loc})",
                                    "language": loc,
                                    "gender": (v.get("Gender") or "neutral").lower(),
                                    "accent": accent,
                                    "preview_url": None,
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("Azure Speech voice list error: {}", e)

    return get_azure_builtin_catalog()


async def fetch_google_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    return get_google_builtin_catalog()


async def fetch_speechify_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    if api_key:
        try:
            url = "https://api.sws.speechify.com/v1/voices"
            headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    voices_list = resp.json()
                    if isinstance(voices_list, list) and voices_list:
                        normalized = []
                        for v in voices_list:
                            if not isinstance(v, dict):
                                continue
                            v_id = v.get("id") or v.get("voice_id")
                            if v_id:
                                normalized.append({
                                    "voice_id": str(v_id),
                                    "name": str(v.get("display_name") or v.get("name") or v_id),
                                    "description": v.get("description") or "Speechify Natural Voice",
                                    "language": str(v.get("language") or "en").lower(),
                                    "gender": str(v.get("gender") or "neutral").lower(),
                                    "accent": v.get("accent") or "neutral",
                                    "preview_url": v.get("sample_url"),
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("Speechify voice list error: {}", e)

    return get_speechify_builtin_catalog()


async def fetch_rime_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    if api_key:
        try:
            url = "https://users.rime.ai/v1/voices"
            headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    voices_list = resp.json()
                    if isinstance(voices_list, list) and voices_list:
                        normalized = []
                        for v in voices_list:
                            if not isinstance(v, dict):
                                continue
                            v_id = v.get("speaker") or v.get("name") or v.get("id")
                            if v_id:
                                normalized.append({
                                    "voice_id": str(v_id),
                                    "name": str(v_id).title(),
                                    "description": "Rime Mist Neural Voice",
                                    "language": "en",
                                    "gender": (v.get("gender") or "neutral").lower(),
                                    "accent": "american",
                                    "preview_url": None,
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("Rime voice list error: {}", e)

    return get_rime_builtin_catalog()


async def fetch_lmnt_voices(api_key: Optional[str]) -> List[Dict[str, Any]]:
    if api_key:
        try:
            url = "https://api.lmnt.com/v1/ai/voice/list"
            headers = {"X-API-Key": api_key, "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    voices_list = resp.json()
                    if isinstance(voices_list, list) and voices_list:
                        normalized = []
                        for v in voices_list:
                            if not isinstance(v, dict):
                                continue
                            v_id = v.get("id") or v.get("name")
                            if v_id:
                                normalized.append({
                                    "voice_id": str(v_id),
                                    "name": str(v.get("name") or v_id),
                                    "description": v.get("description") or "LMNT Speech Voice",
                                    "language": "en",
                                    "gender": (v.get("gender") or "neutral").lower(),
                                    "accent": "american",
                                    "preview_url": None,
                                })
                        if normalized:
                            return normalized
        except Exception as e:
            logger.debug("LMNT voice list error: {}", e)

    return get_lmnt_builtin_catalog()


# ==============================================================================
# 3. BUILT-IN OFFLINE CATALOGS (Full Comprehensive Libraries)
# ==============================================================================


def get_cartesia_builtin_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "voice_id": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
            "name": "Sarah",
            "description": "Smooth, professional female voice ideal for customer conversations and outreach.",
            "language": "en",
            "gender": "female",
            "accent": "american",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/sarah.mp3",
        },
        {
            "voice_id": "a0e99841-438c-4a64-b679-ae501e7d6091",
            "name": "Barbershop Man",
            "description": "Deep, confident male voice with warm tonal resonance.",
            "language": "en",
            "gender": "male",
            "accent": "american",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/barbershop.mp3",
        },
        {
            "voice_id": "ee7ea9f8-c0c1-498c-9f79-f24e6bda777c",
            "name": "Classy British Lady",
            "description": "Polite and articulate British female voice for luxury and corporate support.",
            "language": "en",
            "gender": "female",
            "accent": "british",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/british_lady.mp3",
        },
        {
            "voice_id": "638efaaa-4d0c-442e-b701-3fae16ae29c0",
            "name": "Calm French Woman",
            "description": "Gentle, reassuring French accent female voice.",
            "language": "en",
            "gender": "female",
            "accent": "french",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/french_woman.mp3",
        },
        {
            "voice_id": "8488e3a2-9442-4957-8977-9d7a22eb825f",
            "name": "Friendly Hindi Specialist",
            "description": "Natural Indian English & Hindi conversational voice.",
            "language": "hi",
            "gender": "female",
            "accent": "indian",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/hindi_female.mp3",
        },
        {
            "voice_id": "2b568345-1d48-4047-b25f-7baccf842eb0",
            "name": "Customer Support Man",
            "description": "Friendly, reliable and helpful customer service voice.",
            "language": "en",
            "gender": "male",
            "accent": "american",
            "preview_url": "https://storage.googleapis.com/cartesia-voice-previews/support_man.mp3",
        },
        {
            "voice_id": "c45bc5ec-5968-4f0b-8d02-132d70732128",
            "name": "Helpful German Man",
            "description": "Precise and friendly German and English male speaker.",
            "language": "de",
            "gender": "male",
            "accent": "german",
            "preview_url": None,
        },
        {
            "voice_id": "156fb8d2-335b-4950-9cb3-a2d33fabfb77",
            "name": "Commercial Lady",
            "description": "Upbeat, energetic commercial speaker for promotional calls.",
            "language": "en",
            "gender": "female",
            "accent": "american",
            "preview_url": None,
        },
    ]


def get_elevenlabs_builtin_catalog() -> List[Dict[str, Any]]:
    """40+ standard official ElevenLabs voices."""
    items = [
        ("21m00Tcm4TlvDq8ikWAM", "Rachel", "female", "american", "Calm, friendly and warm young female voice, perfect for narration and sales."),
        ("AZnzlk1XvdvUeBnXmlld", "Domi", "female", "american", "Strong, clear and direct young female voice."),
        ("EXAVITQu4vr4xnSDxMaL", "Bella", "female", "american", "Bright, expressive and sweet female voice, ideal for conversational agents."),
        ("ErXwobaYiN019PkySvjV", "Antoni", "male", "american", "Well-rounded, pleasant and authentic male voice for versatile calls."),
        ("MF3mGyEYCl7XYWbV9V6O", "Elli", "female", "american", "Emotional, expressive female voice with clear enunciation."),
        ("TxGEqnHWrfWFTfGW9XjX", "Josh", "male", "american", "Natural, conversational and engaging young American male voice."),
        ("VR6AewLTigWG4xSOukaG", "Arnold", "male", "american", "Crisp, authoritative male voice with slight narrative cadence."),
        ("pNInz6obpgDQGcFmaJgB", "Adam", "male", "american", "Warm, deep and commanding male voice, excellent for professional outreach."),
        ("yoZ06aMxZJJ28mfd3POQ", "Sam", "male", "american", "Dynamic and adaptable male voice."),
        ("IKne3meq5aSn9XLyUdCD", "Charlie", "male", "australian", "Conversational, casual Australian male voice."),
        ("JBFqnCBsd6RMkjVDRZzb", "George", "male", "british", "Warm, sophisticated British male speaker."),
        ("N2lVS1w4EtoT3dr4eOWO", "Callum", "male", "transatlantic", "Intense, dramatic storytelling voice."),
        ("XB0fDUnXU5powFXDhCwa", "Charlotte", "female", "swedish", "Seductive and clear English voice with European flair."),
        ("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", "british", "Confident, well-spoken British corporate female voice."),
        ("bVMeCyTHy58xNoL34h3p", "Jeremy", "male", "american", "Excited, animated, youthful male voice."),
        ("cgSgspJ2msm6clMCkdW9", "Jessica", "female", "american", "Playful, bright and cheerful female voice."),
        ("cjVigY5qzO86Huf0OWal", "Eric", "male", "american", "Trustworthy, conversational male assistant voice."),
        ("iP95p4xoKVk53GoZ742B", "Chris", "male", "american", "Easygoing and casual young male speaker."),
        ("nPczCjzI2devNBz1zQrb", "Brian", "male", "american", "Deep, resonant and authoritative male voice."),
        ("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", "british", "Authoritative, dignified British voice for news and formal calls."),
        ("pFZP5JQG7iQjIQuC4Bku", "Lily", "female", "british", "Warm, soothing British narrator."),
        ("pqHfZKP75CvOlQylNhV4", "Bill", "male", "american", "Trustworthy, mature American male speaker."),
        ("t0jbNlBVZ17f02VDIeMI", "Jessie", "male", "american", "Distinctive raspy conversational tone."),
        ("ThT5KcBeYPX3keUQqHPh", "Dorothy", "female", "british", "Pleasant, polite British female voice."),
        ("flq6f7yk4E4fJM5XTYuZ", "Michael", "male", "american", "Natural corporate conversational voice."),
        ("g5CIjZEefAph4nQFvHAz", "Ethan", "male", "american", "Soft, gentle and relaxed male voice."),
        ("jBpfuIE2acCO8z3wKNLl", "Gigi", "female", "american", "Youthful, energetic female voice."),
        ("jsCqWAovK2LkecY7zXl4", "Freya", "female", "american", "Expressive, empathetic female voice."),
        ("oWAxZDx7w5VEj9dCyTzz", "Grace", "female", "american", "Friendly Southern American accent female voice."),
        ("piTKgcLEGmPE4e6mEKli", "Nicole", "female", "american", "Calm and whispery female speaker."),
        ("z9fAnlkpzviPz146aGWa", "Glinda", "female", "american", "Lively theatrical voice."),
        ("zcAOhNBS3c14rBihAFp1", "Giovanni", "male", "italian", "English with charming Italian accent."),
        ("zrHiDhphv9ZnVXBqCLjz", "Mimi", "female", "swedish", "Sweet, cheerful Scandinavian English voice."),
        ("2EiwWnXFnvU5JabPnv8n", "Clyde", "male", "american", "Seasoned, gravelly veteran male voice."),
        ("CYw3kZ02Hs0563khs1Fj", "Dave", "male", "british", "Casual London conversational voice."),
        ("D38z5RcWu1voky8WS1ja", "Fin", "male", "irish", "Authentic Irish cadence and natural melody."),
        ("LcfcDJNUP1GQjkzn1xUU", "Emily", "female", "american", "Calm, thoughtful female voice."),
        ("SOYHLrjzK2X1ezoPC6cr", "Harry", "male", "american", "Relatable young adult voice."),
        ("ZQe5CZNOzWyzPSCn5a3c", "James", "male", "australian", "Grounded, polite Australian male speaker."),
        ("Zlb1dXrM653N07WRdFW3", "Joseph", "male", "british", "Stately, formal British male speaker."),
        ("bIHbv24MWmeRgasXA8ed", "Liam", "male", "american", "Energetic teenager and young adult voice."),
        ("wViXBPUzp2ZZixvnxNQu", "Paul", "male", "american", "Mature, grounded male voice for instructional calls."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": "en",
            "gender": gender,
            "accent": accent,
            "preview_url": f"https://storage.googleapis.com/eleven-public-prod/premade/voices/{vid}/preview.mp3",
        }
        for vid, name, gender, accent, desc in items
    ]


def get_sarvam_builtin_catalog() -> List[Dict[str, Any]]:
    """47+ complete Sarvam AI Bulbul v2 & v3 Indian regional voices."""
    speakers_data = [
        # Flagship conversational voices
        ("meera", "Meera", "female", "hi-IN", "Fluent, polite Indian female voice tailored for Hindi and Hinglish sales calls."),
        ("arvind", "Arvind", "male", "hi-IN", "Professional Indian male voice with natural pitch and conversational clarity."),
        ("shubh", "Shubh", "male", "hi-IN", "Energetic, persuasive young Indian male voice."),
        ("aditya", "Aditya", "male", "hi-IN", "Confident Indian executive speaker."),
        ("ritu", "Ritu", "female", "hi-IN", "Warm and welcoming female voice."),
        ("priya", "Priya", "female", "hi-IN", "Calm, empathetic Hindi/English female support agent."),
        ("neha", "Neha", "female", "hi-IN", "Friendly, clear customer outreach voice."),
        ("rahul", "Rahul", "male", "en-IN", "Corporate Indian English & Hindi voice."),
        ("pooja", "Pooja", "female", "hi-IN", "Polite and conversational female specialist."),
        ("rohan", "Rohan", "male", "hi-IN", "Modern young Indian male voice."),
        ("simran", "Simran", "female", "pa-IN", "Authentic Punjabi & Hindi female voice."),
        ("kavya", "Kavya", "female", "te-IN", "Natural Telugu and Indian English conversational voice."),
        ("amit", "Amit", "male", "hi-IN", "Direct and clear Hindi male voice."),
        ("dev", "Dev", "male", "hi-IN", "Deep and steady male speaker."),
        ("ishita", "Ishita", "female", "hi-IN", "Pleasant, bright female sales voice."),
        ("shreya", "Shreya", "female", "hi-IN", "Expressive female voice."),
        ("ratan", "Ratan", "male", "hi-IN", "Mature and trustworthy male voice."),
        ("varun", "Varun", "male", "hi-IN", "Enthusiastic and articulate male speaker."),
        ("manan", "Manan", "male", "gu-IN", "Gujarati and Hindi male voice."),
        ("sumit", "Sumit", "male", "hi-IN", "Clear conversational Hindi speaker."),
        ("roopa", "Roopa", "female", "kn-IN", "Kannada and Indian English female voice."),
        ("kabir", "Kabir", "male", "hi-IN", "Warm and persuasive male narrator."),
        ("aayan", "Aayan", "male", "hi-IN", "Friendly Indian male support voice."),
        ("ashutosh", "Ashutosh", "male", "hi-IN", "Authoritative male voice."),
        ("advait", "Advait", "male", "hi-IN", "Gentle, polite customer relationship voice."),
        ("amelia", "Amelia", "female", "en-IN", "Fluent Indian English corporate voice."),
        ("sophia", "Sophia", "female", "en-IN", "Modern, international Indian English voice."),
        ("anand", "Anand", "male", "ta-IN", "Tamil and South Indian English male speaker."),
        ("tanya", "Tanya", "female", "hi-IN", "Bright customer interaction voice."),
        ("tarun", "Tarun", "male", "hi-IN", "Persuasive sales consultant."),
        ("sunny", "Sunny", "male", "pa-IN", "Upbeat Punjabi and Hindi male voice."),
        ("mani", "Mani", "male", "ta-IN", "Tamil male voice with authentic cadence."),
        ("gokul", "Gokul", "male", "ml-IN", "Malayalam and Indian English male speaker."),
        ("vijay", "Vijay", "male", "te-IN", "Telugu and Hindi male specialist."),
        ("shruti", "Shruti", "female", "mr-IN", "Marathi and Hindi female voice."),
        ("suhani", "Suhani", "female", "hi-IN", "Charming female voice for outbound campaigns."),
        ("mohit", "Mohit", "male", "hi-IN", "Friendly and natural male voice."),
        ("kavitha", "Kavitha", "female", "ta-IN", "Polite Tamil female support voice."),
        ("rehan", "Rehan", "male", "ur-IN", "Articulate Urdu and Hindi male voice."),
        ("soham", "Soham", "male", "bn-IN", "Bengali and Indian English male voice."),
        ("rupali", "Rupali", "female", "mr-IN", "Marathi female speaker."),
        ("pavithra", "Pavithra", "female", "ta-IN", "Authentic South Indian female voice for Tamil and Indian English."),
        ("amartya", "Amartya", "male", "bn-IN", "Clear Bengali and Indian English male speaker."),
        ("maitreyi", "Maitreyi", "female", "mr-IN", "Expressive Marathi and Hindi female voice with polite diction."),
        ("suhas", "Suhas", "male", "kn-IN", "Friendly Kannada and Indian English male voice for customer engagement."),
        ("anushka", "Anushka (v2)", "female", "hi-IN", "Classic Sarvam v2 Hindi female voice."),
        ("vidya", "Vidya (v2)", "female", "hi-IN", "Classic Sarvam v2 natural narrator."),
        ("karun", "Karun (v2)", "male", "ta-IN", "Classic Sarvam v2 Tamil male speaker."),
    ]
    return [
        {
            "voice_id": vid,
            "name": f"{name} ({lang})",
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": "indian",
            "preview_url": f"https://cdn.sarvam.ai/samples/{vid}_sample.mp3",
        }
        for vid, name, gender, lang, desc in speakers_data
    ]


def get_deepgram_builtin_catalog() -> List[Dict[str, Any]]:
    """20+ Deepgram Aura voices across accents."""
    voices_data = [
        ("aura-asteria-en", "Asteria", "female", "en", "american", "Warm, confident American female voice optimized for fast real-time conversations."),
        ("aura-luna-en", "Luna", "female", "en", "american", "Energetic and friendly young American female voice."),
        ("aura-stella-en", "Stella", "female", "en", "american", "Professional, composed and smooth female voice."),
        ("aura-athena-en", "Athena", "female", "en-GB", "british", "Polite British female voice with excellent diction."),
        ("aura-hera-en", "Hera", "female", "en", "american", "Authoritative, dignified mature female voice."),
        ("aura-orion-en", "Orion", "male", "en", "american", "Deep, authoritative American male voice for corporate and executive calls."),
        ("aura-arcas-en", "Arcas", "male", "en", "american", "Calm, casual and relatable American male voice."),
        ("aura-perseus-en", "Perseus", "male", "en", "american", "Strong, direct and engaging conversational male voice."),
        ("aura-angus-en", "Angus", "male", "en-IE", "irish", "Distinguished Irish male voice with charming cadence."),
        ("aura-orpheus-en", "Orpheus", "male", "en", "american", "Confident and soothing male voice."),
        ("aura-helios-en", "Helios", "male", "en-GB", "british", "Refined, articulate British male voice."),
        ("aura-zeus-en", "Zeus", "male", "en", "american", "Commanding, resonant deep male voice."),
        ("aura-thales-en", "Thales", "male", "en", "american", "Approachable and thoughtful male narrator."),
        ("aura-hyperion-en", "Hyperion", "male", "en-AU", "australian", "Friendly, genuine Australian male speaker."),
        ("aura-vesta-es", "Vesta (Spanish)", "female", "es", "spanish", "Conversational Latin American Spanish female voice."),
        ("aura-nestor-es", "Nestor (Spanish)", "male", "es", "spanish", "Clear Latin American Spanish male voice."),
        ("aura-dione-fr", "Dione (French)", "female", "fr", "french", "Elegant, native French female voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": accent,
            "preview_url": f"https://static.deepgram.com/examples/audio/{vid}.mp3",
        }
        for vid, name, gender, lang, accent, desc in voices_data
    ]


def get_openai_builtin_catalog() -> List[Dict[str, Any]]:
    """Complete OpenAI speech voices."""
    items = [
        ("alloy", "Alloy", "neutral", "american", "Versatile, balanced and expressive conversational voice."),
        ("echo", "Echo", "male", "american", "Warm, resonant male voice with rounded tones."),
        ("fable", "Fable", "male", "british", "Expressive British accent voice with distinctive narrative rhythm."),
        ("onyx", "Onyx", "male", "american", "Deep, smooth and commanding male voice."),
        ("nova", "Nova", "female", "american", "Energetic, bright and engaging female voice."),
        ("shimmer", "Shimmer", "female", "american", "Clear, optimistic and pleasant female voice."),
        ("ash", "Ash", "male", "american", "Relaxed and conversational male voice with gentle cadence."),
        ("coral", "Coral", "female", "american", "Supportive and friendly female voice for customer support."),
        ("sage", "Sage", "neutral", "american", "Mature, authoritative and trustworthy conversational voice."),
        ("verse", "Verse", "male", "american", "Dynamic, adaptable voice with contemporary tone."),
        ("ballad", "Ballad", "male", "british", "Storytelling, measured British male voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": "en",
            "gender": gender,
            "accent": accent,
            "preview_url": f"https://cdn.openai.com/api/voices/{vid}.mp3",
        }
        for vid, name, gender, accent, desc in items
    ]


def get_smallest_builtin_catalog() -> List[Dict[str, Any]]:
    """22+ standard and pro Smallest Waves voices."""
    voices_data = [
        ("sophia", "Sophia", "female", "en", "american", "Clear and charming American female voice."),
        ("avery", "Avery", "female", "en", "american", "Dynamic and modern female speaker."),
        ("liam", "Liam", "male", "en", "american", "Youthful, energetic conversational male voice."),
        ("lucas", "Lucas", "male", "en", "american", "Approachable and friendly male narrator."),
        ("olivia", "Olivia", "female", "en", "american", "Warm and articulate female voice."),
        ("ryan", "Ryan", "male", "en", "american", "Casual and direct male conversationalist."),
        ("freya", "Freya", "female", "en", "american", "Sweet, expressive female tone."),
        ("william", "William", "male", "en", "british", "Polite and formal British male speaker."),
        ("arjun", "Arjun (Hindi/English)", "male", "hi", "indian", "Authentic conversational Indian male voice."),
        ("niharika", "Niharika (Hindi Specialist)", "female", "hi", "indian", "Natural Indian female voice for outbound campaigns."),
        ("devansh", "Devansh", "male", "hi", "indian", "Confident Indian male executive voice."),
        ("maya", "Maya", "female", "hi", "indian", "Empathetic Indian customer support specialist."),
        ("dhruv", "Dhruv", "male", "hi", "indian", "Persuasive Indian sales voice."),
        ("mia", "Mia", "female", "en", "american", "Upbeat assistant voice."),
        ("maithili", "Maithili", "female", "hi", "indian", "Polite Hindi conversational speaker."),
        # Pro voices
        ("meher", "Meher (Pro Voice)", "female", "hi", "indian", "Ultra high-fidelity Indian female voice for lightning_v3.1_pro."),
        ("aviraj", "Aviraj (Pro Voice)", "male", "hi", "indian", "Premium expressive Indian male voice for lightning_v3.1_pro."),
        ("rhea", "Rhea (Pro Voice)", "female", "hi", "indian", "Sophisticated Indian English female narrator."),
        ("cressida", "Cressida (Pro Voice)", "female", "en", "british", "Luxury British female voice."),
        ("willow", "Willow (Pro Voice)", "female", "en", "american", "Warm storytelling American voice."),
        ("maverick", "Maverick (Pro Voice)", "male", "en", "american", "Bold, commanding male voice for high-impact calls."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": accent,
            "preview_url": None,
        }
        for vid, name, gender, lang, accent, desc in voices_data
    ]


def get_azure_builtin_catalog() -> List[Dict[str, Any]]:
    """30+ high-demand Microsoft Azure Neural Voices."""
    items = [
        ("en-US-JennyNeural", "Jenny (Neural)", "female", "en-US", "american", "Natural, conversational and widely used American female neural voice."),
        ("en-US-GuyNeural", "Guy (Neural)", "male", "en-US", "american", "Friendly, warm and professional American male neural voice."),
        ("en-US-AriaNeural", "Aria (Neural)", "female", "en-US", "american", "Expressive American female voice suitable for news and customer service."),
        ("en-US-DavisNeural", "Davis (Neural)", "male", "en-US", "american", "Casual, calm and grounded male voice."),
        ("en-US-AmberNeural", "Amber (Neural)", "female", "en-US", "american", "Warm and empathetic female customer voice."),
        ("en-US-AnaNeural", "Ana (Neural)", "female", "en-US", "american", "Friendly, youthful female speaker."),
        ("en-US-AshleyNeural", "Ashley (Neural)", "female", "en-US", "american", "Clear, pleasant and direct assistant tone."),
        ("en-US-BrandonNeural", "Brandon (Neural)", "male", "en-US", "american", "Enthusiastic and engaging young male voice."),
        ("en-US-ChristopherNeural", "Christopher (Neural)", "male", "en-US", "american", "Authoritative, dignified corporate male narrator."),
        ("en-US-ElizabethNeural", "Elizabeth (Neural)", "female", "en-US", "american", "Refined and articulate female corporate voice."),
        ("en-US-EricNeural", "Eric (Neural)", "male", "en-US", "american", "Reliable and reassuring customer service male voice."),
        ("en-US-JacobNeural", "Jacob (Neural)", "male", "en-US", "american", "Natural, easygoing male conversationalist."),
        ("en-US-MichelleNeural", "Michelle (Neural)", "female", "en-US", "american", "Bright and friendly female corporate voice."),
        ("en-US-MonicaNeural", "Monica (Neural)", "female", "en-US", "american", "Professional female voice with high clarity."),
        ("en-US-NancyNeural", "Nancy (Neural)", "female", "en-US", "american", "Polite and measured female speaker."),
        ("en-US-RogerNeural", "Roger (Neural)", "male", "en-US", "american", "Seasoned, confident male voice."),
        ("en-US-SaraNeural", "Sara (Neural)", "female", "en-US", "american", "Modern, adaptable female assistant voice."),
        ("en-US-SteffanNeural", "Steffan (Neural)", "male", "en-US", "american", "Articulate and polite male voice."),
        ("en-US-TonyNeural", "Tony (Neural)", "male", "en-US", "american", "Direct and confident male voice."),
        # Indian Neural Voices
        ("en-IN-NeerjaNeural", "Neerja (Indian English)", "female", "en-IN", "indian", "Crisp and polite Indian English female voice."),
        ("en-IN-PrabhatNeural", "Prabhat (Indian English)", "male", "en-IN", "indian", "Professional Indian English male voice."),
        ("hi-IN-SwaraNeural", "Swara (Hindi)", "female", "hi-IN", "indian", "Polite Hindi female neural voice for customer interactions."),
        ("hi-IN-MadhurNeural", "Madhur (Hindi)", "male", "hi-IN", "indian", "Clear and resonant Hindi male neural voice."),
        ("ta-IN-PallaviNeural", "Pallavi (Tamil)", "female", "ta-IN", "indian", "Natural Tamil female conversational voice."),
        ("te-IN-ShrutiNeural", "Shruti (Telugu)", "female", "te-IN", "indian", "Fluent Telugu female assistant voice."),
        ("mr-IN-AarohiNeural", "Aarohi (Marathi)", "female", "mr-IN", "indian", "Expressive Marathi female speaker."),
        ("bn-IN-TanishaaNeural", "Tanishaa (Bengali)", "female", "bn-IN", "indian", "Sweet, clear Bengali female voice."),
        ("gu-IN-DhwaniNeural", "Dhwani (Gujarati)", "female", "gu-IN", "indian", "Polite Gujarati female speaker."),
        ("kn-IN-SapnaNeural", "Sapna (Kannada)", "female", "kn-IN", "indian", "Natural Kannada female speaker."),
        # UK Neural Voices
        ("en-GB-SoniaNeural", "Sonia (British)", "female", "en-GB", "british", "Sophisticated, warm British female voice."),
        ("en-GB-RyanNeural", "Ryan (British)", "male", "en-GB", "british", "Approachable, conversational British male voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": accent,
            "preview_url": None,
        }
        for vid, name, gender, lang, accent, desc in items
    ]


def get_google_builtin_catalog() -> List[Dict[str, Any]]:
    """20+ Google Cloud Chirp 3 HD & Neural2 voices."""
    items = [
        ("en-US-Chirp3-HD-Charon", "Charon (Chirp 3 HD)", "male", "en-US", "american", "Next-gen ultra realistic conversational male voice."),
        ("en-US-Chirp3-HD-Puck", "Puck (Chirp 3 HD)", "male", "en-US", "american", "Bright, energetic Chirp 3 HD male voice."),
        ("en-US-Chirp3-HD-Aoede", "Aoede (Chirp 3 HD)", "female", "en-US", "american", "Expressive, empathetic Chirp 3 HD female voice."),
        ("en-US-Chirp3-HD-Kore", "Kore (Chirp 3 HD)", "female", "en-US", "american", "Composed, soothing Chirp 3 HD female voice."),
        ("en-US-Chirp3-HD-Fenrir", "Fenrir (Chirp 3 HD)", "male", "en-US", "american", "Deep, authoritative Chirp 3 HD male voice."),
        ("Puck", "Puck (Gemini Live)", "male", "en", "american", "Bright, responsive Gemini Live interactive voice."),
        ("Aoede", "Aoede (Gemini Live)", "female", "en", "american", "Expressive, empathetic Gemini Live female voice."),
        ("Kore", "Kore (Gemini Live)", "female", "en", "american", "Composed, soothing female conversational voice."),
        ("Charon", "Charon (Gemini Live)", "male", "en", "american", "Calm and articulate Gemini Live male voice."),
        ("Fenrir", "Fenrir (Gemini Live)", "male", "en", "american", "Commanding Gemini Live male voice."),
        ("en-US-Journey-F", "Journey F", "female", "en-US", "american", "Spontaneous, natural conversational voice."),
        ("en-US-Journey-D", "Journey D", "male", "en-US", "american", "Natural flowing male conversational tone."),
        ("en-US-Studio-O", "Studio O", "female", "en-US", "american", "High-fidelity broadcast studio female voice."),
        ("en-US-Studio-Q", "Studio Q", "male", "en-US", "american", "Professional broadcast studio male voice."),
        ("en-US-Neural2-A", "Neural2 A", "female", "en-US", "american", "Clear corporate female voice."),
        ("en-US-Neural2-C", "Neural2 C", "male", "en-US", "american", "Friendly corporate male voice."),
        ("en-US-Neural2-F", "Neural2 F", "female", "en-US", "american", "Warm instructional female voice."),
        # Indian voices
        ("en-IN-Neural2-A", "Google Indian Female (Neural2 A)", "female", "en-IN", "indian", "Natural Indian English female assistant voice."),
        ("en-IN-Neural2-B", "Google Indian Male (Neural2 B)", "male", "en-IN", "indian", "Approachable Indian English male assistant voice."),
        ("en-IN-Neural2-D", "Google Indian Female (Neural2 D)", "female", "en-IN", "indian", "Polite Indian English customer support voice."),
        ("hi-IN-Neural2-A", "Google Hindi Female (Neural2 A)", "female", "hi-IN", "indian", "Fluent Hindi female conversational voice."),
        ("hi-IN-Neural2-B", "Google Hindi Male (Neural2 B)", "male", "hi-IN", "indian", "Standard Hindi male conversational voice."),
        ("hi-IN-Neural2-C", "Google Hindi Male (Neural2 C)", "male", "hi-IN", "indian", "Direct Hindi male outreach voice."),
        ("hi-IN-Neural2-D", "Google Hindi Female (Neural2 D)", "female", "hi-IN", "indian", "Empathetic Hindi female support voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": accent,
            "preview_url": None,
        }
        for vid, name, gender, lang, accent, desc in items
    ]


def get_speechify_builtin_catalog() -> List[Dict[str, Any]]:
    """12+ Speechify natural voices."""
    items = [
        ("george", "George", "male", "en-GB", "british", "Natural, articulate British English male voice."),
        ("kristy", "Kristy", "female", "en-US", "american", "Smooth and warm American female narrator."),
        ("cliff", "Cliff", "male", "en-US", "american", "Energetic, engaging male voice."),
        ("stephanie", "Stephanie", "female", "en-US", "american", "Professional, composed corporate female voice."),
        ("henry", "Henry", "male", "en-GB", "british", "Classic British male narrator."),
        ("oliver", "Oliver", "male", "en-GB", "british", "Young, friendly British male speaker."),
        ("joe", "Joe", "male", "en-US", "american", "Conversational, relatable American male voice."),
        ("tish", "Tish", "female", "en-US", "american", "Warm storytelling female voice."),
        ("guy", "Guy", "male", "en-US", "american", "Clear corporate presenter."),
        ("matthew", "Matthew", "male", "en-US", "american", "Direct and articulate male voice."),
        ("alison", "Alison", "female", "en-US", "american", "Friendly customer support specialist."),
        ("mary", "Mary", "female", "en-US", "american", "Polite, reassuring female voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": lang,
            "gender": gender,
            "accent": accent,
            "preview_url": None,
        }
        for vid, name, gender, lang, accent, desc in items
    ]


def get_rime_builtin_catalog() -> List[Dict[str, Any]]:
    """16+ Rime Mist neural voices."""
    items = [
        ("mistv2", "Mist v2", "female", "Ultra-low latency conversational voice."),
        ("arcana", "Arcana", "female", "Dynamic, expressive voice optimized for customer calls."),
        ("marsh", "Marsh", "male", "Deep and grounded male voice."),
        ("astra", "Astra", "female", "Clear, energetic conversational female voice."),
        ("luna", "Luna", "female", "Gentle, polite customer care female voice."),
        ("bloom", "Bloom", "female", "Bright, optimistic female outreach voice."),
        ("ember", "Ember", "female", "Expressive storytelling tone."),
        ("peak", "Peak", "male", "Crisp, authoritative male business voice."),
        ("echo", "Echo", "male", "Resonant conversational male narrator."),
        ("dune", "Dune", "male", "Warm and relaxed male conversationalist."),
        ("breeze", "Breeze", "female", "Light, breezy and friendly voice."),
        ("canyon", "Canyon", "male", "Commanding deep male voice."),
        ("cedar", "Cedar", "male", "Dependable, steady male tone."),
        ("haven", "Haven", "female", "Reassuring, soft female speaker."),
        ("flint", "Flint", "male", "Direct, assertive sales specialist."),
        ("jade", "Jade", "female", "Articulate and polished customer voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": "en",
            "gender": gender,
            "accent": "american",
            "preview_url": None,
        }
        for vid, name, gender, desc in items
    ]


def get_lmnt_builtin_catalog() -> List[Dict[str, Any]]:
    """13+ LMNT speech voices."""
    items = [
        ("lily", "Lily", "female", "Natural, warm conversational female voice."),
        ("daniel", "Daniel", "male", "Articulate, approachable male voice."),
        ("zoe", "Zoe", "female", "Energetic and crisp female voice."),
        ("miles", "Miles", "male", "Casual, easygoing young male voice."),
        ("evelyn", "Evelyn", "female", "Sophisticated corporate narrator."),
        ("chloe", "Chloe", "female", "Playful and lively female conversationalist."),
        ("henry", "Henry", "male", "Classic, polished male voice."),
        ("laura", "Laura", "female", "Confident and direct customer outreach voice."),
        ("morgan", "Morgan", "neutral", "Balanced, neutral assistant tone."),
        ("nathan", "Nathan", "male", "Warm and friendly male voice."),
        ("oliver", "Oliver", "male", "Polite and articulate speaker."),
        ("paige", "Paige", "female", "Bright and helpful support voice."),
        ("quinn", "Quinn", "neutral", "Modern and adaptive conversational voice."),
    ]
    return [
        {
            "voice_id": vid,
            "name": name,
            "description": desc,
            "language": "en",
            "gender": gender,
            "accent": "american",
            "preview_url": None,
        }
        for vid, name, gender, desc in items
    ]


# ==============================================================================
# 4. UNIFIED VOICE CATALOG DISPATCHER
# ==============================================================================


async def get_direct_provider_voices(
    provider: str,
    organization_id: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch voices directly for any configured provider using BYOK or Platform Master Key.

    Returns:
        Tuple of (voices_list, key_source)
    """
    norm_provider = provider.lower().strip()
    if norm_provider == "azure_speech":
        norm_provider = "azure"

    api_key, key_source = await resolve_tts_credentials(
        norm_provider, organization_id=organization_id
    )

    fetcher_map = {
        "cartesia": fetch_cartesia_voices,
        "elevenlabs": fetch_elevenlabs_voices,
        "sarvam": fetch_sarvam_voices,
        "deepgram": fetch_deepgram_voices,
        "openai": fetch_openai_voices,
        "smallest": fetch_smallest_voices,
        "azure": fetch_azure_voices,
        "google": fetch_google_voices,
        "speechify": fetch_speechify_voices,
        "rime": fetch_rime_voices,
        "lmnt": fetch_lmnt_voices,
    }

    builtin_fallback_map = {
        "cartesia": get_cartesia_builtin_catalog,
        "elevenlabs": get_elevenlabs_builtin_catalog,
        "sarvam": get_sarvam_builtin_catalog,
        "deepgram": get_deepgram_builtin_catalog,
        "openai": get_openai_builtin_catalog,
        "smallest": get_smallest_builtin_catalog,
        "azure": get_azure_builtin_catalog,
        "google": get_google_builtin_catalog,
        "speechify": get_speechify_builtin_catalog,
        "rime": get_rime_builtin_catalog,
        "lmnt": get_lmnt_builtin_catalog,
    }

    try:
        fetcher = fetcher_map.get(norm_provider)
        if fetcher:
            voices = await fetcher(api_key)
        else:
            fallback = builtin_fallback_map.get(norm_provider, get_cartesia_builtin_catalog)
            voices = fallback()
    except Exception as exc:
        logger.error(
            "Direct voice fetch error for {} (source={}): {}. Using offline catalog.",
            norm_provider,
            key_source,
            exc,
        )
        fallback = builtin_fallback_map.get(norm_provider, get_cartesia_builtin_catalog)
        voices = fallback()

    return voices, key_source
