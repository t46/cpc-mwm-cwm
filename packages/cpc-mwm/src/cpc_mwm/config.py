from __future__ import annotations

from agent_utils.config import BaseConfig


class MwmConfig(BaseConfig):
    """MWM-specific configuration loaded from environment variables."""

    slack_app_token: str
    mwm_bot_channel_id: str
    agent_config: str = "agents/ada.yml"
    agent_configs: str = ""  # Comma-separated list for multi-agent mode
    whitepaper_path: str = "whitepapers/whitepaper.md"
    # Deprecated — kept for backward compat, ignored when agent configs are used
    persona_file: str = "personas/ada.md"
    persona_files: str = ""
    strategy_path: str = "strategies/default.md"
    response_interval_seconds: int = 120
    enable_audio: bool = False
    audio_device: str | None = None
    whisper_model: str = "large-v3"
    whisper_language: str = "ja"
    free_discussion_interval_seconds: int = 60
    max_consecutive_bot_messages: int = 20
    spontaneous_interval_seconds: int = 1800  # 30 minutes
    max_daily_spontaneous_posts: int = 10
    max_daily_api_calls: int = 200
    thread_probability: float = 0.4
    max_thread_replies: int = 5
    thread_target_max_age_seconds: int = 300
