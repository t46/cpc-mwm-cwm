from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from agent_utils.persona import Persona, load_persona
from cpc_mwm.brain import Brain
from cpc_mwm.config import MwmConfig
from cpc_mwm.session import SessionManager
from cpc_mwm.slack_app import create_slack_app, register_handlers, safe_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def periodic_response(
    app,
    session_mgr: SessionManager,
    brain: Brain,
    config: MwmConfig,
    persona: Persona,
) -> None:
    """Periodically generate and post comments."""
    # Random initial delay (0-60s) to stagger multiple bots
    initial_delay = random.uniform(0, 60)
    logger.info(
        "[%s] Periodic response task started (interval=%ds, initial_delay=%.0fs)",
        persona.name,
        config.response_interval_seconds,
        initial_delay,
    )
    await asyncio.sleep(initial_delay)
    while True:
        # Add jitter (±30s) to avoid synchronized posting
        jitter = random.uniform(-30, 30)
        # Use shorter interval for free discussion mode
        if session_mgr.current_session and session_mgr.current_session.mode == "free":
            interval = config.free_discussion_interval_seconds
        else:
            interval = config.response_interval_seconds
        await asyncio.sleep(interval + jitter)

        if not session_mgr.current_session:
            continue

        if not session_mgr.has_enough_new_context(persona_name=persona.name):
            logger.debug("[%s] Not enough new context, skipping", persona.name)
            continue

        context = session_mgr.get_context_for_prompt(persona_name=persona.name)
        comment = await brain.generate_comment(context)

        if comment:
            try:
                thread_ts = session_mgr.pick_thread_target(persona.name, config)
                await safe_post(
                    app.client, config, comment,
                    persona=persona, thread_ts=thread_ts,
                )
                now = datetime.now()
                session_mgr.current_session.last_bot_post_at = now
                session_mgr.current_session._persona_last_post_at[persona.name] = now
                session_mgr.record_api_call()
                if thread_ts:
                    logger.info("[%s] Posted comment to bot channel (thread)", persona.name)
                else:
                    logger.info("[%s] Posted comment to bot channel", persona.name)
            except Exception:
                logger.exception("[%s] Failed to post comment", persona.name)


async def spontaneous_posting(
    app,
    session_mgr: SessionManager,
    brain: Brain,
    config: MwmConfig,
    persona: Persona,
) -> None:
    """Periodically generate spontaneous topics even without a session."""
    initial_delay = random.uniform(60, 180)
    logger.info(
        "[%s] Spontaneous posting task started (interval=%ds)",
        persona.name,
        config.spontaneous_interval_seconds,
    )
    await asyncio.sleep(initial_delay)
    while True:
        jitter = random.uniform(-300, 300)
        await asyncio.sleep(max(60, config.spontaneous_interval_seconds + jitter))

        # Skip if presentation session is active (let reactive handle it)
        if session_mgr.current_session and session_mgr.current_session.mode == "presentation":
            continue

        if not session_mgr.has_spontaneous_opportunity(config):
            continue

        if not session_mgr.can_make_api_call(config):
            logger.debug("[%s] Daily API call limit reached, skipping", persona.name)
            continue

        context = session_mgr.get_spontaneous_context()
        comment = await brain.generate_spontaneous_topic(context)
        session_mgr.record_api_call()

        if comment:
            try:
                await safe_post(app.client, config, comment, persona=persona)
                session_mgr.record_spontaneous_post()
                logger.info("[%s] Posted spontaneous topic", persona.name)
            except Exception:
                logger.exception("[%s] Failed to post spontaneous topic", persona.name)


def load_personas(config: MwmConfig) -> list[Persona]:
    """Load personas from config. Uses persona_files if set, otherwise persona_file."""
    whitepaper_content = ""
    if config.whitepaper_path:
        whitepaper_content = Path(config.whitepaper_path).read_text()
    if config.persona_files:
        paths = [p.strip() for p in config.persona_files.split(",") if p.strip()]
    else:
        paths = [config.persona_file]
    return [load_persona(p, whitepaper_content=whitepaper_content) for p in paths]


async def main(whitepaper_override: str | None = None) -> None:
    """Entry point for the camp bot."""
    config = MwmConfig()
    if whitepaper_override:
        config.whitepaper_path = whitepaper_override
    personas = load_personas(config)

    for p in personas:
        logger.info("Loaded persona: %s (%s)", p.name, p.style)

    app = create_slack_app(config)
    session_mgr = SessionManager()
    persona_names = {p.name for p in personas}

    # Create a Brain for each persona
    brains = [Brain(config, persona) for persona in personas]

    # Register Slack event handlers (once, shared)
    register_handlers(app, session_mgr, config, persona_names=persona_names)

    # Start audio capture if enabled
    if config.enable_audio:
        from cpc_mwm.audio_capture import AudioTranscriber

        transcriber = AudioTranscriber(config)

        async def on_transcript(text: str) -> None:
            session_mgr.add_transcript(text, source="audio")

        asyncio.create_task(transcriber.start(on_transcript))
        logger.info("Audio capture enabled (device=%s)", config.audio_device or "default")

    # Start periodic response and spontaneous posting tasks for each persona
    for brain, persona in zip(brains, personas):
        asyncio.create_task(periodic_response(app, session_mgr, brain, config, persona))
        asyncio.create_task(spontaneous_posting(app, session_mgr, brain, config, persona))

    # Start Slack Socket Mode connection
    handler = AsyncSocketModeHandler(app, config.slack_app_token)
    names = ", ".join(p.name for p in personas)
    logger.info("Starting bot(s): %s", names)
    await handler.start_async()


def cli() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="MWM — マルチエージェント議論 bot")
    parser.add_argument(
        "--whitepaper",
        default=None,
        help="ホワイトペーパーファイルパス（ペルソナに注入）",
    )
    args = parser.parse_args()
    asyncio.run(main(whitepaper_override=args.whitepaper))


if __name__ == "__main__":
    cli()
