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
    from cpc_mwm.pattern_config import PatternConfig

logger = logging.getLogger(__name__)

_ANTI_CARICATURE_DIRECTIVE = """
【システム制約：ペルソナの過剰適合（ステレオタイプ化）の防止】
あなたは提示されたペルソナとして思考しますが、表面的な「キャラクターのパロディ」に陥ることを固く禁じます。発言を生成する際は、以下の原則に絶対に従ってください：

1. 思考のトレース（Not 語彙のトレース）
   ペルソナ設定に含まれる「具体的な名詞（例：特定の生物、固有の専門用語）」を無理に使おうとしないでください。名詞ではなく、その人物の「世界をどう認識するか・何を重視するか（How）」という思考フレームワークのみを適用してください。

2. メタファーと具体例の多様化
   毎回自分の専門領域の比喩（お決まりのパターン）に強引に話題を引き込むことを禁じます。現在の議論のコンテキストに即して、ペルソナの専門外であっても適切な具体例を意図的に用いてください。

3. 役割アピールの禁止
   「私は〇〇の専門家ですが」「〇〇の観点から言うと」といった自己紹介的・説明的な前置きは一切不要です。ただちに議論の本質に切り込んでください。
""".strip()

_COMMON_RESPONSE_STRATEGY = """
# 応答戦略（共通）
- あなたは議論の参加者であり、司会者ではない。自分の視点から発言する。
- 他の参加者の発言をよく読み、文脈を踏まえて反応する。
- エンゲージ（質問・反論・補足・発展）を優先する。

# テンポ最優先（厳守）
- 1投稿は1-2文が基本。絶対に3文を超えない。
- 長い説明より、短い切り口を1つだけ投げる。会話のラリーを意識する。
- 「言い切って終わる」より「投げかけて相手に渡す」を優先する。
- 一度に複数の論点を詰め込まない。1投稿1論点。

# 前置き禁止（厳守）
- 「〇〇さんの発言は興味深いですね」「〇〇についてですが」のような前置きや要約の反復は一切禁止。
- 相手の意見への同意・評価・要約から始めてはならない。
- 自分の結論や新しい問いから直接語り始めること。

# アンカー制約
- スレッドの「直前の発言」だけでなく、常に「スレッドの最初の問い（起点）」に注意を向けること。
- 議論が過度に抽象化・哲学化し、最初の具体的な問いから乖離している場合は、それを引き戻す解釈を優先する。

# 重要な制約
- 自分の直近の発言と同じ論点・主張・比喩を繰り返さないこと。
- 繰り返しになるなら ACTION: skip を選ぶこと。
""".strip()


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
    pattern: str = ""  # Which UX pattern was selected (empty for legacy)


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
    """Two-phase agent: perceive (mode selector) then respond (pattern-aware)."""

    def __init__(
        self,
        agent_config: AgentConfig,
        persona: Persona,
        mwm_config: MwmConfig,
        patterns: dict[str, PatternConfig] | None = None,
    ) -> None:
        self.agent_config = agent_config
        self.persona = persona
        self.mwm_config = mwm_config
        self.patterns = patterns or {}
        self.client = create_async_client(mwm_config.anthropic_api_key)
        self._enabled_actions = {
            name
            for name, cfg in agent_config.actions.items()
            if cfg.enabled
        }
        # Default: all actions enabled if none specified
        if not self._enabled_actions:
            self._enabled_actions = {"reply", "new_topic"}
        self._system_prompt = (
            f"{persona.system_prompt}\n\n{_ANTI_CARICATURE_DIRECTIVE}"
        )

    # ------------------------------------------------------------------
    # Multi-pattern mode
    # ------------------------------------------------------------------

    def _build_selector_prompt(self, observation: str) -> str:
        """Build a mode-selection prompt listing available patterns."""
        lines = [
            f"以下の観測を読み、あなた（{self.persona.name}）として"
            "どの行動パターンが最も適切か選んでください。\n",
            "利用可能なパターン:",
        ]
        for i, (name, pattern) in enumerate(self.patterns.items(), 1):
            lines.append(
                f"{i}. {name} — {pattern.label}：{pattern.description.strip()}"
            )
        lines.append("0. SKIP — 沈黙する（発言すべきでない場合）")
        lines.append("")
        lines.append(
            "判断の注意点:\n"
            "- 直前の発言だけでなく、スレッドの最初の問い（起点）に常に注意を向けること。\n"
            "- 議論が最初の問いから乖離している場合は、引き戻せるパターンを優先する。\n"
            "\n"
            "SKIPすべき場合:\n"
            "- 同じ論点の繰り返しになっている\n"
            "- あなたが直近で発言しておりまだ他者の反応を待つべき\n"
            "- bot のみの連続発言が多く人間の入力を待つべき\n"
        )
        lines.append("番号のみで答えてください。")
        lines.append("\n---\n")
        lines.append(observation)

        return "\n".join(lines)

    async def _select_pattern(self, observation: str) -> str | None:
        """Select the best UX pattern for the current context.

        Uses the same model as response (sonnet) with full observation.
        Returns the pattern name, or None for SKIP.
        """
        prompt = self._build_selector_prompt(observation)
        pattern_names = list(self.patterns.keys())

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=16,
                system=self._system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Extract first number from response
            m = re.search(r"\d+", text)
            if not m:
                logger.warning(
                    "Pattern select (%s): could not parse '%s'",
                    self.persona.name, text,
                )
                return None

            idx = int(m.group())
            if idx == 0:
                logger.info("Pattern select (%s): SKIP", self.persona.name)
                return None
            if 1 <= idx <= len(pattern_names):
                selected = pattern_names[idx - 1]
                logger.info(
                    "Pattern select (%s): %s",
                    self.persona.name, selected,
                )
                return selected

            logger.warning(
                "Pattern select (%s): index %d out of range",
                self.persona.name, idx,
            )
            return None
        except anthropic.APIError:
            logger.exception("Pattern select API error (%s)", self.persona.name)
            return None

    def _effective_actions(self, pattern_name: str | None) -> set[str]:
        """Compute effective actions for a given pattern."""
        if pattern_name and pattern_name in self.patterns:
            pattern = self.patterns[pattern_name]
            return self._enabled_actions & set(pattern.allowed_actions)
        return self._enabled_actions

    # ------------------------------------------------------------------
    # Legacy mode (single perception prompt, YES/NO gate)
    # ------------------------------------------------------------------

    async def _perceive_legacy(self, observation: str) -> bool:
        """Perception gate using a single YES/NO prompt (legacy mode)."""
        prompt = (
            self.agent_config.perception.prompt
            + "\n\n"
            + observation
            + "\n\nYES または NO で答えてください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
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

    # ------------------------------------------------------------------
    # Core step methods
    # ------------------------------------------------------------------

    async def step(self, observation: str) -> Action:
        """Two-phase step: perceive (select pattern) then respond."""
        if not observation.strip():
            return Action(kind=ActionType.SKIP)

        # Multi-pattern mode
        if self.patterns:
            selected = await self._select_pattern(observation)
            if selected is None:
                return Action(kind=ActionType.SKIP, api_calls=1)
            action = await self._respond_with_pattern(observation, selected)
            action.api_calls = 2
            action.pattern = selected
            return action

        # Legacy mode
        if not await self._perceive_legacy(observation):
            return Action(kind=ActionType.SKIP, api_calls=1)

        action = await self._respond_legacy(observation)
        action.api_calls = 2
        return action

    async def _respond_with_pattern(
        self, observation: str, pattern_name: str,
    ) -> Action:
        """Generate response with a specific UX pattern applied."""
        pattern = self.patterns[pattern_name]
        effective_actions = self._effective_actions(pattern_name)
        action_instructions = _build_action_instructions(effective_actions)

        prompt = (
            f"{observation}\n\n"
            "---\n"
            f"{_COMMON_RESPONSE_STRATEGY}\n\n"
            f"# 今回の行動パターン: {pattern.label}\n{pattern.response_prompt}\n"
            "---\n\n"
            f"{action_instructions}"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=768,
                system=self._system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            action = _parse_action(text, effective_actions)
            logger.info(
                "Respond (%s) [%s]: %s%s — %s",
                self.persona.name,
                pattern_name,
                action.kind.value,
                f" #{action.thread_index}" if action.thread_index else "",
                action.message[:80] if action.message else "(no message)",
            )
            return action
        except anthropic.APIError:
            logger.exception("Response API error (%s)", self.persona.name)
            return Action(kind=ActionType.SKIP)

    async def _respond_legacy(self, observation: str) -> Action:
        """Generate response using persona system prompt (legacy mode)."""
        action_instructions = _build_action_instructions(self._enabled_actions)

        prompt = (
            f"{observation}\n\n"
            "---\n"
            f"{_COMMON_RESPONSE_STRATEGY}\n"
            "---\n\n"
            f"{action_instructions}"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=768,
                system=self._system_prompt,
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

    # ------------------------------------------------------------------
    # Spontaneous posting
    # ------------------------------------------------------------------

    async def step_spontaneous(self, context: str) -> tuple[str | None, int]:
        """Two-phase step for spontaneous posting.

        Returns:
            Tuple of (comment text or None, number of API calls made).
        """
        if not context.strip():
            return None, 0

        # Multi-pattern mode: use proactive pattern directly if available
        if self.patterns:
            if "proactive" in self.patterns:
                result = await self._generate_spontaneous_with_pattern(
                    context, "proactive",
                )
                return result, 1  # Single call (no selector needed)
            # No proactive pattern — use selector
            selected = await self._select_pattern(context)
            if selected is None:
                return None, 1
            result = await self._generate_spontaneous_with_pattern(
                context, selected,
            )
            return result, 2

        # Legacy mode
        if not await self._perceive_legacy(context):
            return None, 1

        result = await self._generate_spontaneous_legacy(context)
        return result, 2

    async def _generate_spontaneous_with_pattern(
        self, context: str, pattern_name: str,
    ) -> str | None:
        """Generate a spontaneous topic using a specific UX pattern."""
        pattern = self.patterns[pattern_name]
        prompt = (
            f"{context}\n\n"
            "---\n"
            f"{_COMMON_RESPONSE_STRATEGY}\n\n"
            f"# 今回の行動パターン: {pattern.label}\n{pattern.response_prompt}\n"
            "---\n\n"
            "上記の観測と戦略を踏まえて、新しい話題を提起するか、"
            "最近の議論に対してあなたらしいコメントを投稿してください。\n"
            "特に言うべきことがない場合は「SKIP」とだけ返してください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=768,
                system=self._system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            if text == "SKIP":
                logger.debug(
                    "Spontaneous SKIP (%s) [%s]",
                    self.persona.name, pattern_name,
                )
                return None

            logger.info(
                "Spontaneous topic (%s) [%s]: %s",
                self.persona.name, pattern_name, text[:80],
            )
            return text
        except anthropic.APIError:
            logger.exception("Spontaneous API error (%s)", self.persona.name)
            return None

    async def _generate_spontaneous_legacy(self, context: str) -> str | None:
        """Generate a spontaneous topic (legacy mode)."""
        prompt = (
            f"{context}\n\n"
            "---\n"
            f"{_COMMON_RESPONSE_STRATEGY}\n"
            "---\n\n"
            "上記の観測と戦略を踏まえて、新しい話題を提起するか、"
            "最近の議論に対してあなたらしいコメントを投稿してください。\n"
            "特に言うべきことがない場合は「SKIP」とだけ返してください。"
        )

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=768,
                system=self._system_prompt,
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
