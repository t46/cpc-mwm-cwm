"""Base configuration shared by CWM and MWM."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class BaseConfig(BaseSettings):
    """Base configuration with fields shared across CWM and MWM.

    Both systems share a single Slack App, so the bot token is common.
    """

    slack_bot_token: str
    anthropic_api_key: str
    model_name: str = "claude-sonnet-4-20250514"

    model_config = {"env_file": ".env", "extra": "ignore"}
