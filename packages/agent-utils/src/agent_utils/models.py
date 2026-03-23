"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """Unified message model used by both CWM and MWM.

    CWM uses: user, text, ts, thread_ts, replies
    MWM uses: user, text, ts, thread_ts, is_bot, timestamp
    """

    user: str
    text: str
    ts: str  # Slack timestamp string (message ID)
    thread_ts: str = ""
    is_bot: bool = False
    timestamp: datetime | None = None  # Parsed datetime (used by MWM)
    replies: list[Message] = field(default_factory=list)  # Thread replies (used by CWM)
