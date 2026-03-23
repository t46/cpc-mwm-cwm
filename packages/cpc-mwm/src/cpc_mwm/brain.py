from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import anthropic
from agent_utils.claude_client import create_async_client

if TYPE_CHECKING:
    from cpc_mwm.config import MwmConfig
    from agent_utils.persona import Persona

logger = logging.getLogger(__name__)


class Brain:
    """Generates AI-powered comments using Claude API."""

    def __init__(self, config: MwmConfig, persona: Persona) -> None:
        self.client = create_async_client(config.anthropic_api_key)
        self.persona = persona
        self.config = config

    async def generate_comment(self, context: str) -> str | None:
        """Generate a comment based on session context.

        Args:
            context: The assembled context string from SessionManager.

        Returns:
            Comment text, or None if nothing worth saying.
        """
        if not context.strip():
            return None

        try:
            response = await self.client.messages.create(
                model=self.config.model_name,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": context}],
            )
            text = response.content[0].text.strip()

            if text == "SKIP":
                logger.debug("Brain decided to SKIP")
                return None

            logger.info(
                "Generated comment (%s): %s",
                self.persona.name,
                text[:80],
            )
            return text

        except anthropic.APIError:
            logger.exception("Claude API error")
            return None

    async def generate_spontaneous_topic(self, context: str) -> str | None:
        """Generate a spontaneous topic for moltbook mode.

        Args:
            context: The assembled context string from SessionManager.

        Returns:
            Topic text, or None if nothing worth saying.
        """
        if not context.strip():
            return None

        try:
            response = await self.client.messages.create(
                model=self.config.model_name,
                max_tokens=768,
                system=self.persona.system_prompt,
                messages=[{"role": "user", "content": context}],
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
