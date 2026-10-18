"""Central configuration for Nyvexa backend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

try:
    from dotenv import load_dotenv

    # Load backend/.env regardless of the working directory
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(_env_path)
    load_dotenv()  # also allow project-root .env overrides
except ImportError:  # pragma: no cover
    pass


def _clean_key(key: str) -> str:
    key = (key or "").strip()
    if key in {"", "sk-or-v1-your-key-here", "your-key-here", "changeme", "nvapi-your-key"}:
        return ""
    if key.startswith("sk-or-") and key.endswith("here"):
        return ""
    return key


# Model catalog shown in the UI (NVIDIA NIM + OpenRouter free)
DEFAULT_MODELS: list[dict[str, str]] = [
    {
        "id": "nvidia/nemotron-3-super-120b-a12b",
        "label": "Nemotron 3 Super 120B (NVIDIA)",
        "description": "NVIDIA NIM — 120B",
    },
    {
        "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "label": "Nemotron 3 Nano Omni (NVIDIA)",
        "description": "NVIDIA NIM — fast reasoning",
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "label": "Nemotron 3.5 Super VL (NVIDIA)",
        "description": "NVIDIA NIM — multimodal",
    },
    {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "label": "Nemotron 3 Super (OpenRouter free)",
        "description": "Free tier on OpenRouter",
    },
    {
        "id": "google/gemma-4-31b-it:free",
        "label": "Gemma 4 31B (free)",
        "description": "OpenRouter free",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "label": "Nemotron Lightning (free)",
        "description": "OpenRouter free",
    },
]


@dataclass(frozen=True)
class Settings:
    app_name: str = "Nyvexa"

    # OpenRouter (fallback provider)
    api_base: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    api_key: str = os.getenv("OPENROUTER_API_KEY", "")

    # NVIDIA NIM (primary provider when key set)
    nvidia_base: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    nvidia_key: str = os.getenv("NVIDIA_API_KEY", "")

    default_model: str = os.getenv(
        "NYVEXA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"
    )
    fallback_model: str = os.getenv(
        "NYVEXA_FALLBACK_MODEL",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning,"
        "nvidia/nemotron-3-super-120b-a12b:free,"
        "google/gemma-4-31b-it:free,"
        "nvidia/nemotron-3.5-lightning:free",
    )

    max_history: int = int(os.getenv("NYVEXA_MAX_HISTORY", "24"))
    max_message_chars: int = int(os.getenv("NYVEXA_MAX_MESSAGE_CHARS", "8000"))
    rate_limit_per_minute: int = int(os.getenv("NYVEXA_RATE_LIMIT", "20"))
    auth_rate_limit_per_minute: int = int(os.getenv("NYVEXA_AUTH_RATE_LIMIT", "10"))
    research_max_queries: int = int(os.getenv("NYVEXA_RESEARCH_MAX_QUERIES", "3"))
    research_results_per_query: int = int(os.getenv("NYVEXA_RESEARCH_RESULTS", "3"))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.getenv("NYVEXA_CORS_ORIGINS", "*").split(",")
            if o.strip()
        ]
    )

    @property
    def fallback_chain(self) -> list[str]:
        return [m.strip() for m in self.fallback_model.split(",") if m.strip()]

    @property
    def models(self) -> list[dict[str, str]]:
        return DEFAULT_MODELS

    @property
    def nvidia_configured(self) -> bool:
        return bool(_clean_key(self.nvidia_key))

    @property
    def openrouter_configured(self) -> bool:
        return bool(_clean_key(self.api_key))

    @property
    def configured(self) -> bool:
        return self.nvidia_configured or self.openrouter_configured

    def model_provider(self, model_id: str) -> str:
        """Route model IDs to a provider.

        NVIDIA NIM ids look like 'nvidia/...' or 'z-ai/...' WITHOUT ':free'.
        OpenRouter free ids end with ':free'.
        """
        if model_id.endswith(":free"):
            return "openrouter"
        # Known NVIDIA NIM prefixes / bare model names
        nvidia_prefixes = (
            "nvidia/", "z-ai/", "meta/", "mistralai/", "deepseek-ai/", "moonshotai/", "google/",
        )
        if model_id.startswith(nvidia_prefixes) and not model_id.endswith(":free"):
            return "nvidia"
        if "/" not in model_id:
            return "nvidia"
        return "openrouter"


@lru_cache
def get_settings() -> Settings:
    return Settings()
