"""FEP (Free Energy Principle) agent with self-modifying policies."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import anthropic
from agent_utils.claude_client import create_async_client

from cpc_mwm.agent import (
    Action,
    ActionType,
    _ANTI_CARICATURE_DIRECTIVE,
    _build_action_instructions,
    _parse_action,
)
from cpc_mwm.fep_agent_config import FEPAgentConfig, save_fep_agent_config

if TYPE_CHECKING:
    from agent_utils.persona import Persona
    from cpc_mwm.config import MwmConfig
    from cpc_mwm.session import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class PredictionError:
    has_error: bool
    error_details: str = ""


@dataclass
class ReflectionTask:
    before_ctx: str
    cwm_context: str
    error: PredictionError
    action_text: str
    scheduled_at: datetime
    delay_seconds: int = 3600


class FEPAgent:
    """FEP agent: perceive prediction error, respond to minimize it, reflect and learn."""

    def __init__(
        self,
        fep_config: FEPAgentConfig,
        persona: Persona,
        mwm_config: MwmConfig,
        session_mgr: SessionManager,
    ) -> None:
        self.fep_config = fep_config
        self.persona = persona
        self.mwm_config = mwm_config
        self.session_mgr = session_mgr
        self.client = create_async_client(mwm_config.anthropic_api_key)
        self._enabled_actions = {"reply", "new_topic"}
        self._system_prompt = (
            f"{persona.system_prompt}\n\n{_ANTI_CARICATURE_DIRECTIVE}"
        )
        self._pending_reflections: list[ReflectionTask] = []
        self._config_lock = asyncio.Lock()

    async def step(self, observation: str) -> Action:
        """FEP perceive-respond cycle."""
        if not observation.strip():
            return Action(kind=ActionType.SKIP)

        cwm_context = self._get_cwm_context()

        async with self._config_lock:
            perception = await self._perceive(observation, cwm_context)

        if not perception.has_error:
            return Action(kind=ActionType.SKIP, api_calls=1)

        async with self._config_lock:
            action = await self._respond(observation, cwm_context, perception)
        action.api_calls = 2

        if action.kind != ActionType.SKIP and action.message:
            self._schedule_reflection(observation, cwm_context, perception, action.message)

        return action

    async def step_spontaneous(self, context: str) -> tuple[str | None, int]:
        """Spontaneous posting via FEP cycle."""
        if not context.strip():
            return None, 0

        cwm_context = self._get_cwm_context()

        async with self._config_lock:
            perception = await self._perceive(context, cwm_context)

        if not perception.has_error:
            return None, 1

        async with self._config_lock:
            action = await self._respond(context, cwm_context, perception)

        if action.kind == ActionType.SKIP or not action.message:
            return None, 2

        self._schedule_reflection(context, cwm_context, perception, action.message)
        return action.message, 2

    async def _perceive(self, observation: str, cwm_context: str) -> PredictionError:
        """Evaluate prediction error against generative model."""
        prompt = f"""あなたは以下のフィルターを通して世界を解釈します：
[CWMに対するスタンス]: {self.fep_config.cwm_stance}
[知覚方策]: {self.fep_config.perception_policy}

【入力データ】
[組織のCWM（過去の合意事項）]: {cwm_context}
[現在の生のスレッド]: {observation}

上記のフィルターを通して入力データを解釈し、あなたの理想とする世界とのズレを測ってください：
[生成モデル（理想の期待）]: {self.fep_config.generative_model}

予測誤差（Surprise）は発生していますか？
JSONで {{"has_error": true/false, "error_details": "ズレの具体的な内容(なければ空)"}} を返してください。
JSONのみを返し、それ以外のテキストは含めないでください。"""

        try:
            response = await self.client.messages.create(
                model=self.fep_config.model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Extract JSON from possible markdown code block
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                perception = PredictionError(
                    has_error=result.get("has_error", False),
                    error_details=result.get("error_details", ""),
                )
                logger.info(
                    "FEP Perceive (%s): %s — %s",
                    self.persona.name,
                    "ERROR" if perception.has_error else "OK",
                    perception.error_details[:80] if perception.error_details else "(no error)",
                )
                return perception
            logger.warning("FEP Perceive (%s): could not parse JSON from: %s", self.persona.name, text[:100])
            return PredictionError(has_error=False)
        except (anthropic.APIError, json.JSONDecodeError):
            logger.exception("FEP Perception error (%s)", self.persona.name)
            return PredictionError(has_error=False)

    async def _respond(
        self, observation: str, cwm_context: str, error: PredictionError
    ) -> Action:
        """Generate response using action_policy to minimize prediction error."""
        action_instructions = _build_action_instructions(self._enabled_actions)

        prompt = f"""[理想の期待]: {self.fep_config.generative_model}
[検出された予測誤差]: {error.error_details}

[CWMに対するスタンス]: {self.fep_config.cwm_stance}
[行動方策]: {self.fep_config.action_policy}

組織のCWM（{cwm_context}）と現在の議論（{observation}）を踏まえ、
方策に従ってこの予測誤差を最小化する（理想の世界に近づける）ための発言を生成してください。

---
# 重要な制約
- 自分の直近の発言と同じ論点・主張・比喩を繰り返さないこと。
- 新しい具体例、別の角度、または他者の発言への直接的な応答で展開すること。
- 繰り返しになるなら ACTION: skip を選ぶこと。
---

{action_instructions}"""

        try:
            response = await self.client.messages.create(
                model=self.fep_config.model,
                max_tokens=768,
                system=self._system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            action = _parse_action(text, self._enabled_actions)
            logger.info(
                "FEP Respond (%s): %s%s — %s",
                self.persona.name,
                action.kind.value,
                f" #{action.thread_index}" if action.thread_index else "",
                action.message[:80] if action.message else "(no message)",
            )
            return action
        except anthropic.APIError:
            logger.exception("FEP Response error (%s)", self.persona.name)
            return Action(kind=ActionType.SKIP)

    def _schedule_reflection(
        self, observation: str, cwm_context: str, error: PredictionError, action_text: str
    ) -> None:
        """Queue a reflection task to run after delay."""
        task = ReflectionTask(
            before_ctx=observation,
            cwm_context=cwm_context,
            error=error,
            action_text=action_text,
            scheduled_at=datetime.now(),
            delay_seconds=self.fep_config.reflection_delay,
        )
        self._pending_reflections.append(task)
        logger.info(
            "FEP Reflection scheduled (%s): will evaluate in %ds",
            self.persona.name,
            task.delay_seconds,
        )

    async def run_reflection_loop(self) -> None:
        """Background coroutine that processes pending reflections."""
        logger.info("[%s] FEP reflection loop started (check interval=60s)", self.persona.name)
        while True:
            await asyncio.sleep(60)
            now = datetime.now()
            due = [
                r for r in self._pending_reflections
                if (now - r.scheduled_at).total_seconds() >= r.delay_seconds
            ]
            for task in due:
                self._pending_reflections.remove(task)
                try:
                    await self._reflect_and_learn(task)
                except Exception:
                    logger.exception("FEP Reflection error (%s)", self.persona.name)

    async def _reflect_and_learn(self, task: ReflectionTask) -> None:
        """Evaluate whether action reduced prediction error; update policies if not."""
        after_ctx = self._get_cwm_context()
        if not after_ctx.strip():
            logger.info("FEP Reflection skipped (%s): no current context", self.persona.name)
            return

        # Re-evaluate prediction error with current context
        async with self._config_lock:
            new_perception = await self._perceive(after_ctx, task.cwm_context)

        self.session_mgr.record_api_call()

        if not new_perception.has_error:
            logger.info(
                "FEP Reflection (%s): prediction error resolved, no policy update needed",
                self.persona.name,
            )
            return

        # Error persists — ask LLM to propose policy updates
        logger.info(
            "FEP Reflection (%s): prediction error persists, proposing policy updates",
            self.persona.name,
        )

        prompt = f"""あなたは自律型AIエージェントの上位推論モジュール（メタ認知）です。
以下の予測誤差を消すために行動しましたが、失敗（予測誤差の残存・悪化）しました。

【前提のCWM】: {task.cwm_context}
[行動前のスレッド]: {task.before_ctx}
[あなたの行動]: {task.action_text}
[行動後の予測誤差]: {new_perception.error_details}

失敗の原因は以下のどれですか？
A. 認識論的スタンス（cwm_stanceのミス）：過去の共有知識を信じすぎた、または疑いすぎた
B. 知覚エラー（perception_policyのミス）：スレッドの文脈の解釈を間違えた
C. 運動エラー（action_policyのミス）：解釈は合っていたが、介入の戦術が悪かった

原因を推論し、次回の予測誤差を最小化するために改善した新しいポリシーを提案してください。

現在の設定:
cwm_stance: {self.fep_config.cwm_stance}
perception_policy: {self.fep_config.perception_policy}
action_policy: {self.fep_config.action_policy}

※ generative_model は不変のアイデンティティなので絶対に書き換えないでください。

以下のJSON形式で返してください（JSONのみ、他のテキストは含めない）：
{{"reasoning": "失敗の原因分析", "cwm_stance": "新しいCWMスタンス", "perception_policy": "新しい知覚方策", "action_policy": "新しい行動方策"}}"""

        try:
            response = await self.client.messages.create(
                model=self.fep_config.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            self.session_mgr.record_api_call()

            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if not json_match:
                logger.warning("FEP Reflection (%s): could not parse JSON", self.persona.name)
                return

            updates = json.loads(json_match.group())
            reasoning = updates.get("reasoning", "")
            logger.info("FEP Reflection (%s): %s", self.persona.name, reasoning[:120])

            async with self._config_lock:
                self.fep_config.cwm_stance = updates.get("cwm_stance", self.fep_config.cwm_stance)
                self.fep_config.perception_policy = updates.get("perception_policy", self.fep_config.perception_policy)
                self.fep_config.action_policy = updates.get("action_policy", self.fep_config.action_policy)
                save_fep_agent_config(self.fep_config)

            logger.info("FEP policies updated for %s", self.persona.name)

        except (anthropic.APIError, json.JSONDecodeError):
            logger.exception("FEP Reflection LLM error (%s)", self.persona.name)

    def _get_cwm_context(self) -> str:
        """Build CWM context from session history."""
        parts: list[str] = []
        if self.session_mgr.channel_history:
            recent = self.session_mgr.channel_history[-30:]
            parts.append("## Recent Discussion")
            for msg in recent:
                prefix = "[bot] " if msg.is_bot else ""
                parts.append(f"{prefix}{msg.user}: {msg.text}")
        session = self.session_mgr.current_session
        if session and session.discussion_messages:
            recent_discussion = session.discussion_messages[-20:]
            parts.append("## Session Discussion")
            for msg in recent_discussion:
                prefix = "[bot] " if msg.is_bot else ""
                parts.append(f"{prefix}{msg.user}: {msg.text}")
        return "\n".join(parts) if parts else ""
