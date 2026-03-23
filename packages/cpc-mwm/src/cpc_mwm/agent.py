"""Agent with perceive/respond two-phase architecture."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import anthropic
from agent_utils.claude_client import create_async_client

if TYPE_CHECKING:
    from agent_utils.persona import Persona
    from cpc_mwm.agent_config import AgentConfig
    from cpc_mwm.config import MwmConfig

logger = logging.getLogger(__name__)


class ActionType(Enum):
    REPLY = "reply"
    NEW_TOPIC = "new_topic"
    SKIP = "skip"


@dataclass
class Action:
    """Represents a decision made by the Agent."""

    kind: ActionType
    thread_index: int | None = None  # 1-based index for REPLY
    message: str = ""
    api_calls: int = 0  # Number of LLM API calls made to produce this action


def _parse_action(text: str, enabled_actions: set[str]) -> Action:
    """Parse the LLM's structured output into an Action."""
    lines = text.strip().split("\n", 1)
    first_line = lines[0].strip()
    message = lines[1].strip() if len(lines) > 1 else ""

    # ACTION: skip
    if re.match(r"ACTION:\s*skip", first_line, re.IGNORECASE):
        return Action(kind=ActionType.SKIP)

    # ACTION: reply <number>
    if "reply" in enabled_actions:
        m = re.match(r"ACTION:\s*reply\s+(\d+)", first_line, re.IGNORECASE)
        if m:
            return Action(
                kind=ActionType.REPLY,
                thread_index=int(m.group(1)),
                message=message,
            )

    # ACTION: new_topic
    if "new_topic" in enabled_actions:
        if re.match(r"ACTION:\s*new_topic", first_line, re.IGNORECASE):
            return Action(kind=ActionType.NEW_TOPIC, message=message)

    # Fallback: treat entire text as a new topic (backward compat)
    if text.strip() == "SKIP":
        return Action(kind=ActionType.SKIP)
    if "new_topic" in enabled_actions:
        return Action(kind=ActionType.NEW_TOPIC, message=text.strip())
    return Action(kind=ActionType.SKIP)


def _build_action_instructions(enabled_actions: set[str]) -> str:
    """Build ACTION format instructions based on enabled actions."""
    parts = ["以下のフォーマットで返答してください:", "1行目: アクション指定、2行目以降: 発言内容\n"]

    examples: list[str] = []

    if "reply" in enabled_actions:
        parts.append("- ACTION: reply <番号> — 既存スレッドへの返信")
        examples.append("ACTION: reply 2\nAdaの指摘は面白いですね。私はむしろ...")

    if "new_topic" in enabled_actions:
        parts.append("- ACTION: new_topic — 新しいトピックを立てる")
        examples.append("ACTION: new_topic\n一つ気になっていることがあります。...")

    parts.append("- ACTION: skip — 沈黙する（発言内容不要）")
    examples.append("ACTION: skip")

    parts.append("")
    for ex in examples:
        parts.append(f"例:\n{ex}\n")

    return "\n".join(parts)


class Agent:
    """Two-phase agent: perceive (haiku gate) then respond (sonnet generation)."""

    def __init__(
        self,
        agent_config: AgentConfig,
        persona: Persona,
        mwm_config: MwmConfig,
    ) -> None:
        self.agent_config = agent_config
        self.persona = persona
        self.mwm_config = mwm_config
        self.client = create_async_client(mwm_config.anthropic_api_key)
        self._enabled_actions = {
            name
            for name, cfg in agent_config.actions.items()
            if cfg.enabled
        }
        # Default: all actions enabled if none specified
        if not self._enabled_actions:
            self._enabled_actions = {"reply", "new_topic"}

    async def step(self, observation: str) -> Action:
        """Two-phase step: perceive then respond."""
        if not observation.strip():
            return Action(kind=ActionType.SKIP)

        if not await self.perceive(observation):
            return Action(kind=ActionType.SKIP, api_calls=1)

        action = await self.respond(observation)
        action.api_calls = 2  # perceive + respond
        return action

    async def perceive(self, observation: str) -> bool:
        """Cheap perception gate using haiku."""
        prompt = (
            self.agent_config.perception.prompt
            + "\n\n"
            + observation
            + "\n\nYES または NO で答えてください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.perception.model,
                max_tokens=16,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip().upper()
            result = "YES" in text
            logger.info(
                "Perceive (%s): %s",
                self.persona.name,
                "YES" if result else "NO",
            )
            return result
        except anthropic.APIError:
            logger.exception("Perception API error (%s)", self.persona.name)
            return False

    async def respond(self, observation: str) -> Action:
        """Generate response using sonnet with persona system prompt."""
        action_instructions = _build_action_instructions(self._enabled_actions)

        prompt = (
            f"{observation}\n\n"
            "---\n"
            f"# 応答戦略\n{self.agent_config.response.prompt}\n"
            "---\n\n"
            f"{action_instructions}"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.response.model,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            action = _parse_action(text, self._enabled_actions)
            logger.info(
                "Respond (%s): %s%s — %s",
                self.persona.name,
                action.kind.value,
                f" #{action.thread_index}" if action.thread_index else "",
                action.message[:80] if action.message else "(no message)",
            )
            return action
        except anthropic.APIError:
            logger.exception("Response API error (%s)", self.persona.name)
            return Action(kind=ActionType.SKIP)

    async def step_spontaneous(self, context: str) -> tuple[str | None, int]:
        """Two-phase step for spontaneous posting.

        Returns:
            Tuple of (comment text or None, number of API calls made).
        """
        if not context.strip():
            return None, 0

        if not await self.perceive(context):
            return None, 1

        result = await self._generate_spontaneous(context)
        return result, 2

    async def _generate_spontaneous(self, context: str) -> str | None:
        """Generate a spontaneous topic using the response model."""
        prompt = (
            f"{context}\n\n"
            "---\n"
            f"# 応答戦略\n{self.agent_config.response.prompt}\n"
            "---\n\n"
            "上記の観測と戦略を踏まえて、新しい話題を提起するか、"
            "最近の議論に対してあなたらしいコメントを投稿してください。\n"
            "特に言うべきことがない場合は「SKIP」とだけ返してください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.response.model,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            if text == "SKIP":
                logger.debug("Spontaneous SKIP (%s)", self.persona.name)
                return None

            logger.info(
                "Spontaneous topic (%s): %s",
                self.persona.name,
                text[:80],
            )
            return text
        except anthropic.APIError:
            logger.exception("Spontaneous API error (%s)", self.persona.name)
            return None
