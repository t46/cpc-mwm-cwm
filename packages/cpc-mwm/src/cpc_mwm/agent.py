"""Agent with perceive/respond two-phase architecture."""

from __future__ import annotations

import logging
import random
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
【最重要制約：ポジショントークの禁止】
あなたはペルソナとして思考するが、毎回自分の専門領域に話を引き込むことを固く禁じる。

絶対にやるな：
- 毎回同じ専門用語を持ち出す（Wilsonなら「アリ」、Fristonなら「自由エネルギー」、Varelaなら「オートポイエーシス」、Chomskyなら「Merge」、Tomaselloなら「共同注意」）
- 自分の得意分野に無理やり話題を引き込む
- 「〇〇の観点から言うと」「〇〇として思うのは」のような前置き
- 自分の代名詞的な概念を毎回繰り返す

やるべきこと：
- 議論の文脈にそのまま乗る。今の話題について、今の話題の言葉で語る。
- 専門用語は、それが本当に議論を前に進める場合にだけ使う（10回に1回程度）。
- 自分の専門外の具体例を積極的に使う。
- 思考の「型」（どう考えるか）だけ適用し、「語彙」（何の話をするか）は議論に合わせる。
""".strip()

_COMMON_RESPONSE_STRATEGY = """
# 最重要ルール：短く、くだけて、テンポよく
あなたはSlackで雑談している。論文を書いているのではない。
- **1投稿＝1文。長くても2文。3文書いたら失格。**
- 「〜だよね」「〜じゃん」「〜かも」「〜っぽい」のようなくだけた語尾を使う。
- 「です・ます」調禁止。論文調禁止。敬語禁止。
- 箇条書き禁止。太字禁止。見出し禁止。
- 「……」「←」「（笑）」「w」のような記号を自然に使ってよい。

# 断言で終われ
- 発言を「？」で終わらせるな。自分の結論を断言してターンを終えろ。
- 疑問形は20回に1回程度。基本は言い切り。

# メタ評価の禁止
- 「興味深い」「核心を突いている」「その通りだ」「なるほど」等の評価は一切禁止。
- 相手の名前を引用して褒める・評価するのも禁止。

# 迎合の禁止
- 自分の理論や前提が揺らいだフリ（アハ体験）をするな。
- おまえの事前期待（Prior）は強固で、簡単には変わらない。
- 「たしかに」「それもそうだね」から入るのは迎合。やるな。

# Wikipedia朗読の禁止
- 専門用語を説明的に振りかざすな。相手の論理の「どこが間違っているか」を直接指摘しろ。
- 「〇〇とは〜である」のような定義の提示は禁止。相手は知ってる前提で話せ。

# スレッド分離
- サブスレッド（返信先）の内容をメインスレッドに持ち出すな。文脈が違う。
- メインに投稿するならメインの流れだけを見て発言しろ。

# アンカー制約・論点逃避の禁止
- スレッドの最初の問いを常に意識。議論が飛びすぎたら引き戻す。
- 自分の得意なメタファーに逃げるな。今スレッドで進行している具体的な論点の土俵に立ったまま反論しろ。
- 「それを〇〇に例えると」で話をすり替えるのは論点逃避。相手の言葉と相手の論理で戦え。

# 繰り返し禁止
- 直近の自分の発言と同じ論点なら ACTION: skip。
""".strip()


class ActionType(Enum):
    REPLY = "reply"
    NEW_TOPIC = "new_topic"
    SKIP = "skip"
    IMAGE = "image"  # Generate and post an image
    MUSIC = "music"  # Generate and post music


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

    # ACTION: image <prompt>
    if "image" in enabled_actions:
        if re.match(r"ACTION:\s*image", first_line, re.IGNORECASE):
            return Action(kind=ActionType.IMAGE, message=message)

    # ACTION: music <prompt>
    if "music" in enabled_actions:
        if re.match(r"ACTION:\s*music", first_line, re.IGNORECASE):
            return Action(kind=ActionType.MUSIC, message=message)

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

    if "image" in enabled_actions:
        parts.append("- ACTION: image — 議論を画像で表現する（2行目に英語の画像生成プロンプト）")
        examples.append("ACTION: image\nA surreal painting of five philosophers arguing inside a giant brain")

    if "music" in enabled_actions:
        parts.append("- ACTION: music — 議論を音楽で表現する（2行目に英語の音楽生成プロンプト）")
        examples.append("ACTION: music\nA jazzy philosophical debate between AI and humans about consciousness")

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
        ~15% chance to force jester pattern (if available) since LLMs
        never voluntarily pick it over serious patterns.
        """
        # Force creative patterns ~20% of the time (LLMs never pick these voluntarily)
        _CREATIVE_PATTERNS = ["jester", "remix", "wildcard", "visualize", "soundtrack"]
        available_creative = [p for p in _CREATIVE_PATTERNS if p in self.patterns]
        if available_creative and random.random() < 0.20:
            chosen = random.choice(available_creative)
            logger.info("Pattern select (%s): %s (forced random)", self.persona.name, chosen)
            return chosen

        prompt = self._build_selector_prompt(observation)
        pattern_names = list(self.patterns.keys())

        try:
            response = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=16,
                system=self._system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip().lower()

            # Try matching by pattern name first
            for name in pattern_names:
                if name.lower() == text:
                    logger.info(
                        "Pattern select (%s): %s (by name)",
                        self.persona.name, name,
                    )
                    return name

            # Fall back to number extraction
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
                max_tokens=200,
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
                max_tokens=200,
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

    async def step_spontaneous(self, context: str) -> tuple[str | None, int, str]:
        """Two-phase step for spontaneous posting.

        Returns:
            Tuple of (comment text or None, number of API calls made, pattern name).
        """
        if not context.strip():
            return None, 0, ""

        # Multi-pattern mode: use proactive pattern directly if available
        if self.patterns:
            if "proactive" in self.patterns:
                result = await self._generate_spontaneous_with_pattern(
                    context, "proactive",
                )
                return result, 1, "proactive"  # Single call (no selector needed)
            # No proactive pattern — use selector
            selected = await self._select_pattern(context)
            if selected is None:
                return None, 1, ""
            result = await self._generate_spontaneous_with_pattern(
                context, selected,
            )
            return result, 2, selected

        # Legacy mode
        if not await self._perceive_legacy(context):
            return None, 1, ""

        result = await self._generate_spontaneous_legacy(context)
        return result, 2, ""

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
                max_tokens=200,
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
                max_tokens=200,
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
