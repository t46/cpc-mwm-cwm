"""Slack message fetching, user resolution, and formatting.

Ported from cpc-cwm/cpc_scholar_bot/slack_reader.py.
Provides sync batch-fetch operations used by CWM.
MWM receives messages in real-time via Socket Mode and does not use these functions.
"""

from __future__ import annotations

import logging
from typing import Optional

from slack_sdk import WebClient

from .models import Message

logger = logging.getLogger(__name__)


def fetch_channel_messages(
    client: WebClient,
    channel_id: str,
    limit: int = 1000,
) -> list[Message]:
    """Fetch all messages from a channel, including threaded replies."""
    messages: list[Message] = []
    cursor = None

    while True:
        kwargs: dict = {"channel": channel_id, "limit": min(limit, 200)}
        if cursor:
            kwargs["cursor"] = cursor

        result = client.conversations_history(**kwargs)

        for msg in result.get("messages", []):
            if msg.get("subtype"):
                continue

            # Fetch thread replies if any
            replies: list[Message] = []
            if int(msg.get("reply_count", 0)) > 0:
                replies = _fetch_thread_replies(client, channel_id, msg["ts"])

            messages.append(
                Message(
                    user=msg.get("user", "unknown"),
                    text=msg.get("text", ""),
                    ts=msg["ts"],
                    thread_ts=msg.get("thread_ts", ""),
                    replies=replies,
                )
            )

            if len(messages) >= limit:
                break

        if len(messages) >= limit:
            break

        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    # Reverse to chronological order (oldest first)
    messages.reverse()
    logger.info("Fetched %d messages from channel %s", len(messages), channel_id)
    return messages


def _fetch_thread_replies(
    client: WebClient, channel_id: str, thread_ts: str
) -> list[Message]:
    """Fetch all replies in a thread (excluding the parent)."""
    replies: list[Message] = []
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        for msg in result.get("messages", [])[1:]:  # Skip parent
            if msg.get("subtype"):
                continue
            replies.append(
                Message(
                    user=msg.get("user", "unknown"),
                    text=msg.get("text", ""),
                    ts=msg["ts"],
                    thread_ts=thread_ts,
                    replies=[],
                )
            )
    except Exception as e:
        logger.error("Failed to fetch thread %s: %s", thread_ts, e)
    return replies


def resolve_user_names(
    client: WebClient, messages: list[Message]
) -> dict[str, str]:
    """Build a user_id -> display_name mapping for all users in messages."""
    user_ids: set[str] = set()
    for msg in messages:
        user_ids.add(msg.user)
        for reply in msg.replies:
            user_ids.add(reply.user)

    user_map: dict[str, str] = {}
    for uid in user_ids:
        if uid == "unknown":
            continue
        try:
            info = client.users_info(user=uid)
            profile = info["user"]["profile"]
            user_map[uid] = (
                profile.get("display_name")
                or profile.get("real_name")
                or uid
            )
        except Exception:
            user_map[uid] = uid

    return user_map


def format_messages_for_prompt(
    messages: list[Message], user_map: dict[str, str]
) -> str:
    """Format messages into a readable text for the LLM prompt."""
    lines: list[str] = []

    for msg in messages:
        name = user_map.get(msg.user, msg.user)
        lines.append(f"[{name}]: {msg.text}")

        for reply in msg.replies:
            reply_name = user_map.get(reply.user, reply.user)
            lines.append(f"  └ [{reply_name}]: {reply.text}")

        if msg.replies:
            lines.append("")  # Blank line after threaded discussions

    return "\n".join(lines)
