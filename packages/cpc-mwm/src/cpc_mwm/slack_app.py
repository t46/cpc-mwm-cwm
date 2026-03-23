from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from slack_bolt.app.async_app import AsyncApp

from agent_utils.models import Message
from cpc_mwm.slides import download_file_from_slack, extract_slide_texts
from cpc_mwm.transcript import parse_vtt

if TYPE_CHECKING:
    from cpc_mwm.config import MwmConfig
    from agent_utils.persona import Persona
    from cpc_mwm.session import SessionManager

logger = logging.getLogger(__name__)


async def safe_post(
    client,
    config: MwmConfig,
    text: str,
    persona: Persona | None = None,
    thread_ts: str = "",
) -> str:
    """Post a message only to the bot channel. Returns the posted message ts."""
    kwargs: dict = {"channel": config.mwm_bot_channel_id, "text": text}
    if persona:
        kwargs["username"] = persona.name
        kwargs["icon_emoji"] = persona.avatar_emoji
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    result = await client.chat_postMessage(**kwargs)
    return result.get("ts", "")


def create_slack_app(config: MwmConfig) -> AsyncApp:
    """Create and configure the Slack AsyncApp with Socket Mode."""
    app = AsyncApp(token=config.slack_bot_token)
    return app


def register_handlers(
    app: AsyncApp,
    session_mgr: SessionManager,
    config: MwmConfig,
    persona_names: set[str] | None = None,
) -> None:
    """Register all Slack event handlers.

    Args:
        persona_names: Names of personas running in this process.
            Messages from our own bot_id with these usernames are tracked
            as bot messages so other personas can see them.
    """
    own_persona_names = persona_names or set()

    # Track our own bot_id to identify self-messages
    _self_bot_id: str | None = None

    @app.event("message")
    async def handle_message(event: dict, client) -> None:
        nonlocal _self_bot_id
        """Route incoming messages based on channel and content."""
        # Lazily fetch our own bot_id
        if _self_bot_id is None:
            try:
                auth = await client.auth_test()
                _self_bot_id = auth.get("bot_id", "")
                logger.info("Own bot_id: %s", _self_bot_id)
            except Exception:
                _self_bot_id = ""

        channel = event.get("channel", "")
        text = event.get("text", "")
        user = event.get("user", "unknown")
        subtype = event.get("subtype")
        bot_id = event.get("bot_id")

        # Ignore message edits, deletes, etc.
        if subtype in ("message_changed", "message_deleted"):
            return

        ts = event.get("ts", "")

        # --- Bot channel ---
        if channel == config.mwm_bot_channel_id:
            # Session management commands
            if text.startswith("!session start-free "):
                await _handle_session_start_free(text, client, config, session_mgr)
                return

            if text.startswith("!session start "):
                await _handle_session_start(text, client, config, session_mgr)
                return

            if text.strip() == "!session end":
                session_mgr.end_session()
                await safe_post(client, config, "セッションを終了しました。")
                return

            if text.strip() == "!session status":
                await _handle_session_status(client, config, session_mgr)
                return

            if text.strip() == "!moltbook":
                session_mgr.start_session("moltbook", config.mwm_bot_channel_id, mode="free")
                await safe_post(client, config, "Moltbook モードを開始しました。自由に議論します。")
                return

            # File attachments (PDF, VTT)
            files = event.get("files", [])
            for file_info in files:
                filetype = file_info.get("filetype", "")
                filename = file_info.get("name", "")

                if filetype == "pdf" or filename.endswith(".pdf"):
                    await _handle_pdf(file_info, client, config, session_mgr)

                elif filetype == "vtt" or filename.endswith(".vtt"):
                    await _handle_vtt(file_info, client, session_mgr)

            # Track bot messages (both from other bots AND our own personas)
            if bot_id:
                from datetime import datetime

                username = event.get("username", bot_id)
                msg = Message(
                    user=username,
                    text=text,
                    ts=ts,
                    timestamp=datetime.fromtimestamp(float(ts)) if ts else datetime.now(),
                    is_bot=True,
                    thread_ts=event.get("thread_ts", ""),
                )
                session_mgr.add_bot_message(msg)
                session_mgr.add_channel_message(msg)
                logger.info("Bot message from %s: %s", username, text[:50])

            # Track all bot channel messages for spontaneous context
            if not bot_id and not text.startswith("!"):
                from datetime import datetime

                thread_ts_val = event.get("thread_ts", "")
                msg = Message(
                    user=user,
                    text=text,
                    ts=ts,
                    timestamp=datetime.fromtimestamp(float(ts)) if ts else datetime.now(),
                    is_bot=False,
                    thread_ts=thread_ts_val,
                )
                session_mgr.add_channel_message(msg)
                logger.info("Human message in bot channel (thread_ts=%s): %s", thread_ts_val or "none", text[:50])
                # Include human thread replies in bot_messages so agents
                # can see and respond to thread engagement
                if thread_ts_val:
                    session_mgr.add_bot_message(msg)
                    logger.info("Added human thread reply to bot_messages: %s", text[:50])

        # --- Session channel (read only, no writing) ---
        elif session_mgr.is_session_channel(channel):
            from datetime import datetime

            msg = Message(
                user=user,
                text=text,
                ts=ts,
                timestamp=datetime.fromtimestamp(float(ts)) if ts else datetime.now(),
                is_bot=bool(bot_id),
            )
            session_mgr.add_discussion(channel, msg)
            logger.debug("Session discussion from %s: %s", user, text[:50])


async def _handle_session_start(
    text: str,
    client,
    config: MwmConfig,
    session_mgr: SessionManager,
) -> None:
    """Handle !session start <name> <channel_id> command."""
    parts = text.split(maxsplit=3)
    if len(parts) < 4:
        await safe_post(
            client,
            config,
            "使い方: `!session start <セッション名> <チャンネルID>`\n"
            "例: `!session start 機械学習の基礎 C0123456789`",
        )
        return

    session_name = parts[2]
    session_channel = parts[3].strip().strip("<>#")

    session_mgr.start_session(session_name, session_channel)

    # Fetch recent history from the session channel
    history_count = await _backfill_channel_history(client, session_channel, session_mgr)

    await safe_post(
        client,
        config,
        f"セッション「{session_name}」を開始しました。\n"
        f"チャンネル: <#{session_channel}> を監視中（過去メッセージ {history_count} 件取得済み）。",
    )


async def _handle_session_start_free(
    text: str,
    client,
    config: MwmConfig,
    session_mgr: SessionManager,
) -> None:
    """Handle !session start-free <name> <channel_id> command."""
    parts = text.split(maxsplit=3)
    if len(parts) < 4:
        await safe_post(
            client,
            config,
            "使い方: `!session start-free <セッション名> <チャンネルID>`\n"
            "例: `!session start-free 自由議論 C0123456789`",
        )
        return

    session_name = parts[2]
    session_channel = parts[3].strip().strip("<>#")

    session_mgr.start_session(session_name, session_channel, mode="free")

    # Fetch recent history from the session channel
    history_count = await _backfill_channel_history(client, session_channel, session_mgr)

    await safe_post(
        client,
        config,
        f"フリーセッション「{session_name}」を開始しました（自律議論モード）。\n"
        f"チャンネル: <#{session_channel}> を監視中（過去メッセージ {history_count} 件取得済み）。",
    )


async def _handle_session_status(
    client,
    config: MwmConfig,
    session_mgr: SessionManager,
) -> None:
    """Handle !session status command."""
    session = session_mgr.current_session
    if not session:
        await safe_post(client, config, "現在アクティブなセッションはありません。")
        return

    await safe_post(
        client,
        config,
        f"*セッション: {session.name}*\n"
        f"モード: {session.mode}\n"
        f"チャンネル: <#{session.channel_id}>\n"
        f"スライド: {len(session.slide_texts)} ページ\n"
        f"トランスクリプト: {len(session.transcript_chunks)} チャンク\n"
        f"議論メッセージ: {len(session.discussion_messages)} 件\n"
        f"bot メッセージ: {len(session.bot_messages)} 件",
    )


async def _backfill_channel_history(
    client,
    channel_id: str,
    session_mgr: SessionManager,
    limit: int = 50,
) -> int:
    """Fetch recent messages from a channel and add them as discussion messages."""
    from datetime import datetime

    try:
        result = await client.conversations_history(channel=channel_id, limit=limit)
        messages = result.get("messages", [])
        count = 0
        for msg in reversed(messages):  # oldest first
            if msg.get("subtype"):
                continue
            m = Message(
                user=msg.get("user", "unknown"),
                text=msg.get("text", ""),
                ts=msg.get("ts", ""),
                timestamp=datetime.fromtimestamp(float(msg["ts"])) if msg.get("ts") else datetime.now(),
                is_bot=bool(msg.get("bot_id")),
                thread_ts=msg.get("thread_ts", ""),
            )
            session_mgr.add_discussion(channel_id, m)
            count += 1
        logger.info("Backfilled %d messages from channel %s", count, channel_id)
        return count
    except Exception:
        logger.exception("Failed to backfill history from %s", channel_id)
        return 0


async def _handle_pdf(
    file_info: dict,
    client,
    config: MwmConfig,
    session_mgr: SessionManager,
) -> None:
    """Download and process a PDF file."""
    filename = file_info.get("name", "unknown.pdf")
    logger.info("Processing PDF: %s", filename)

    pdf_bytes = await download_file_from_slack(client, file_info)
    if not pdf_bytes:
        return

    slide_texts = extract_slide_texts(pdf_bytes)
    session_mgr.add_slides(slide_texts)

    await safe_post(
        client,
        config,
        f"PDF「{filename}」を読み込みました（{len(slide_texts)}ページ）。",
    )


async def _handle_vtt(
    file_info: dict,
    client,
    session_mgr: SessionManager,
) -> None:
    """Download and process a VTT transcript file."""
    filename = file_info.get("name", "unknown.vtt")
    logger.info("Processing VTT: %s", filename)

    vtt_bytes = await download_file_from_slack(client, file_info)
    if not vtt_bytes:
        return

    vtt_content = vtt_bytes.decode("utf-8", errors="replace")
    entries = parse_vtt(vtt_content)

    for entry in entries:
        text = f"{entry.speaker}: {entry.text}" if entry.speaker else entry.text
        session_mgr.add_transcript(text, source="vtt")

    logger.info("Processed %d VTT entries from %s", len(entries), filename)
