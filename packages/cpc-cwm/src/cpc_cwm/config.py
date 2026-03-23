"""CWM-specific configuration."""

from __future__ import annotations

from agent_utils.config import BaseConfig


class CwmConfig(BaseConfig):
    """Configuration for the CWM (Collaborative Whitepaper Machine)."""

    cwm_source_channel_ids: str = ""
    github_token: str = ""
    github_repo: str = ""
