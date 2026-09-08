"""Rumik AI Silk TTS configuration options."""

# Only streaming models are supported on this platform
RUMIK_TTS_MODELS = (
    "mulberry",  # Fast streaming model steered via natural language voice prompts
    "muga",      # Expressive streaming model steered via inline emotional tone tags
)

RUMIK_TTS_LANGUAGES = (
    "en",        # English
    "hi",        # Hindi
    "hinglish",  # Hinglish (Conversational Indian English + Hindi)
    "bn",        # Bengali
    "mr",        # Marathi
    "ta",        # Tamil
    "te",        # Telugu
    "gu",        # Gujarati
    "kn",        # Kannada
)

# Suggested voice description examples for Rumik Silk (steered via natural language or preset)
RUMIK_TTS_VOICE_PRESETS = (
    "warm professional female, Indian accent",
    "friendly conversational male, Indian accent",
    "calm customer support female, Hinglish",
    "energetic young male, Hinglish",
    "clear professional female, neutral accent",
    "empathetic advisory female, Indian accent",
)
