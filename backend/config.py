"""Central configuration loaded from environment variables.

Required to run the live pipeline:
    QLOO_API_KEY   - hackathon key from the Qloo API key request form
                     (valid only against https://hackathon.api.qloo.com)
    LLM_API_KEY    - key for any OpenAI-compatible chat-completions endpoint

Everything else has sane defaults and can be overridden.
"""
from __future__ import annotations

import os

try:  # local dev convenience: load .env if present; harmless in production
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    # --- Qloo ---
    QLOO_API_KEY: str = _get("QLOO_API_KEY")
    QLOO_BASE_URL: str = _get("QLOO_BASE_URL", "https://hackathon.api.qloo.com")
    LOCAL_QLOO: bool = _get("TASTEATLAS_LOCAL_QLOO", "0") == "1"
    QLOO_PROXY: str = _get("QLOO_PROXY_URL")
    STATE_DIR: str = _get("TASTEATLAS_STATE_DIR")
    QLOO_CALL_LIMIT: int = int(_get("TASTEATLAS_QLOO_CALL_LIMIT", "24"))
    REQUIRE_EXISTING_STATE: bool = _get("TASTEATLAS_REQUIRE_EXISTING_STATE", "0") == "1"

    # --- LLM (any OpenAI-compatible chat completions endpoint) ---
    LLM_API_KEY: str = _get("LLM_API_KEY")
    LLM_BASE_URL: str = _get("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = _get("LLM_MODEL", "gpt-4o-mini")
    LLM_ENABLED: bool = _get("TASTEATLAS_LLM_ENABLED", "0") == "1"

    # --- App ---
    PORT: int = int(_get("PORT", "8000"))
    REQUEST_TIMEOUT_S: float = float(_get("REQUEST_TIMEOUT_S", "30"))


settings = Settings()
