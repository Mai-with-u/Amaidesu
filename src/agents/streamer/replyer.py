"""Replyer - 主播 Agent 表达引擎（reply 工具的实现载体）

定位（Planner ReAct 架构下）：
- Planner 是决策主体，以 ReAct 循环运行（查信息 → 决定说不说 → 调 reply 工具）。
- Replyer 是 reply 工具的内部实现：被 Planner 的 reply 调用触发，注入人设，
  把决策意图（topic_summary / reply_guidance / target）渲染成实际回复。
- reply 工具契约由 ``tools/reply_tool.py`` 暴露；本类**不**注册为工具、
  **不**持有任何工具面（reply 是唯一 function 定义，纯结构化输出口）。

职责边界：
- 调用 LLMManager.call_tools(prompt, tools=[reply_fn_def])——LLM 只见 reply。
- 解析 response.tool_calls：reply call 取 speech/emotion/intensity；其余忽略。
- **敏感词净化**（输出端）：内置 ProfanityFilter 做"嘴"端净化——speech 输出前
  经词表过滤（替换或丢弃）。
- 两者使用不同的 LLM 客户端：Planner 用快速模型（llm_fast），Replyer 用高质量模型（llm）。

配置兼容：``replyer_client`` 配置键保留（向后兼容）；同时接受 ``replyer_llm``。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.types.emotion_vocab import Emotion

from .message_buffer import MessageBuffer
from .plan import DecisionPlan

# 默认人设兜底值（persona dict 缺字段时使用）
#
# 优先级链：persona dict 中的同名键（来自 config/core.toml 的 [persona] 段，由装配根
# main._register_agents_from_config 拉取后透传给 StreamerAgent.persona_provider，
# 再经 ReplyToolProvider._resolve_persona 解析后传给 Replyer.generate(persona=...)）
# > StreamerAgentConfig.bot_name（agents.toml 显式覆盖）
# > 本模块 _DEFAULT_* 常量（仅当 persona dict 完全缺失/字段缺位时兜底，避免冷启动崩）。
#
# _DEFAULT_BOT_NAME = '麦麦'、personality/style_constraints 文本与
# core_schemas.PersonaConfig 默认值对齐；不允许 config 模块反向依赖 agents 层，
# 故这里复制文本（保持依赖方向 agents → config 干净）。
_DEFAULT_BOT_NAME = "麦麦"
_DEFAULT_PERSONALITY = "活泼开朗，有些调皮，喜欢和观众互动"
_DEFAULT_STYLE_CONSTRAINTS = "口语化，使用网络流行语，避免机械式回复，适当使用emoji"

# Replyer 模板名（含 $personality/$style_constraints/$bot_name 人设注入）
_REPLYER_TEMPLATE = "amaidesu_replyer"

# reply function 名称（Agent 内部协议工具，与 tools/reply_tool.py 的 _REPLY_TOOL_NAME 对齐）
_REPLY_FUNCTION_NAME = "reply"


class Replyer:
    """表达引擎：消费 DecisionPlan + 弹幕 + 人设，生成实际回复。

    通过标准 function calling 协议一次性产出 speech + emotion + actions；
    返回 ``Optional[dict]``，由 reply_tool 包装后返回给 LLM。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        llm_service: Any,
        prompt_service: Any,
        tool_registry: Optional[Any] = None,
        profanity_filter: Optional["ProfanityFilter"] = None,
    ) -> None:
        """初始化 Replyer。

        Args:
            config: 配置字典（兼容 StreamerAgentConfig 的子集字段），
                    读取 replyer_llm / replyer_client / bot_name。
            llm_service: LLM 管理器（使用 replyer_llm 指定的高质量客户端）。
            prompt_service: 提示词管理器（渲染 amaidesu_replyer 模板）。
            tool_registry: 工具注册表（可选，用于收集动作工具的 function 定义）。
            profanity_filter: 敏感词过滤器（输出端净化入口；None 表示不净化）。
        """
        self._config: Dict[str, Any] = config or {}
        # replyer_llm（新 agents_schemas 命名）+ replyer_client（向后兼容）
        self.replyer_llm: str = self._config.get("replyer_llm", self._config.get("replyer_client", "llm"))
        self._bot_name: str = self._config.get("bot_name", _DEFAULT_BOT_NAME)

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._tool_registry = tool_registry
        self._profanity_filter = profanity_filter
        self.logger = get_logger("Replyer")

    async def generate(
        self,
        plan: DecisionPlan,
        batch: List[Any],
        persona: Dict[str, Any],
        history: Optional[List[Any]] = None,
        agenda: Optional[str] = None,
        on_delta: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """根据 Planner 的决策计划 + 弹幕批次 + 人设，生成实际回复。

        流程：注入人设渲染 'amaidesu_replyer'（含 $personality/$style_constraints/
        $bot_name）→ 调用高质量 LLM（replyer_llm，**call_tools 标准接口**，
        tools=[reply_fn_def] + action_fn_defs）→ 解析 response.tool_calls 提取
        reply(speech/emotion) 与 actions → 情绪降级 neutral → 敏感词净化 →
        返回 dict（不发布事件；reply_tool 负责 ToolExecutionResult 包装）。

        Args:
            plan: Planner 产出的决策计划（should_reply=True 时才应到达此处）。
            batch: 本批弹幕（NormalizedMessage 列表）。
            persona: 人设字典（bot_name / personality / style_constraints）。
            history: 可选的最近会话历史（鸭子类型对象列表，需有 ``role`` 和 ``content`` 属性）；
                     role 可能是枚举（取 ``.value``），content 是 str。None 表示无历史可用，
                     渲染为占位文本。
            agenda: 当前 Agenda 的渲染文本（可选）。由调用方（如 StreamerAgent
                主循环）从 ``AgendaState`` 拼装后传入，描述当前环节的
                title / task_description / key_points / 环节剩余时长 + 整场进度。
                透传到 prompt 的 ``$agenda`` 变量。
            on_delta: 思考流回调（LLM 层形态 (kind, text_delta)；ADR-008）。

        Returns:
            Dict 实例（含 speech/emotion/actions/metadata）；LLM 异常、tool_calls 缺失
            reply call、或 speech 为空时返回 None（silent 降级）。
            reply_tool 直接将此 dict 包装进 ToolExecutionResult 返回给 LLM。
        """
        # 防御：Planner 已裁决 should_reply=True 才会进入此处；False 直接放弃。
        if not plan.should_reply:
            self.logger.debug("DecisionPlan.should_reply=False，Replyer 跳过生成")
            return None

        # 注入人设 + 决策意图 + 弹幕上下文 + 会话历史 + Agenda 上下文，渲染 prompt
        prompt = self._render_prompt(plan, batch, persona, history, agenda)

        # reply 是唯一工具——表达引擎不持有信息/动作工具面
        tools = [self._build_reply_function_def()]

        try:
            self.logger.info(f"Replyer 生成回复中 (plan.target={plan.target!r}, client={self.replyer_llm})")
            response = await self._llm_service.call_tools(
                prompt=prompt,
                tools=tools,
                client_type=self.replyer_llm,
                on_delta=on_delta,
            )
        except Exception as e:
            self.logger.error(f"Replyer LLM 调用异常: {e}", exc_info=True)
            return None

        if not getattr(response, "success", False):
            self.logger.warning(f"Replyer LLM 返回失败: {getattr(response, 'error', 'unknown')}, silent 降级")
            return None

        speech, emotion_name, emotion_intensity = self._parse_tool_calls(getattr(response, "tool_calls", None))

        if not speech:
            self.logger.info("Replyer LLM 未返回 reply 或 speech 为空，silent 降级")
            return None

        # 情绪校验：合法枚举保留，非枚举降级 neutral
        valid_emotion_names = {e.value for e in Emotion}
        if emotion_name not in valid_emotion_names:
            self.logger.warning(f"Replyer 情绪 '{emotion_name}' 不在枚举中，降级为 neutral")
            emotion_name = "neutral"

        # actions 恒空——动作执行归 Planner ReAct 循环的 registry 工具调用
        result = {
            "speech": speech,
            "emotion": {
                "name": emotion_name,
                "intensity": emotion_intensity,
            },
            "actions": [],
            "metadata": {
                "source_id": "streamer_agent",
                "target": plan.target,
                "topic_summary": plan.topic_summary,
                "reply_guidance": plan.reply_guidance,
                "confidence": plan.confidence,
            },
        }

        # ⑦ 敏感词净化（净化职责归 Replyer 表达引擎）
        result = self._apply_profanity_filter(result)
        if result is None:
            self.logger.warning("Replyer 输出被 profanity filter 丢弃（drop_on_match=True）")
            return None

        self.logger.info(
            f"Replyer 生成回复: speech={result.get('speech', '')[:50]!r}, actions={len(result.get('actions', []))}"
        )
        return result

    # ==================== prompt 渲染（人设注入核心） ====================

    def _render_prompt(
        self,
        plan: DecisionPlan,
        batch: List[Any],
        persona: Dict[str, Any],
        history: Optional[List[Any]] = None,
        agenda: Optional[str] = None,
    ) -> str:
        """渲染 Replyer prompt，注入人设三件套 + 计划 + 弹幕 + 会话历史 + Agenda 上下文。

        人设分离承诺的另一半：$personality / $style_constraints / $bot_name 必须传给模板。
        会话历史用于让 Replyer 看到自己最近说过的话，避免冷场反复生成相同句式。
        Agenda 上下文（$agenda）是任务上下文注入（当前环节 / 整场进度），由调用方拼装后传入；
        None / 空串时用占位文本，避免模板出现字面 $agenda。
        """
        # Agenda 上下文：None / 空串时用占位文本，与 Planner 对齐
        agenda_render = agenda if agenda else "（当前无节目单）"
        return self._prompt_service.render_safe(
            _REPLYER_TEMPLATE,
            bot_name=persona.get("bot_name", self._bot_name),
            personality=persona.get("personality", _DEFAULT_PERSONALITY),
            style_constraints=persona.get("style_constraints", _DEFAULT_STYLE_CONSTRAINTS),
            plan=_render_plan_text(plan),
            danmaku_batch=_render_batch_text(batch),
            conversation_history=_render_history_text(history),
            agenda=agenda_render,
        )

    # ==================== function 定义构造 ====================

    @staticmethod
    def _build_reply_function_def() -> Dict[str, Any]:
        """构造 reply function 定义（OpenAI function calling 形态）。

        reply 是 Agent 内部协议工具——只服务主播自身 LLM 会话，不进 ToolRegistry。
        LLM 通过调用此函数输出 speech + emotion（emotion 是 emotion_vocab 12 枚举之一）。
        """
        return {
            "name": _REPLY_FUNCTION_NAME,
            "description": (
                "主播发言：输出你要对直播间说的话和情绪。"
                "必填：speech（1-2 句口语化文本）；可选：emotion（12 枚举之一，缺省 neutral）。"
                "调用此工具即代表你决定本轮发言；如需同时触发动作，可继续调用对应动作工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "speech": {
                        "type": "string",
                        "description": "要说的台词（1-2 句话，口语化，符合人设语气）",
                    },
                    "emotion": {
                        "type": "string",
                        "enum": [e.value for e in Emotion],
                        "description": "情绪（12 枚举之一）",
                    },
                },
                "required": ["speech"],
            },
        }

    # ==================== tool_calls 解析 ====================

    @staticmethod
    def _parse_tool_calls(
        tool_calls: Optional[List[Dict[str, Any]]],
    ) -> Tuple[str, Optional[str], float]:
        """从 LLMResponse.tool_calls 解析 reply(speech/emotion/intensity)。

        Replyer 工具面只有 reply，非 reply 调用一律忽略。

        Args:
            tool_calls: LLM 返回的 tool_calls 列表（OpenAI 形态：
                        ``{"name": str, "arguments": str|dict, "id": str, "type": "function"}``）

        Returns:
            ``(speech, emotion_name, emotion_intensity)``：
            - speech: 找到 reply call 时的 speech 字符串；找不到 reply 或 speech 为空时为 ``""``
            - emotion_name: reply call 提供的 emotion；未提供/非法时为 ``None``
            - emotion_intensity: reply call 提供的情绪强度，clamp 到 [0.0, 1.0]；
              未提供/非法时默认 0.5
        """
        if not tool_calls:
            return "", None, 0.5

        speech = ""
        emotion_name: Optional[str] = None
        emotion_intensity = 0.5

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else call
            if fn.get("name", "") != _REPLY_FUNCTION_NAME:
                continue
            args = _parse_call_arguments(call)
            if isinstance(args, dict):
                raw_speech = args.get("speech", "")
                if isinstance(raw_speech, str):
                    speech = raw_speech.strip()
                raw_emotion = args.get("emotion")
                if isinstance(raw_emotion, str) and raw_emotion:
                    emotion_name = raw_emotion.lower()
                raw_intensity = args.get("intensity")
                try:
                    emotion_intensity = min(1.0, max(0.0, float(raw_intensity)))
                except (TypeError, ValueError):
                    emotion_intensity = 0.5

        return speech, emotion_name, emotion_intensity

    # ==================== 敏感词净化（输出端） ====================

    def _apply_profanity_filter(
        self,
        result: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """敏感词净化（Replyer 表达引擎内部净化）。

        Args:
            result: 待净化的回复 dict（含 speech / emotion / actions）

        Returns:
            净化后的 result；``drop_on_match=True`` 且命中时返回 None（丢弃整条）。
            无 profanity_filter 注入时直接返回原 result（无净化）。
        """
        if result is None or self._profanity_filter is None:
            return result
        speech = result.get("speech", "")
        if not isinstance(speech, str):
            return result
        cleaned_speech, dropped = self._profanity_filter.filter(speech)
        if dropped and self._profanity_filter.drop_on_match:
            return None
        # 原 dict 复制以避免污染其他引用
        new_result = dict(result)
        new_result["speech"] = cleaned_speech
        return new_result


# ============================================================================
# ProfanityFilter —— 敏感词净化
# ============================================================================


class ProfanityFilter:
    """敏感词过滤器（"嘴"端净化职责）。

    配置项：
    - ``enabled``（bool）：总开关
    - ``words``（List[str]）：敏感词表
    - ``replacement``（str）：替换字符（默认 ``***``）
    - ``case_sensitive``（bool）：是否大小写敏感
    - ``drop_on_match``（bool）：命中时是否整条丢弃（True → 返回 None）

    使用示例：
        >>> flt = ProfanityFilter(words=["脏话A", "脏话B"], replacement="***", drop_on_match=False)
        >>> cleaned, dropped = flt.filter("这是一条脏话A测试")
        >>> cleaned
        '这是一条***测试'
        >>> dropped
        True
    """

    def __init__(
        self,
        *,
        words: Optional[List[str]] = None,
        replacement: str = "***",
        case_sensitive: bool = False,
        drop_on_match: bool = False,
        enabled: bool = True,
    ) -> None:
        """初始化敏感词过滤器。

        Args:
            words: 敏感词列表；None/空时过滤器不命中任何词（仅保留接口）
            replacement: 替换字符（默认 ``"***"``）
            case_sensitive: 是否大小写敏感（默认 False，更宽容）
            drop_on_match: 命中时是否整条丢弃（默认 False 仅替换）
            enabled: 总开关（默认 True；False 时 filter() 直接返回原文 + dropped=False）
        """
        self.enabled = enabled
        self.words = list(words) if words else []
        self.replacement = replacement
        self.case_sensitive = case_sensitive
        self.drop_on_match = drop_on_match

    def filter(self, text: str) -> Tuple[str, bool]:
        """过滤敏感词。

        Args:
            text: 待过滤的原始文本

        Returns:
            (cleaned_text, dropped) 元组：
            - ``dropped`` = True 表示命中了敏感词（drop_on_match=True 时 caller 应丢弃整条）。
            - ``cleaned_text`` = 替换后的文本（drop_on_match=False 时使用）。
        """
        if not self.enabled or not self.words or not text:
            return text, False

        # 大小写策略：构造搜索用的统一小写副本，命中后用原始大小写替换
        words_search = self.words if self.case_sensitive else [w.lower() for w in self.words]

        cleaned = text
        lowered = text if self.case_sensitive else text.lower()
        dropped = False

        for original_word, search_word in zip(self.words, words_search, strict=False):
            if not search_word:
                continue
            # 简易包含匹配（不区分词边界）
            if search_word in lowered:
                dropped = True
                # 替换（不区分大小写策略下用 case-insensitive replace）
                if self.case_sensitive:
                    cleaned = cleaned.replace(original_word, self.replacement)
                else:
                    # 用正则实现大小写不敏感的全局替换
                    pattern = re.compile(re.escape(original_word), re.IGNORECASE)
                    cleaned = pattern.sub(self.replacement, cleaned)
                    lowered = cleaned.lower()

        return cleaned, dropped


# ============================================================================
# 模块级辅助函数
# ============================================================================


def _parse_call_arguments(call: Dict[str, Any]) -> Any:
    """从单个 tool_call 中解析 arguments（兼容 str/dict 两种形态）。

    OpenAI 标准 tool_call.arguments 是 JSON 字符串；部分客户端/测试可能直接传 dict。
    解析失败时返回原始值（让 caller 自行降级）。
    """
    raw = call.get("arguments", {}) if isinstance(call, dict) else {}
    if not raw and isinstance(call.get("function"), dict):
        raw = call["function"].get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return {}


def _render_plan_text(plan: DecisionPlan) -> str:
    """把 DecisionPlan 渲染为供 prompt 使用的可读文本。"""
    target = plan.target or "（无特定目标，面向全体观众）"
    return (
        f"target: {target}\n"
        f"topic_summary: {plan.topic_summary or '（无特定话题）'}\n"
        f"reply_guidance: {plan.reply_guidance or '（无额外指引）'}\n"
        f"confidence: {plan.confidence:.2f}"
    )


def _render_batch_text(batch: List[Any]) -> str:
    """把弹幕批次渲染为文本（复用 MessageBuffer.render_batch_text）。"""
    if not batch:
        return "（本批无弹幕）"
    return MessageBuffer.render_batch_text(batch)


def _render_history_text(history: Optional[List[Any]]) -> str:
    """把会话历史渲染为供 prompt 使用的多行文本。

    每条消息渲染为 ``<role>: <content>`` 一行，从旧到新换行拼接。
    - role 可能是枚举对象（用 ``getattr(role, "value", str(role))`` 取值）；
    - 元素是鸭子类型（只需 ``role`` / ``content`` 两个属性），不绑定具体类型。
    - history 为 None 或空时返回占位文本，避免 LLM 拿到空字符串误以为没有上下文。
    """
    if not history:
        return "（暂无对话历史）"
    lines: List[str] = []
    for msg in history:
        role = getattr(msg, "role", None)
        role_str = getattr(role, "value", str(role)) if role else "user"
        content = getattr(msg, "content", "") or ""
        # 主动发言的 user 占位（"（主动发言，主题：...）"）是系统元数据而非观众弹幕，
        # 渲染为 [系统] 标注，避免 Replyer 误当成观众消息（与 Planner 渲染逻辑对齐）。
        if role_str == "user" and content.startswith("（主动发言"):
            lines.append(f"[系统] {content}")
            continue
        lines.append(f"{role_str}: {content}")
    return "\n".join(lines)


__all__ = ["Replyer", "ProfanityFilter"]
