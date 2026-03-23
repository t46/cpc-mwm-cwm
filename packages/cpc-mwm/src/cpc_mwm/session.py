from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from agent_utils.models import Message
from cpc_mwm.config import MwmConfig

logger = logging.getLogger(__name__)


@dataclass
class TranscriptChunk:
    timestamp: datetime
    text: str
    source: str  # "audio" or "vtt"


@dataclass
class Session:
    name: str
    channel_id: str
    mode: str = "presentation"
    slide_texts: list[str] = field(default_factory=list)
    transcript_chunks: list[TranscriptChunk] = field(default_factory=list)
    discussion_messages: list[Message] = field(default_factory=list)
    bot_messages: list[Message] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    last_bot_post_at: datetime | None = None
    consecutive_bot_only_count: int = 0
    _last_context_hash: str = field(default="", repr=False)
    # Per-persona tracking for multi-persona mode
    _persona_last_post_at: dict[str, datetime] = field(default_factory=dict, repr=False)
    _persona_context_hash: dict[str, str] = field(default_factory=dict, repr=False)


class SessionManager:
    """Manages the lifecycle of presentation sessions."""

    def __init__(self) -> None:
        self.current_session: Session | None = None
        self._sessions: dict[str, Session] = {}
        self.channel_history: list[Message] = []
        self._daily_api_calls: int = 0
        self._daily_api_calls_date: date | None = None
        self._spontaneous_posts_today: int = 0
        self._last_spontaneous_at: datetime | None = None

    def start_session(self, name: str, channel_id: str, mode: str = "presentation") -> Session:
        """Start a new session, ending any current one."""
        if self.current_session:
            logger.info("Ending previous session: %s", self.current_session.name)
        session = Session(name=name, channel_id=channel_id, mode=mode)
        self._sessions[channel_id] = session
        self.current_session = session
        logger.info("Started session: %s (channel: %s, mode: %s)", name, channel_id, mode)
        return session

    def end_session(self) -> None:
        """End the current session."""
        if self.current_session:
            logger.info("Ended session: %s", self.current_session.name)
            self.current_session = None

    def is_session_channel(self, channel_id: str) -> bool:
        """Check if a channel is a tracked session channel."""
        return (
            self.current_session is not None
            and self.current_session.channel_id == channel_id
        )

    def add_transcript(self, text: str, source: str = "audio") -> None:
        """Add a transcript chunk to the current session."""
        if not self.current_session or not text.strip():
            return
        chunk = TranscriptChunk(
            timestamp=datetime.now(),
            text=text.strip(),
            source=source,
        )
        self.current_session.transcript_chunks.append(chunk)
        self.current_session.consecutive_bot_only_count = 0
        logger.debug("Added transcript chunk (%s): %s", source, text[:50])

    def add_slides(self, slide_texts: list[str]) -> None:
        """Set slide texts for the current session."""
        if not self.current_session:
            return
        self.current_session.slide_texts = slide_texts
        logger.info("Added %d slides to session", len(slide_texts))

    def add_discussion(self, channel_id: str, msg: Message) -> None:
        """Add a discussion message from a session channel."""
        if not self.current_session or self.current_session.channel_id != channel_id:
            return
        self.current_session.discussion_messages.append(msg)
        self.current_session.consecutive_bot_only_count = 0

    def add_bot_message(self, msg: Message) -> None:
        """Add a bot channel message to the current session."""
        if not self.current_session:
            return
        self.current_session.bot_messages.append(msg)
        self.current_session.consecutive_bot_only_count += 1
        # Also append to channel_history
        self.channel_history.append(msg)
        self.channel_history = self.channel_history[-50:]

    def add_channel_message(self, msg: Message) -> None:
        """Always append to channel_history regardless of session state."""
        self.channel_history.append(msg)
        self.channel_history = self.channel_history[-50:]

    def has_enough_new_context(self, persona_name: str = "") -> bool:
        """Check if there's enough new context since the last bot post to warrant a response.

        Args:
            persona_name: If provided, uses per-persona tracking instead of shared state.
        """
        session = self.current_session
        if not session:
            return False

        is_free = session.mode == "free"

        # Need at least slides or transcript (skip in free mode)
        if not is_free:
            if not session.slide_texts and not session.transcript_chunks:
                return False

        # Check if context has changed since last response (skip in free mode)
        if not is_free:
            current_hash = self._compute_context_hash(session)
            last_hash = (
                session._persona_context_hash.get(persona_name, "")
                if persona_name
                else session._last_context_hash
            )
            if current_hash == last_hash:
                return False

        # Get the last post time for this persona
        last_post_at = (
            session._persona_last_post_at.get(persona_name)
            if persona_name
            else session.last_bot_post_at
        )

        # If never posted, post if we have some content
        if last_post_at is None:
            if is_free:
                return True
            return len(session.transcript_chunks) >= 3 or len(session.slide_texts) > 0

        # Check if bot-only discussion has gone on too long
        max_bot_exchanges = 20
        if len(session.bot_messages) >= max_bot_exchanges:
            recent_bot = session.bot_messages[-max_bot_exchanges:]
            oldest_bot_ts = recent_bot[0].timestamp
            # Any human messages or transcript since the oldest of those bot messages?
            has_human_input = any(
                m.timestamp > oldest_bot_ts for m in session.discussion_messages
            ) or any(
                c.timestamp > oldest_bot_ts for c in session.transcript_chunks
            )
            if not has_human_input:
                logger.debug("Bot exchange limit reached (%d), waiting for new input", max_bot_exchanges)
                return False

        # Count new content since last post
        new_chunks = [
            c for c in session.transcript_chunks
            if c.timestamp > last_post_at
        ]
        new_discussion = [
            m for m in session.discussion_messages
            if m.timestamp > last_post_at
        ]
        new_bot_msgs = [
            m for m in session.bot_messages
            if m.timestamp > last_post_at
        ]
        # Respond if: new transcript, new discussion, or other bots said something
        return len(new_chunks) >= 3 or len(new_discussion) >= 1 or len(new_bot_msgs) >= 1


    def get_thread_candidates(self, persona_name: str, config: MwmConfig) -> list[Message]:
        """Get recent top-level messages eligible for thread engagement.

        Returns top-level messages within the age window that have human
        replies or are from other personas, with fewer than max_thread_replies.
        """
        session = self.current_session
        if not session or not session.bot_messages:
            return []

        now = datetime.now()
        max_age = config.thread_target_max_age_seconds

        candidates: list[Message] = []
        for msg in reversed(session.bot_messages):
            age = (now - msg.timestamp).total_seconds()
            if age > max_age:
                break
            # Only consider top-level messages (not thread replies)
            if msg.thread_ts or not msg.ts:
                continue

            replies = [m for m in session.bot_messages if m.thread_ts == msg.ts]
            reply_count = len(replies)
            if reply_count >= config.max_thread_replies:
                continue

            # Include if: from another persona, OR has human replies
            has_human_reply = any(not r.is_bot for r in replies)
            if msg.user != persona_name or has_human_reply:
                candidates.append(msg)

        return candidates

    def build_observation(self, persona_name: str, config: MwmConfig) -> str:
        """Build the observation text that the LLM sees to make decisions.

        Includes session context (slides, transcript, discussion) and
        a numbered list of thread candidates for engagement.
        """
        session = self.current_session
        if not session:
            return ""

        parts: list[str] = []

        # Slides
        if session.slide_texts:
            parts.append(f"## 発表スライド（{len(session.slide_texts)}ページ）")
            for i, text in enumerate(session.slide_texts, 1):
                if text.strip():
                    parts.append(f"### スライド {i}")
                    parts.append(text.strip())
            parts.append("")

        # Transcript (last 30 chunks max)
        if session.transcript_chunks:
            recent_transcript = session.transcript_chunks[-30:]
            parts.append(f"## トランスクリプト（最新{len(recent_transcript)}件）")
            for chunk in recent_transcript:
                ts = chunk.timestamp.strftime("%H:%M:%S")
                parts.append(f"[{ts}] {chunk.text}")
            parts.append("")

        # Discussion messages (last 20)
        if session.discussion_messages:
            recent_discussion = session.discussion_messages[-20:]
            parts.append(f"## セッションチャンネルの議論（最新{len(recent_discussion)}件）")
            for msg in recent_discussion:
                prefix = "[bot] " if msg.is_bot else ""
                parts.append(f"{prefix}{msg.user}: {msg.text}")
            parts.append("")

        # Bot channel messages (last 15), showing thread structure
        if session.bot_messages:
            recent_bot = session.bot_messages[-15:]
            parts.append(f"## bot チャンネルの議論（最新{len(recent_bot)}件）")
            for msg in recent_bot:
                if msg.thread_ts:
                    parts.append(f"  ↳ {msg.user}: {msg.text}")
                else:
                    parts.append(f"{msg.user}: {msg.text}")
            parts.append("")

        # Thread candidates
        candidates = self.get_thread_candidates(persona_name, config)
        if candidates:
            parts.append("## エンゲージ可能なスレッド")
            for i, msg in enumerate(candidates, 1):
                replies = [m for m in session.bot_messages if m.thread_ts == msg.ts]
                reply_count = len(replies)
                parts.append(f"{i}. {msg.user}: {msg.text[:150]}（返信{reply_count}件）")
                for reply in replies[-3:]:
                    parts.append(f"   ↳ {reply.user}: {reply.text[:100]}")
            parts.append("")
        else:
            parts.append("## エンゲージ可能なスレッド\nなし（新しいトピックを立てるか、SKIP してください）\n")

        # Own recent posts (for repetition avoidance)
        own_recent = [m for m in session.bot_messages if m.user == persona_name][-5:]
        if own_recent:
            parts.append(f"## あなた（{persona_name}）の直近の発言")
            for msg in own_recent:
                parts.append(f"- {msg.text[:200]}")
            parts.append("⚠ 上記と同じ論点・表現を繰り返さないでください。新しい角度や具体例で展開してください。")
            parts.append("")

        # Status info
        n = session.consecutive_bot_only_count
        parts.append(f"## 状態\n- bot のみの連続発言: {n}回\n- セッションモード: {session.mode}")

        # Update hash
        context = "\n".join(parts)
        new_hash = self._compute_context_hash(session)
        if persona_name:
            session._persona_context_hash[persona_name] = new_hash
        else:
            session._last_context_hash = new_hash
        return context

    def has_spontaneous_opportunity(self, config: MwmConfig) -> bool:
        """Check if conditions allow a spontaneous post."""
        # No spontaneous posts during active presentation sessions
        if self.current_session and self.current_session.mode == "presentation":
            return False

        # Check cooldown
        if self._last_spontaneous_at is not None:
            elapsed = (datetime.now() - self._last_spontaneous_at).total_seconds()
            if elapsed < config.spontaneous_interval_seconds:
                return False

        # Reset daily counters if date changed
        self._reset_daily_counters_if_needed()

        # Check daily spontaneous limit
        if self._spontaneous_posts_today >= config.max_daily_spontaneous_posts:
            return False

        # Check daily API limit
        if self._daily_api_calls >= config.max_daily_api_calls:
            return False

        return True

    def get_spontaneous_context(self) -> str:
        """Build context for spontaneous topic initiation from channel history."""
        parts: list[str] = []

        if self.channel_history:
            recent = self.channel_history[-50:]
            parts.append(f"## チャンネルの最近の発言（最新{len(recent)}件）")
            for msg in recent:
                prefix = "[bot] " if msg.is_bot else ""
                ts = msg.timestamp.strftime("%H:%M:%S")
                parts.append(f"[{ts}] {prefix}{msg.user}: {msg.text}")
            parts.append("")

        parts.append(
            "上記のチャンネルの流れを踏まえて、新しい話題を提起するか、"
            "最近の議論に対してあなたらしいコメントを投稿してください。\n"
            "特に言うべきことがない場合は「SKIP」とだけ返してください。"
        )

        return "\n".join(parts)

    def record_spontaneous_post(self) -> None:
        """Record that a spontaneous post was made."""
        self._reset_daily_counters_if_needed()
        self._spontaneous_posts_today += 1
        self._last_spontaneous_at = datetime.now()

    def record_api_call(self) -> None:
        """Record an API call, resetting daily counter if date changed."""
        self._reset_daily_counters_if_needed()
        self._daily_api_calls += 1

    def can_make_api_call(self, config: MwmConfig) -> bool:
        """Check if daily API call budget allows another call."""
        self._reset_daily_counters_if_needed()
        return self._daily_api_calls < config.max_daily_api_calls

    def _reset_daily_counters_if_needed(self) -> None:
        """Reset daily counters if the date has changed."""
        today = date.today()
        if self._daily_api_calls_date != today:
            self._daily_api_calls = 0
            self._spontaneous_posts_today = 0
            self._daily_api_calls_date = today

    @staticmethod
    def _compute_context_hash(session: Session) -> str:
        """Compute a hash of the current context to detect changes."""
        content = (
            str(len(session.transcript_chunks))
            + str(len(session.discussion_messages))
            + str(len(session.bot_messages))
            + str(len(session.slide_texts))
        )
        return hashlib.md5(content.encode()).hexdigest()
