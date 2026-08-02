"""Replyer - 双阶段决策器（AmaidesuDecider）的 Stage 2 回复生成器。

设计原则（人设分离承诺）：
- Planner（Stage 1）：**零人设**，只决定"要不要回复 / 回复谁 / 聊什么"，输出 DecisionPlan。
- Replyer（Stage 2，本模块）：**注入人设**，根据 plan + 弹幕批次 + 人设生成实际回复，
  输出 Intent `{speech, emotion, action, action_parameters}`。
- 两者使用不同的 LLM 客户端：Planner 用快速模型（llm_fast），Replyer 用高质量模型（llm）。

职责边界：
- 只生成并**返回** Intent 对象，**不发布事件**（事件发布由 AmaidesuDecider 在 Task 10 统一负责）。
- **不调用 tools**（不做 function calling，纯文本 JSON 输出）。
- 复用 AmaidesuDecider 既有模式：`_clean_llm_json` 三步清理、情绪降级 neutral、动作白名单校验。

与 AmaidesuDecider 的关系：
- 本类是一个"纯函数式"的 Stage 2 组件，由 AmaidesuDecider 持有并在 Task 10 编排调用。
- 能力白名单逻辑（`_ensure_capabilities` / `_build_action`）与 amaidesu_decider.py 保持一致，
  但在此独立实现，避免双向耦合。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from src.modules.events.event_bus import EventBus
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.time_utils import now_ms
from src.modules.types import Intent, IntentAction, IntentEmotion, IntentMetadata
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.capabilities import CapabilitiesProvider, UnifiedCapabilitiesView

from .message_buffer import MessageBuffer
from .plan import DecisionPlan

# 默认人设兜底值（persona dict 缺字段时使用）
_DEFAULT_BOT_NAME = "爱德丝"
_DEFAULT_PERSONALITY = "活泼开朗，有些调皮，喜欢和直播间观众互动"
_DEFAULT_STYLE_CONSTRAINTS = "口语化、简短，像在直播间和观众聊天，避免机械式回复"

# Replyer 模板名（Task 3 产出，含 $personality/$style_constraints/$bot_name 人设注入）
_REPLYER_TEMPLATE = "decision/amaidesu_replyer"


class Replyer:
    """Stage 2 回复生成器：消费 DecisionPlan + 弹幕 + 人设，产出 Intent。

    不发布事件、不调用 tools。`generate()` 返回 Optional[Intent]，
    由上层 AmaidesuDecider 负责 `event_bus.emit(decision.intent.generated)`。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        llm_service: LLMManager,
        prompt_service: PromptManager,
        event_bus: EventBus,
        context_service: Any = None,
        capabilities_provider: Optional[CapabilitiesProvider] = None,
    ) -> None:
        """初始化 Replyer。

        Args:
            config: 配置字典（兼容 AmaidesuDecider.ConfigSchema 的子集字段），
                    读取 replyer_client / enable_action_selection / bot_name。
            llm_service: LLM 管理器（使用 replyer_client 指定的高质量客户端）。
            prompt_service: 提示词管理器（渲染 amaidesu_replyer 模板）。
            event_bus: 事件总线（保留参数，generate 不直接使用，由 Decider 发布）。
            context_service: 上下文服务（可选，当前未使用，预留）。
            capabilities_provider: Output 能力提供者（可选，用于动作白名单校验）。
        """
        self._config: Dict[str, Any] = config or {}
        self.replyer_client: str = self._config.get("replyer_client", "llm")
        self._enable_action_selection: bool = self._config.get("enable_action_selection", True)
        self._bot_name: str = self._config.get("bot_name", _DEFAULT_BOT_NAME)

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._event_bus = event_bus
        self._context_service = context_service
        self._capabilities_provider = capabilities_provider
        self.logger = get_logger("Replyer")

        # 能力快照（首次 generate 时惰性加载并缓存）
        self._capabilities_loaded: bool = False
        self._valid_action_names: set[str] = set()
        self._action_list_str: str = ""

    async def generate(
        self,
        plan: DecisionPlan,
        batch: List[NormalizedMessage],
        persona: Dict[str, Any],
    ) -> Optional[Intent]:
        """根据 Planner 的决策计划 + 弹幕批次 + 人设，生成实际回复 Intent。

        流程：
        1. 注入人设：render 'decision/amaidesu_replyer'（含 $personality/$style_constraints/$bot_name）。
        2. 调用高质量 LLM（replyer_client，默认 llm），**不传 tools**。
        3. 清理 + 解析 JSON → {text, emotion, action, action_parameters}。
        4. 组装 Intent：情绪降级 neutral、动作白名单校验（非法丢弃保留 speech）。
        5. 返回 Intent（不发布事件，由 AmaidesuDecider 负责）。

        Args:
            plan: Planner 产出的决策计划（should_reply=True 时才应到达此处）。
            batch: 本批弹幕（NormalizedMessage 列表）。
            persona: 人设字典（bot_name / personality / style_constraints）。

        Returns:
            Intent 实例；LLM 异常或解析失败时返回 None（silent 降级）。
        """
        # 防御：Planner 已裁决 should_reply=True 才会进入此处；False 直接放弃。
        if not plan.should_reply:
            self.logger.debug("DecisionPlan.should_reply=False，Replyer 跳过生成")
            return None

        # 惰性加载能力快照（用于动作白名单）
        self._ensure_capabilities()

        # ① 注入人设 + 决策计划 + 弹幕上下文，渲染 Replyer prompt
        prompt = self._render_prompt(plan, batch, persona)

        # ② 调用高质量 LLM（无 tools）
        try:
            self.logger.info(f"Replyer 生成回复中 (plan.target={plan.target!r}, client={self.replyer_client})")
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.replyer_client,
            )
        except Exception as e:
            self.logger.error(f"Replyer LLM 调用异常: {e}", exc_info=True)
            return None

        # 兼容真实 LLMResponse(.success/.content) 与测试 mock(直接字符串)
        content = self._extract_content(response)
        if content is None:
            self.logger.warning("Replyer LLM 返回失败或空内容，silent 降级")
            return None

        # ③ 清理 + JSON 解析
        cleaned_json = _clean_llm_json(content)
        try:
            parsed_data = json.loads(cleaned_json)
        except json.JSONDecodeError as e:
            self.logger.error(f"Replyer JSON 解析失败: {e}, 清理后内容: {cleaned_json[:200]}")
            return None

        speech = (parsed_data.get("text", "") or parsed_data.get("speech", "")).strip()
        if not speech:
            self.logger.info("Replyer LLM 返回空 text，silent 降级")
            return None

        # ④ 组装 Intent（情绪降级 + 动作白名单）
        intent = self._create_intent(parsed_data, speech, plan)
        self.logger.info(f"Replyer 生成回复: {speech}")
        return intent

    # ==================== prompt 渲染（人设注入核心） ====================

    def _render_prompt(
        self,
        plan: DecisionPlan,
        batch: List[NormalizedMessage],
        persona: Dict[str, Any],
    ) -> str:
        """渲染 Replyer prompt，注入人设三件套 + 计划 + 弹幕。

        人设分离承诺的另一半：$personality / $style_constraints / $bot_name 必须传给模板。
        """
        return self._prompt_service.render_safe(
            _REPLYER_TEMPLATE,
            bot_name=persona.get("bot_name", self._bot_name),
            personality=persona.get("personality", _DEFAULT_PERSONALITY),
            style_constraints=persona.get("style_constraints", _DEFAULT_STYLE_CONSTRAINTS),
            plan=_render_plan_text(plan),
            danmaku_batch=_render_batch_text(batch),
            action_list=self._action_list_str or "（当前无可用动作，action 请留空字符串）",
        )

    # ==================== Intent 组装（情绪降级 + 动作白名单） ====================

    def _create_intent(
        self,
        parsed_data: Dict[str, Any],
        speech: str,
        plan: DecisionPlan,
    ) -> Intent:
        """从解析后的 JSON 构造 Intent（speech + emotion + 经能力校验的 action）。

        与 amaidesu_decider.py._create_intent 同构：
        - 非法 emotion → 降级 neutral（12 枚举校验由 IntentEmotion field_validator 强制）。
        - 非法 action（不在白名单）→ 丢弃 action，保留 speech。
        """
        emotion_raw = str(parsed_data.get("emotion", "neutral")).lower()
        try:
            emotion_obj: Optional[IntentEmotion] = IntentEmotion(name=emotion_raw, intensity=0.5)
        except Exception:
            self.logger.warning(f"Replyer 情绪 '{emotion_raw}' 不在枚举中，降级为 neutral")
            emotion_obj = IntentEmotion(name="neutral", intensity=0.5)

        action_obj = self._build_action(parsed_data)

        return Intent(
            emotion=emotion_obj,
            action=action_obj,
            speech=speech,
            metadata=IntentMetadata(
                source_id="amaidesu",
                decision_time_ms=now_ms(),
                source_message_id=plan.target,
            ),
        )

    def _build_action(self, parsed_data: Dict[str, Any]) -> Optional[IntentAction]:
        """从 LLM 输出构造并校验 IntentAction。

        - 期望 `action` 为全限定名 `<handler>.<local_action>`（来自能力清单）。
        - 启用动作选择且已加载能力时，对动作名做白名单校验，非法则丢弃（保留 speech）。
        - `action_parameters` 必须为 dict，否则忽略参数。
        """
        action_raw = str(parsed_data.get("action", "")).strip()
        if not action_raw:
            return None

        if self._enable_action_selection and self._valid_action_names:
            if action_raw not in self._valid_action_names:
                self.logger.warning(f"Replyer 选择的动作 '{action_raw}' 不在可用能力清单中，丢弃")
                return None

        raw_params = parsed_data.get("action_parameters") or parsed_data.get("parameters") or {}
        parameters = raw_params if isinstance(raw_params, dict) else {}
        if raw_params and not isinstance(raw_params, dict):
            self.logger.warning(f"action_parameters 非对象（{type(raw_params).__name__}），忽略参数")

        try:
            return IntentAction(name=action_raw, parameters=parameters)
        except Exception as e:
            self.logger.warning(f"构造 IntentAction 失败（name={action_raw!r}）：{e}")
            return None

    # ==================== 能力快照（惰性加载，与 amaidesu_decider 一致） ====================

    def _ensure_capabilities(self) -> None:
        """惰性加载并缓存 Output 能力快照（首次 generate 时调用一次）。"""
        if self._capabilities_loaded:
            return
        self._capabilities_loaded = True

        if not self._enable_action_selection or self._capabilities_provider is None:
            return

        try:
            view = self._capabilities_provider.get_all_capabilities()
        except Exception as e:
            self.logger.warning(f"Replyer 查询 Output 能力失败，动作选择降级为禁用：{e}")
            return

        self._valid_action_names = {entry.name for entry in view.actions}
        self._action_list_str = _format_action_list(view)
        self.logger.info(f"Replyer 已加载 {len(self._valid_action_names)} 个可用动作供选择")

    # ==================== LLM 响应归一化 ====================

    @staticmethod
    def _extract_content(response: Any) -> Optional[str]:
        """从 LLM 返回值中提取文本内容。

        兼容两种形态：
        - 真实 LLMResponse 对象（生产）：检查 .success，取 .content。
        - 直接字符串（测试 mock / QA 场景）：原样返回。

        失败（success=False 或无 content）返回 None。
        """
        # 字符串形态（测试 mock 直接返回 JSON 字符串）
        if isinstance(response, str):
            return response

        # LLMResponse 对象形态
        success = getattr(response, "success", True)
        if not success:
            return None
        content = getattr(response, "content", None)
        return content


# ==================== 模块级辅助函数 ====================


def _clean_llm_json(raw_output: str) -> str:
    """清理 LLM 返回的 JSON 字符串（与 AmaidesuDecider._clean_llm_json 一致的三步清理）。

    独立为模块级函数以避免与 AmaidesuDecider 产生双向依赖。
    """
    cleaned = raw_output.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        cleaned = cleaned[first_brace : last_brace + 1]

    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    return cleaned


def _render_plan_text(plan: DecisionPlan) -> str:
    """把 DecisionPlan 渲染为供 prompt 使用的可读文本。"""
    target = plan.target or "（无特定目标，面向全体观众）"
    return (
        f"target: {target}\n"
        f"topic_summary: {plan.topic_summary or '（无特定话题）'}\n"
        f"reply_guidance: {plan.reply_guidance or '（无额外指引）'}\n"
        f"confidence: {plan.confidence:.2f}"
    )


def _render_batch_text(batch: List[NormalizedMessage]) -> str:
    """把弹幕批次渲染为文本（复用 MessageBuffer.render_batch_text）。"""
    if not batch:
        return "（本批无弹幕）"
    return MessageBuffer.render_batch_text(batch)


def _format_action_list(view: UnifiedCapabilitiesView) -> str:
    """把能力视图渲染为供 prompt 使用的动作清单文本。

    与 AmaidesuDecider._format_action_list 同构（静态方法提升为模块函数）。
    """
    lines: List[str] = []
    for entry in view.actions:
        param_parts: List[str] = []
        for pname, spec in entry.parameters.items():
            seg = f"{pname}:{spec.type}"
            if spec.minimum is not None or spec.maximum is not None:
                seg += f"[{spec.minimum}~{spec.maximum}]"
            if spec.default is not None:
                seg += f"=默认{spec.default}"
            param_parts.append(seg)
        params_str = f"（参数: {', '.join(param_parts)}）" if param_parts else ""
        desc = entry.description or ""
        lines.append(f"- {entry.name}: {desc}{params_str}")
    return "\n".join(lines)


__all__ = ["Replyer"]
