from typing import List, Optional
from pydantic import BaseModel, Field


class SupportedLanguage(BaseModel):
    code: str
    name: str
    flag: Optional[str] = "🌐"


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: str | None = None
    accent: str | None = None
    gender: str | None = None
    language: str | None = None
    preview_url: str | None = None
    is_default: bool = False


class VoiceFacets(BaseModel):
    """Distinct selector values across a provider's full voice catalog."""

    genders: list[str] = Field(default_factory=list)
    accents: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class VoicesResponse(BaseModel):
    provider: str
    voices: list[VoiceInfo]
    facets: VoiceFacets | None = None
    default_model: Optional[str] = None
    default_voice: Optional[str] = None
    supported_languages: List[SupportedLanguage] = Field(default_factory=list)

