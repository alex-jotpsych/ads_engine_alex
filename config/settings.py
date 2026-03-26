"""
Configuration — all secrets and settings in one place.

Uses environment variables with .env file fallback.
NEVER commit .env to version control.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    All configuration for the ads engine.
    Set via environment variables or .env file.
    """

    # -- Anthropic (for intake parsing + copy generation) --
    ANTHROPIC_API_KEY: str = ""

    # -- Meta Marketing API --
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_ACCESS_TOKEN: str = ""
    META_AD_ACCOUNT_ID: str = ""           # Format: act_XXXXXXXXXX

    # -- Google Ads API --
    GOOGLE_ADS_DEVELOPER_TOKEN: str = ""
    GOOGLE_ADS_CLIENT_ID: str = ""
    GOOGLE_ADS_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REFRESH_TOKEN: str = ""
    GOOGLE_ADS_CUSTOMER_ID: str = ""       # Format: XXX-XXX-XXXX (no dashes in API)

    # -- Slack --
    SLACK_WEBHOOK_URL: str = ""
    SLACK_CHANNEL: str = "#ads-engine"

    # -- Image Generation --
    OPENAI_API_KEY: str = ""               # For DALL-E 3
    GOOGLE_GEMINI_API_KEY: str = ""        # For Imagen 3

    # -- Video Generation (future — Veo 3 via Gemini API) --
    VIDEO_GEN_API_KEY: str = ""
    VIDEO_GEN_PROVIDER: str = ""

    # -- Decision Engine Thresholds --
    MIN_SPEND_FOR_DECISION: float = 50.0
    MIN_DAYS_LIVE: int = 3
    KILL_CPA_MULTIPLIER: float = 2.0
    SCALE_CPA_MULTIPLIER: float = 0.7
    DAILY_BUDGET_LIMIT: float = 700.0      # ~$20k/mo ÷ 30

    # -- Data --
    DATA_DIR: str = "data"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


def get_settings() -> Settings:
    return Settings()
