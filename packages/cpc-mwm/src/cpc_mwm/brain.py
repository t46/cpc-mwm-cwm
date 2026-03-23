from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic
from agent_utils.claude_client import create_async_client

if TYPE_CHECKING:
    from cpc_mwm.config import MwmConfig
    from agent_utils.persona import Persona

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """Represents a decision made by the Brain."""

    kind: str  # "engage", "new", "skip"
    thread_index: int | None = None  # 1-based index into candidates (for "engage")
    message: str = ""


class Brain:
    """Generates AI-powered comments using Claude API."""

    def __init__(self, config: MwmConfig, persona: Persona) -> None:
        self.client = create_async_client(config.anthropic_api_key)
        self.persona = persona
        self.config = config

    async def decide_and_generate(
        self, observation: str, strategy: str,
    ) -> Action:
        """Observe the current state and decide what to do.

        The LLM receives the observation (context + thread candidates) and the
        strategy text, then outputs a structured action.

        Returns:
            An Action indicating what to do.
        """
        if not observation.strip():
            return Action(kind="skip")

        prompt = (
            f"{observation}\n\n"
            "---\n"
            f"# 戦略\n{strategy}\n"
            "---\n\n"
            "上記の観測と戦略を踏まえて、行動を決定してください。\n\n"
            "以下のフォーマットで返答してください:\n"
            "1行目: ACTION: engage <番号> / ACTION: new / ACTION: skip\n"
            "2行目以降: 発言内容（skip の場合は不要）\n\n"
            "例:\n"
            "ACTION: engage 2\n"
            "Adaの指摘は面白いですね。私はむしろ...\n\n"
            "例:\n"
            "ACTION: new\n"
            "一つ気になっていることがあります。...\n\n"
            "例:\n"
            "ACTION: skip"
        )

        try:
            response = await self.client.messages.create(
                model=self.config.model_name,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            action = self._parse_action(text)
            logger.info(
                "Action (%s): %s%s — %s",
                self.persona.name,
                action.kind,
                f" #{action.thread_index}" if action.thread_index else "",
                action.message[:80] if action.message else "(no message)",
            )
            return action

        except anthropic.APIError:
            logger.exception("Claude API error")
            return Action(kind="skip")

    async def generate_spontaneous_topic(self, context: str, strategy: str) -> str | None:
        """Generate a spontaneous topic for moltbook mode."""
        if not context.strip():
            return None

        prompt = (
            f"{context}\n\n"
            "---\n"
            f"# 戦略\n{strategy}\n"
            "---\n\n"
            "上記の観測と戦略を踏まえて、新しい話題を提起するか、"
            "最近の議論に対してあなたらしいコメントを投稿してください。\n"
            "特に言うべきことがない場合は「SKIP」とだけ返してください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.config.model_name,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            if text == "SKIP":
                logger.debug("Brain decided to SKIP")
                return None

            logger.info(
                "Generated spontaneous topic (%s): %s",
                self.persona.name,
                text[:80],
            )
            return text

        except anthropic.APIError:
            logger.exception("Claude API error")
            return None

    @staticmethod
    def _parse_action(text: str) -> Action:
        """Parse the LLM's structured output into an Action."""
        lines = text.strip().split("\n", 1)
        first_line = lines[0].strip()
        message = lines[1].strip() if len(lines) > 1 else ""

        # ACTION: skip
        if re.match(r"ACTION:\s*skip", first_line, re.IGNORECASE):
            return Action(kind="skip")

        # ACTION: engage <number>
        m = re.match(r"ACTION:\s*engage\s+(\d+)", first_line, re.IGNORECASE)
        if m:
            return Action(kind="engage", thread_index=int(m.group(1)), message=message)

        # ACTION: new
        if re.match(r"ACTION:\s*new", first_line, re.IGNORECASE):
            return Action(kind="new", message=message)

        # Fallback: treat entire text as a new topic message (backwards compat)
        if text.strip() == "SKIP":
            return Action(kind="skip")
        return Action(kind="new", message=text.strip())
