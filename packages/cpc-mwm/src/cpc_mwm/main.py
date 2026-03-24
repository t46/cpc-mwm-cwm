from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

import yaml

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from agent_utils.persona import load_persona
from cpc_mwm.agent import ActionType, Agent
from cpc_mwm.agent_config import load_agent_config
from cpc_mwm.pattern_config import load_patterns
from cpc_mwm.config import MwmConfig
from cpc_mwm.fep_agent import FEPAgent
from cpc_mwm.fep_agent_config import load_fep_agent_config
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
    agent: Agent,
    config: MwmConfig,
) -> None:
    """Periodically observe and act based on agent config."""
    persona = agent.persona
    initial_delay = random.uniform(0, 60)
    logger.info(
        "[%s] Periodic response task started (interval=%ds, initial_delay=%.0fs)",
        persona.name,
        config.response_interval_seconds,
        initial_delay,
    )
    await asyncio.sleep(initial_delay)
    while True:
        jitter = random.uniform(-30, 30)
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

        # Build observation and let the agent decide (perceive + respond)
        observation = session_mgr.build_observation(persona.name, config)
        action = await agent.step(observation)
        for _ in range(action.api_calls):
            session_mgr.record_api_call()

        if action.kind == ActionType.SKIP or not action.message:
            continue

        try:
            thread_ts = ""
            if action.kind == ActionType.REPLY and action.thread_index is not None:
                candidates = session_mgr.get_thread_candidates(persona.name, config)
                idx = action.thread_index - 1  # 1-based to 0-based
                if 0 <= idx < len(candidates):
                    thread_ts = candidates[idx].ts
                else:
                    logger.warning(
                        "[%s] Invalid thread index %d (candidates: %d)",
                        persona.name, action.thread_index, len(candidates),
                    )

            post_text = action.message
            if action.pattern:
                post_text = f"[{action.pattern}] {post_text}"

            posted_ts = await safe_post(
                app.client, config, post_text,
                persona=persona, thread_ts=thread_ts,
            )
            now = datetime.now()
            session_mgr.current_session.last_bot_post_at = now
            session_mgr.current_session._persona_last_post_at[persona.name] = now
            # Track own post in bot_messages so it appears as thread candidate
            from agent_utils.models import Message as Msg
            own_msg = Msg(
                user=persona.name,
                text=action.message,
                ts=posted_ts,
                timestamp=now,
                is_bot=True,
                thread_ts=thread_ts,
            )
            session_mgr.add_bot_message(own_msg)
            if thread_ts:
                logger.info("[%s] Posted to thread", persona.name)
            else:
                logger.info("[%s] Posted new topic", persona.name)
        except Exception:
            logger.exception("[%s] Failed to post", persona.name)


async def spontaneous_posting(
    app,
    session_mgr: SessionManager,
    agent: Agent,
    config: MwmConfig,
) -> None:
    """Periodically generate spontaneous topics even without a session."""
    persona = agent.persona
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

        if session_mgr.current_session and session_mgr.current_session.mode == "presentation":
            continue

        if not session_mgr.has_spontaneous_opportunity(config):
            continue

        if not session_mgr.can_make_api_call(config):
            logger.debug("[%s] Daily API call limit reached, skipping", persona.name)
            continue

        context = session_mgr.get_spontaneous_context()
        comment, api_calls, pattern_name = await agent.step_spontaneous(context)
        for _ in range(api_calls):
            session_mgr.record_api_call()

        if comment:
            try:
                post_text = comment
                if pattern_name:
                    post_text = f"[{pattern_name}] {post_text}"
                await safe_post(app.client, config, post_text, persona=persona)
                session_mgr.record_spontaneous_post()
                logger.info("[%s] Posted spontaneous topic", persona.name)
            except Exception:
                logger.exception("[%s] Failed to post spontaneous topic", persona.name)


def _is_fep_config(path: str | Path) -> bool:
    """Check if a YAML config file is an FEP agent config."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return "generative_model" in raw


def load_agents(
    config: MwmConfig,
    session_mgr: SessionManager | None = None,
) -> list[Agent | FEPAgent]:
    """Load agents from YAML config files."""
    whitepaper_content = ""
    if config.whitepaper_path:
        wp_path = Path(config.whitepaper_path)
        if wp_path.is_file():
            whitepaper_content = wp_path.read_text()

    if config.agent_configs:
        paths = [p.strip() for p in config.agent_configs.split(",") if p.strip()]
    else:
        paths = [config.agent_config]

    # Separate FEP configs from standard configs
    fep_paths = []
    standard_paths = []
    for path in paths:
        if _is_fep_config(path):
            fep_paths.append(path)
        else:
            standard_paths.append(path)

    # Load standard agent configs first to collect all pattern names
    agent_configs = []
    for path in standard_paths:
        agent_configs.append(load_agent_config(path))

    # Load shared patterns
    all_pattern_names: set[str] = set()
    for agent_cfg in agent_configs:
        all_pattern_names.update(agent_cfg.patterns)

    shared_patterns = {}
    if all_pattern_names:
        patterns_dir = Path("patterns")
        shared_patterns = load_patterns(list(all_pattern_names), patterns_dir)
        logger.info(
            "Loaded %d patterns: %s",
            len(shared_patterns),
            ", ".join(shared_patterns.keys()),
        )

    # Build agents with their pattern subsets
    agents: list[Agent | FEPAgent] = []
    for agent_cfg in agent_configs:
        persona = load_persona(agent_cfg.persona, whitepaper_content=whitepaper_content)
        agent_patterns = {
            k: shared_patterns[k]
            for k in agent_cfg.patterns
            if k in shared_patterns
        }
        agents.append(Agent(agent_cfg, persona, config, patterns=agent_patterns))

    # Load FEP agents
    for path in fep_paths:
        fep_cfg = load_fep_agent_config(path)
        persona = load_persona(fep_cfg.persona, whitepaper_content=whitepaper_content)
        if session_mgr is None:
            raise ValueError("FEP agents require a SessionManager — pass session_mgr to load_agents()")
        agents.append(FEPAgent(fep_cfg, persona, config, session_mgr))
    return agents


async def main(whitepaper_override: str | None = None, agent_config_override: str | None = None) -> None:
    """Entry point for the camp bot."""
    config = MwmConfig()
    if whitepaper_override:
        config.whitepaper_path = whitepaper_override
    if agent_config_override:
        config.agent_config = agent_config_override
        config.agent_configs = ""  # CLI override takes precedence

    session_mgr = SessionManager()
    agents = load_agents(config, session_mgr=session_mgr)

    for agent in agents:
        logger.info("Loaded agent: %s (%s)", agent.persona.name, agent.persona.style)

    app = create_slack_app(config)
    persona_names = {a.persona.name for a in agents}

    register_handlers(app, session_mgr, config, persona_names=persona_names)

    if config.enable_audio:
        from cpc_mwm.audio_capture import AudioTranscriber

        transcriber = AudioTranscriber(config)

        async def on_transcript(text: str) -> None:
            session_mgr.add_transcript(text, source="audio")

        asyncio.create_task(transcriber.start(on_transcript))
        logger.info("Audio capture enabled (device=%s)", config.audio_device or "default")

    for agent in agents:
        asyncio.create_task(
            periodic_response(app, session_mgr, agent, config)
        )
        asyncio.create_task(
            spontaneous_posting(app, session_mgr, agent, config)
        )
        if isinstance(agent, FEPAgent):
            asyncio.create_task(agent.run_reflection_loop())

    handler = AsyncSocketModeHandler(app, config.slack_app_token)
    names = ", ".join(a.persona.name for a in agents)
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
    parser.add_argument(
        "--agent-config",
        default=None,
        help="エージェント設定 YAML ファイルパス（デフォルト: agents/ada.yml）",
    )
    args = parser.parse_args()
    asyncio.run(main(whitepaper_override=args.whitepaper, agent_config_override=args.agent_config))


if __name__ == "__main__":
    cli()
