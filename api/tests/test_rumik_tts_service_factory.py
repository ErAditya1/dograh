"""Tests for Rumik AI Silk streaming TTS configuration and service factory."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options.rumik import RUMIK_TTS_MODELS
from api.services.configuration.registry import (
    REGISTRY,
    RumikTTSConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.service_factory import create_tts_service


def test_rumik_tts_configuration_defaults_and_registry():
    config = RumikTTSConfiguration(api_key="rumik-test-key")

    assert config.provider == ServiceProviders.RUMIK
    assert config.model == "mulberry"
    assert config.voice == "warm professional female, Indian accent"
    assert config.language == "hi"
    assert config.base_url == "https://silk-api.rumik.ai"
    assert (
        REGISTRY[ServiceType.TTS][ServiceProviders.RUMIK]
        is RumikTTSConfiguration
    )


def test_rumik_tts_only_accepts_streaming_models():
    # mulberry and muga are streaming models
    assert "mulberry" in RUMIK_TTS_MODELS
    assert "muga" in RUMIK_TTS_MODELS

    config_mulberry = RumikTTSConfiguration(api_key="test-key", model="mulberry")
    assert config_mulberry.model == "mulberry"

    config_muga = RumikTTSConfiguration(api_key="test-key", model="muga")
    assert config_muga.model == "muga"


def test_validator_accepts_rumik_service():
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            RumikTTSConfiguration(api_key="valid-rumik-key"),
            "tts",
        )
        == []
    )


def test_create_rumik_tts_service_with_telephony_sample_rate():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.RUMIK.value,
            api_key="rumik-secret-key",
            model="mulberry",
            voice="warm professional female, Indian accent",
            language="hi",
            base_url="wss://silk-api.rumik.ai/v1/stream",
        )
    )
    # Native telephony PSTN sample rate (Smartflo/Twilio)
    audio_config = SimpleNamespace(transport_out_sample_rate=8000)

    with patch(
        "pipecat_rumik.RumikTTSService.__init__",
        return_value=None,
    ) as mock_init:
        create_tts_service(user_config, audio_config)

    assert mock_init.call_count == 1
    kwargs = mock_init.call_args.kwargs

    assert kwargs["api_key"] == "rumik-secret-key"
    assert kwargs["gateway_url"] == "https://silk-api.rumik.ai"
    assert kwargs["settings"].model == "mulberry"
    assert kwargs["settings"].voice == "warm professional female, Indian accent"
