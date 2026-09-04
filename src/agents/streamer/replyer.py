"""Replyer - 主播 Agent Stage 2 表达引擎

设计原则（人设分离承诺）：
- Planner（Stage 1）：**零人设**，只决定"要不要回复 / 回复谁 / 聊什么"，输出 DecisionPlan。
- Replyer（Stage 2，本模块）：**注入人设**，根据 plan + 弹幕批次 + 人设生成实际回复，
  输出可直接被 TTS 等工具消费的 speech + emotion + action。
- 两者使用不同的 LLM 客户端：Planner 用快速模型（llm_fast），Replyer 用高质量模型（llm）。

职责边界：
- 只生成并**返回** dict（含 speech/emotion/action），**不**直接调用 reply_tool，
  reply_tool 是 Agent 暴露给 LLM 调用的工具入口（Stage 2 内脏 + 工具入口分层）。
- **不调用 tools**（不做 function calling，纯文本 JSON 输出）。
- **敏感词净化**（输出端）：原 output/pipelines/profanity_filter 的词表过滤逻辑
  verbatim 归此地——"嘴"端净化（不再经 output 阶段"通用净化"）。
- 复用 Planner 既有模式：`_clean_llm_json` 三步清理、情绪降级 neutral、动作白名单校验。

与 StreamerAgent 的关系：
- 本类是一个"纯函数式"的 Stage 2 组件，由 StreamerAgent 持有并在 reply_tool.invoke 时调用。
- 能力白名单逻辑（`_ensure_capabilities` / `_build_action`）与原 replyer.py 保持一致，
  但在此独立实现，避免双向耦合。

迁移记录：
- 原 ``stages/decision/deciders/amaidesu/replyer.py`` → ``agents/streamer/replyer.py``
- ``Intent`` 输出 → dict 输出（Intent 类型已删除，工具调用参数即边界）
- ``replyer_client`` 配置键保留（向后兼容）；同时接受 ``replyer_llm``（新 agents_schemas 命名）
- 净化：内置 profanity filter（词表 + 替换 + drop_on_match 选项）
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.types.capabilities import CapabilitiesProvider, UnifiedCapabilitiesView
from src.modules.types.emotion_vocab import Emotion

from .message_buffer import MessageBuffer
from .plan import DecisionPlan

# 默认人设兜底值（persona dict 缺字段时使用）
#
# 优先级链：
# 1. persona dict 中的同名键（来自 config/core.toml 的 [persona] 段，由装配根
#    main._register_agents_from_config 拉取后透传给 StreamerAgent.persona_provider，
#    再经 ReplyToolProvider._resolve_persona 解析后传给 Replyer.generate(persona=...)）。
# 2. StreamerAgentConfig.bot_name（agents.toml 显式覆盖）。
# 3. 本模块 _DEFAULT_* 常量（仅当 persona dict 完全缺失/字段缺位时兜底，避免冷启动崩）。
#
# _DEFAULT_BOT_NAME = '麦麦'、personality/style_constraints 文本与
# core_schemas.PersonaConfig 默认值对齐；不允许 config 模块反向依赖 agents 层，
# 故这里复制文本（保持依赖方向 agents → config 干净）。
_DEFAULT_BOT_NAME = "麦麦"
_DEFAULT_PERSONALITY = "活泼开朗，有些调皮，喜欢和观众互动"
_DEFAULT_STYLE_CONSTRAINTS = "口语化，使用网络流行语，避免机械式回复，适当使用emoji"

# Replyer 模板名（Stage 2，含 $personality/$style_constraints/$bot_name 人设注入）
_REPLYER_TEMPLATE = "amaidesu_replyer"


class Replyer:
    """Stage 2 表达引擎：消费 DecisionPlan + 弹幕 + 人设，生成实际回复。

    不发布事件、不调用 tools。``generate()`` 返回 ``Optional[dict]``（含
    speech/emotion/action_parameters），由 reply_tool 包装后返回给 LLM。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        llm_service: Any,
        prompt_service: Any,
        capabilities_provider: Optional[CapabilitiesProvider] = None,
        profanity_filter: Optional["ProfanityFilter"] = None,
    ) -> None:
        """初始化 Replyer。

        Args:
            config: 配置字典（兼容 StreamerAgentConfig 的子集字段），
                    读取 replyer_llm / replyer_client / enable_action_selection / bot_name。
            llm_service: LLM 管理器（使用 replyer_llm 指定的高质量客户端）。
            prompt_service: 提示词管理器（渲染 amaidesu_replyer 模板）。
            capabilities_provider: 工具能力提供者（可选，用于动作白名单校验）。
            profanity_filter: 敏感词过滤器（输出端净化入口；None 表示不净化）。
        """
        self._config: Dict[str, Any] = config or {}
        # replyer_llm（新 agents_schemas 命名）+ replyer_client（向后兼容）
        self.replyer_llm: str = self._config.get("replyer_llm", self._config.get("replyer_client", "llm"))
        self._enable_action_selection: bool = self._config.get("enable_action_selection", True)
        self._bot_name: str = self._config.get("bot_name", _DEFAULT_BOT_NAME)

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._capabilities_provider = capabilities_provider
        self._profanity_filter = profanity_filter
        self.logger = get_logger("Replyer")

        # 能力快照（首次 generate 时惰性加载并缓存）
        self._capabilities_loaded: bool = False
        self._valid_action_names: set[str] = set()
        self._action_list_str: str = ""

    async def generate(
        self,
        plan: DecisionPlan,
        batch: List[Any],
        persona: Dict[str, Any],
        history: Optional[List[Any]] = None,
        agenda: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """根据 Planner 的决策计划 + 弹幕批次 + 人设，生成实际回复。

        流程：
        1. 注入人设：render 'amaidesu_replyer'（含 $personality/$style_constraints/$bot_name）。
        2. 调用高质量 LLM（replyer_llm，默认 llm），**不传 tools**。
        3. 清理 + 解析 JSON → {text, emotion, action, action_parameters}。
        4. 组装：情绪降级 neutral、动作白名单校验（非法丢弃保留 speech）。
        5. **敏感词净化**（输出端）：profanity_filter 净化 speech（替换或丢弃）。
        6. 返回 dict（不发布事件；reply_tool 负责 ToolExecutionResult 包装）。

        Args:
            plan: Planner 产出的决策计划（should_reply=True 时才应到达此处）。
            batch: 本批弹幕（NormalizedMessage 列表）。
            persona: 人设字典（bot_name / personality / style_constraints）。
            history: 可选的最近会话历史（鸭子类型对象列表，需有 ``role`` 和 ``content`` 属性）；
                     role 可能是枚举（取 ``.value``），content 是 str。None 表示无历史可用，
                     渲染为占位文本。用于让 Replyer 看到自己最近说过的话，避免冷场时反复
                     生成相同句式。
            agenda: 当前 Agenda 的渲染文本（可选）。由调用方（如 StreamerAgent
                主循环）从 ``AgendaState`` 拼装后传入，描述当前环节的
                title / task_description / key_points / 环节剩余时长 + 整场进度
                （已进行时长 / 总计划时长 / 百分比）。``None`` 或空字符串时使用占位文本
                "（当前无节目单）"——Replyer 在未启用 Agenda 机制时仍可正常工作。
                透传到 prompt 的 ``$agenda`` 变量。**注意**：$agenda 是任务上下文
                注入，不改变 Replyer 注入人设的分工（人设三件套 $personality /
                $style_constraints / $bot_name 仍由本类负责注入）。

        Returns:
            Dict 实例（含 speech/emotion/action/action_parameters/...）；LLM 异常或
            解析失败时返回 None（silent 降级）。reply_tool 直接将此 dict 包装进
            ToolExecutionResult 返回给 LLM。
        """
        # 防御：Planner 已裁决 should_reply=True 才会进入此处；False 直接放弃。
        if not plan.should_reply:
            self.logger.debug("DecisionPlan.should_reply=False，Replyer 跳过生成")
            return None

        # 惰性加载能力快照（用于动作白名单）
        self._ensure_capabilities()

        # ① 注入人设 + 决策计划 + 弹幕上下文 + 会话历史 + Agenda 上下文，渲染 Replyer prompt
        prompt = self._render_prompt(plan, batch, persona, history, agenda)

        # ② 调用高质量 LLM（无 tools）
        try:
            self.logger.info(f"Replyer 生成回复中 (plan.target={plan.target!r}, client={self.replyer_llm})")
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.replyer_llm,
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

        # ④ 组装回复（情绪降级 + 动作白名单）
        result = self._create_result(parsed_data, speech, plan)

        # ⑤ 敏感词净化（净化职责归 Replyer 表达引擎）
        result = self._apply_profanity_filter(result)
        if result is None:
            self.logger.warning("Replyer 输出被 profanity filter 丢弃（drop_on_match=True）")
            return None

        self.logger.info(f"Replyer 生成回复: {result.get('speech', '')}")
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
            action_list=self._action_list_str or "（当前无可用动作，action 请留空字符串）",
            agenda=agenda_render,
        )

    # ==================== 回复组装（情绪降级 + 动作白名单） ====================

    def _create_result(
        self,
        parsed_data: Dict[str, Any],
        speech: str,
        plan: DecisionPlan,
    ) -> Dict[str, Any]:
        """从解析后的 JSON 构造回复 dict（speech + emotion + 经能力校验的 action）。

        与原 replyer.py._create_intent 同构：
        - 非法 emotion → 降级 neutral（12 枚举校验由 emotion_vocab 强制）。
        - 非法 action（不在白名单）→ 丢弃 action，保留 speech。
        """
        emotion_raw = str(parsed_data.get("emotion", "neutral")).lower()
        valid_emotion_names = {e.value for e in Emotion}
        if emotion_raw in valid_emotion_names:
            emotion_name = emotion_raw
        else:
            self.logger.warning(f"Replyer 情绪 '{emotion_raw}' 不在枚举中，降级为 neutral")
            emotion_name = "neutral"

        action = self._build_action(parsed_data)

        return {
            "speech": speech,
            "emotion": {
                "name": emotion_name,
                "intensity": 0.5,
            },
            "action": action,
            "metadata": {
                "source_id": "streamer_agent",
                "target": plan.target,
                "topic_summary": plan.topic_summary,
                "reply_guidance": plan.reply_guidance,
                "confidence": plan.confidence,
            },
        }

    def _build_action(self, parsed_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 LLM 输出构造并校验动作字典。

        - 期望 ``action`` 为全限定名 ``<tool>.<local_action>``（来自能力清单）。
        - 启用动作选择且已加载能力时，对动作名做白名单校验，非法则丢弃（保留 speech）。
        - ``action_parameters`` 必须为 dict，否则忽略参数。
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

        return {
            "name": action_raw,
            "parameters": parameters,
        }

    # ==================== 敏感词净化（输出端；行为 verbatim 保留） ====================

    def _apply_profanity_filter(
        self,
        result: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """敏感词净化（输出管道 → Replyer 表达引擎内部净化）。

        Args:
            result: 待净化的回复 dict（含 speech / emotion / action）

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

    # ==================== 能力快照（惰性加载） ====================

    def _ensure_capabilities(self) -> None:
        """惰性加载并缓存工具能力快照（首次 generate 时调用一次）。"""
        if self._capabilities_loaded:
            return
        self._capabilities_loaded = True

        if not self._enable_action_selection or self._capabilities_provider is None:
            return

        try:
            view = self._capabilities_provider.get_all_capabilities()
        except Exception as e:
            self.logger.warning(f"Replyer 查询工具能力失败，动作选择降级为禁用: {e}")
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


# ============================================================================
# ProfanityFilter —— 敏感词净化（行为 verbatim 移植自 output/pipelines/profanity_filter）
# ============================================================================


class ProfanityFilter:
    """敏感词过滤器（从 output 管道搬到 Replyer 内部）。

    原 ``stages/output/pipelines/profanity_filter/pipeline.py`` 中：
    - ``enabled``（bool）：总开关
    - ``words``（List[str]）：敏感词表
    - ``replacement``（str）：替换字符（默认 ``***``）
    - ``case_sensitive``（bool）：是否大小写敏感
    - ``drop_on_match``（bool）：命中时是否整条丢弃（True → 返回 None）

    行为保留 verbatim；仅适配"嘴"端净化职责。

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
            # 简易包含匹配（不区分词边界，匹配原 profanity_filter pipeline 行为）
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
# 模块级辅助函数（_clean_llm_json / _render_*）
# ============================================================================


def _clean_llm_json(raw_output: str) -> str:
    """清理 LLM 返回的 JSON 字符串（与原 Replyer._clean_llm_json 一致的三步清理）。

    独立为模块级函数以避免与 StreamerAgent 产生双向依赖。
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


def _format_action_list(view: UnifiedCapabilitiesView) -> str:
    """把能力视图渲染为供 prompt 使用的动作清单文本。"""
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


__all__ = ["Replyer", "ProfanityFilter"]
