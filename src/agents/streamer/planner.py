"""Planner - 主播 Agent 主循环 / 决策核心

职责（Agent 内脏，**不是工具**）：
- 判断当前弹幕批次是否值得主播介入（should_reply）
- 产出 DecisionPlan：目标对象 / 话题摘要 / 回复指引 / 置信度
- **不写台词**（那是 Replyer 的职责）

核心契约：
1. **人设行为准则注入**：Planner prompt 注入 ``$behavior_style``（行动准则：何时参与聊天、
   如何观察局面、何时保持安静）——该字段来自 config/core.toml 的 [persona].behavior_style，
   由 StreamerAgent 装配时透传给 Planner。
2. **身份/表达人设隔离**：Planner prompt **不传** ``$personality`` / ``$style_constraints`` /
   ``$bot_name``——这三件套是身份与表达层，仅注入 Replyer（表达引擎）。人设按三层拆分：
   personality + style_constraints = 表达侧（Replyer），behavior_style = 决策侧（Planner）。
3. **快速模型**：使用 ``planner_llm``（默认 ``llm_fast``），与 Replyer 的
   ``replyer_llm``（默认 ``llm``）分离，避免共享客户端实例。
4. **结构化决策输出**：`call_tools(tools=[produce_plan_fn_def])` 走标准 function calling，
   Planner 是 Agent 内部协议（不进 ToolRegistry），由 LLM 通过 produce_plan 工具调用产出
   DecisionPlan 字段。Planner **不执行**任何动作工具——它是决策器而非工具调用者。
5. **降级安全**：LLM 异常 / 无 tool_calls / 参数解析失败均返回 ``None``，
   由调用方（StreamerAgent 主循环）处理降级。
6. **历史感知**：可选注入最近对话历史（``history``），渲染为 ``$conversation_history``
   注入 prompt，让 Planner 决策时能反重复（特别是主动发言时避免重复已聊过的话题）。

数据流：
    batch + room_state.snapshot + history + forced/proactive + behavior_style
        ──▶ render_safe('amaidesu_planner')
        ──▶ llm_service.call_tools(prompt, tools=[produce_plan_fn_def], client_type=planner_llm)
        ──▶ tool_calls[0]["arguments"] → json.loads → DecisionPlan（或 None）

提示词内聚于本包 prompts/ 目录，键来自模板 frontmatter 的
``name: amaidesu_planner``。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.modules.config.schemas.base import BaseConfig
from src.modules.context.assembler import AssemblerInputs, PlannerAssembler
from src.modules.context.snapshot import EnvironmentBlock
from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit
from src.modules.time_utils import format_duration_ms, now_ms
from src.modules.types.message_type import require_message_type

from .plan import DecisionPlan
from .room_state import RoomState, RoomStateSnapshot

__all__ = ["Planner"]


#: 决策一致性阈值：`should_reply=true` 要求的最低置信度。
#: LLM 偶发输出 `confidence=0.0` 却 `should_reply=true` 的矛盾决策
#: （日志实测出现过），此时降级静默，避免"没话找话硬开口"。
_MIN_CONFIDENCE_FOR_REPLY: float = 0.3

#: 默认记忆召回条数（Planner 每轮决策注入的 hit 上限）。
#: 配置面不允许暴露——记忆质量先稳定再调参，避免污染用户配置文件。
_DEFAULT_RECALL_TOP_K: int = 3

#: 弹幕批次拼接到召回 query 的最大字符数（控 query 长度，避免污染召回）。
_RECALL_QUERY_BATCH_CHARS: int = 200

#: 单条召回 hit 文本截断长度（控制 prompt 体积）。
_RECALL_HIT_TEXT_CHARS: int = 80


# ---------------------------------------------------------------------------
# produce_plan function definition（Agent 内部协议，不进 ToolRegistry）
# ---------------------------------------------------------------------------


#: Planner 决策结构化输出的 function calling 声明。
#:
#: **为何显式声明而非从 ``DecisionPlan.model_json_schema()`` 自动生成**：
#: 自动生成会包含 ``silent_reason``（仅 Planner 降级路径填写，LLM 不应输出）
#: 与 ``version``（内部字段）——显式收窄到 LLM 真正应该填的字段集，避免噪声契约。
#:
#: **为何不进 ToolRegistry**：produce_plan 是 Planner 自身出口，
#: 仅服务于主播 Agent 的 LLM 会话；ToolRegistry 是通用动作库，
#: Agent 内脏注册为工具违反「主体性判据」红线（Agent 内脏 ≠ 工具）。
PRODUCE_PLAN_FN_DEF: Dict[str, Any] = {
    "name": "produce_plan",
    "description": "对本批弹幕/当前直播间态势做决策：主播是否应该发言、回应谁、话题是什么",
    "parameters": {
        "type": "object",
        "properties": {
            "should_reply": {"type": "boolean"},
            "target": {
                "type": "string",
                "nullable": True,
                "description": "要回应的弹幕 message_id 或片段",
            },
            "reply_to": {
                "type": "string",
                "nullable": True,
                "description": "本轮回复所指向弹幕的 message_id（从批次中选取）",
            },
            "topic_summary": {"type": "string"},
            "reply_guidance": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "may_advance": {"type": "boolean"},
            "need_more_time": {"type": "boolean"},
            "branch_id": {"type": "string", "nullable": True},
        },
        "required": ["should_reply", "topic_summary", "confidence"],
    },
}


# ---------------------------------------------------------------------------
# 配置 Schema（最小子集）
# ---------------------------------------------------------------------------


class _PlannerConfig(BaseConfig):
    """Planner 配置 Schema。

    仅声明 Planner 直接使用的字段；当从完整 StreamerAgentConfig 加载时，
    BaseConfig.from_dict() 的漂移检测会自动剥离其余字段。
    """

    # 两阶段-Planner 使用的 LLM 客户端（快速模型，默认 llm_fast）
    # 与 Replyer 的 replyer_llm（默认 llm）分离，避免共享客户端实例。
    planner_llm: str = "llm_fast"


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """主播 Agent 决策核心：判断"要不要参与 + 如何参与"。

    非线程安全；仅在 StreamerAgent 的单一 asyncio 事件循环内使用。
    通过 ``plan()`` 方法驱动，**不订阅 EventBus**——它是 Agent 内部子组件。
    """

    #: Planner 专用的提示词模板名（零人设注入）
    TEMPLATE_NAME: str = "amaidesu_planner"

    def __init__(
        self,
        config: Any,
        llm_service: Any,
        prompt_service: Any,
        room_state: RoomState,
        tool_registry: Any = None,
        memory: Any = None,
        recall_top_k: int = _DEFAULT_RECALL_TOP_K,
        context_enabled: bool = True,
        behavior_style: str = "",
    ) -> None:
        """初始化 Planner。

        Args:
            config: 配置字典或已解析的配置对象。支持两种形式：
                - ``dict``：经 ``_PlannerConfig.from_dict()`` 解析（自动剥离未知字段）
                - 已解析对象：若有 ``planner_llm`` 属性则直接读取
            llm_service: LLM 管理器（``LLMManager`` 或鸭子类型），
                需提供 ``async call_tools(prompt, tools, *, client_type) -> LLMResponse``
                接口（标准 function calling 入口）。保留兼容旧 ``chat()`` 形式需自行包装。
            prompt_service: 提示词管理器（``PromptManager`` 或鸭子类型），
                需提供 ``render_safe(template_name, **vars) -> str`` 接口
            room_state: 直播间态势规则层实例（``RoomState``）
            tool_registry: 已**废弃**，参数保留仅为不破坏 ``StreamerAgent`` 调用方。
                Planner 不再向 prompt 注入动作工具清单——决策器不感知动作能力；
                工具选择由下游 Replyer 阶段负责（Agent 内部协议 ``produce_plan`` 是
                Planner 唯一的"工具"声明，不进 ``ToolRegistry``）。
            memory: 记忆后端（鸭子类型 ``MemoryProvider``）。``None`` 时记忆
                召回段落渲染为 ``（暂无）``，Planner 走无记忆决策路径。功能可关闭
                而非崩溃友好——主控装配时未注入则 Planner 整体降级。
            recall_top_k: 每轮注入 prompt 的最大命中条数，默认 3。
                装配时由 core.toml [context].memory_recall_long_term 驱动。
            context_enabled: 组装器路径开关（core.toml [context].enabled）。
                False 时跳过记忆召回与组装器，直接以直播流窗口文本作为
                context_block（裸消息路径）。
            behavior_style: 人设行为准则（来自 [persona].behavior_style）。
                仅注入 Planner prompt 的 ``$behavior_style`` 变量，指导"何时发言 / 聊
                什么话题 / 何时保持安静"等行动决策；不会反向泄露到 Replyer 表达侧。
                缺省空串时模板会渲染为占位文本（不让 LLM 看到字面 ``$behavior_style``）。
        """
        # 解析配置（容忍 dict / 已解析对象 / None）
        if config is None:
            self.typed_config = _PlannerConfig()
        elif hasattr(config, "planner_llm"):
            # 已解析的配置对象（如 StreamerAgentConfig）
            self.typed_config = _PlannerConfig(planner_llm=config.planner_llm)
        elif isinstance(config, dict):
            self.typed_config = _PlannerConfig.from_dict(config)
        else:
            # 兜底：尝试当作 dict-like 解析
            self.typed_config = _PlannerConfig.from_dict(dict(config))

        self.planner_llm: str = self.typed_config.planner_llm

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._room_state = room_state
        # tool_registry 参数保留以不破坏 StreamerAgent 调用方；内部不再使用
        # （Planner 不感知动作工具；produce_plan 是 Agent 内部协议）
        del tool_registry
        # 记忆后端与召回深度
        self._memory = memory
        self._recall_top_k = recall_top_k
        # [context].enabled：False 时走裸消息路径（跳过召回与组装器）
        self._context_enabled = context_enabled

        # PlannerAssembler 一份实例（assemble 纯函数，但保留成员以便未来
        # 缓存 stable_prefix_hash 做 LLM 缓存前缀命中）
        self._assembler = PlannerAssembler()

        # 决策侧人设准则存储。空串表示上层未注入（罕见：装配时 persona 段缺字段），
        # 渲染时使用占位文本避免模板出现字面 ``$behavior_style``，行为退化为"无准则"。
        self._behavior_style: str = behavior_style or ""

        # 最近一次 plan() 的可观测副产品（决策事件消费；每轮 plan() 入口重置）：
        # - last_raw_content: LLM 原始返回文本（截断前的完整内容，观察器自行截断）
        # - last_request_id: LLM 请求历史 ID（"查看完整请求"的指针）
        # - last_failure: 失败原因（成功为 None；当前 plan() 失败只返回 None，
        #   失败细节经此带出，决策事件才能区分"LLM 出错/解析失败/构造失败"）
        self.last_raw_content: str = ""
        self.last_request_id: Optional[str] = None
        self.last_failure: Optional[str] = None

        self.logger = get_logger("Planner")

    # ==================== 主入口 ====================

    async def plan(
        self,
        batch: List[Any],
        *,
        forced: bool = False,
        proactive: bool = False,
        history: Optional[List[Any]] = None,
        agenda_text: Optional[str] = None,
        game_narrative: str = "",
    ) -> Optional[DecisionPlan]:
        """对一批弹幕做战术决策，产出 DecisionPlan。

        Args:
            batch: 本批弹幕列表（``NormalizedMessage`` 或鸭子类型）。允许为空列表：
                当 ``proactive=True`` 时，模板会基于房间状态独立判断是否主动开口。
            forced: 是否为强制回应批次（SC / 礼物 / 上舰等）。会透传到 prompt 的
                ``$forced`` 变量，影响 LLM 的 should_reply 判断。
            proactive: 是否为主动发言触发（由 ``ProactiveTrigger`` 在冷场或定时间隔
                触发时调用）。会透传到 prompt 的 ``$proactive`` 变量，提示 LLM 本批
                弹幕可能为空、需基于房间状态决定是否主动开口。
            history: 最近对话历史（可选，由调用方从 ``ContextService`` 等获取）。
                元素为 ``ConversationMessage`` 鸭子类型（``role`` 属性可能是
                ``MessageRole`` 枚举或字符串；``content`` 为字符串）。``None`` 表示
                无历史可用，渲染为占位文本。会透传到 prompt 的 ``$conversation_history``
                变量，供 LLM 决策时反重复（特别是主动发言时避免重复已聊过的话题）。
            agenda_text: 当前 Agenda 的渲染文本（可选）。由调用方（如 StreamerAgent
                主循环）从 ``AgendaState`` 拼装后传入，描述当前环节的
                title / task_description / key_points / 环节剩余时长 + 整场进度
                （已进行时长 / 总计划时长 / 百分比）。``None`` 或空字符串时使用占位文本
                "（当前无节目单）"——Planner 在未启用 Agenda 机制时仍可正常工作。
                透传到 prompt 的 ``$agenda`` 变量，**注意**：是任务上下文注入，不
                改变 Planner 零人设原则。

        Returns:
            DecisionPlan：解析成功时返回；LLM 异常 / 脏 JSON / 调用失败时返回 None，
            由调用方（StreamerAgent 主循环）处理降级。
        """
        # 可观测副产品复位：本轮的原始输出/请求 ID/失败原因由各路径写入
        self.last_raw_content = ""
        self.last_request_id = None
        self.last_failure = None

        # 组装上下文（PlannerAssembler 单一装配路径）
        snapshot = self._room_state.get_snapshot()
        danmaku_text = self._render_batch(batch)
        history_text = self._render_history(history)

        # 直播流窗口：历史在上、当前批在下（显式多对一，不强行一问一答）
        recent_chat_parts: List[str] = []
        if history_text and history_text != "（暂无对话历史）":
            recent_chat_parts.append(history_text)
        if danmaku_text and danmaku_text != "（本批无弹幕）":
            recent_chat_parts.append(danmaku_text)
        recent_chat_window = "\n\n".join(recent_chat_parts) if recent_chat_parts else "（暂无）"

        # 3~5. 组装器路径（[context].enabled=False 时走裸消息：context_block=直播流窗口）
        if self._context_enabled:
            # 直播间快照（EnvironmentBlock：分钟级缓存友好）
            current_ms = now_ms()
            env_block = EnvironmentBlock(
                minute_bucket_ms=(current_ms // 60000) * 60000,
                # 暂无"开播时刻"字段——按任务约定传 0，模板按 0ms 处理
                duration_so_far_ms=0,
                current_stage_label=None,  # Planner 零上下文承诺；环节标题由 Replyer 用
                unread_summary=getattr(snapshot, "topic_summary", "") or "",
                key_changes=list(getattr(snapshot, "topics", []) or []),
            )

            # 记忆召回：recall(query, top_k) → 文本行
            memory_recall_section = await self._recall_memory(snapshot, batch)

            # PlannerAssembler 输入（★ Planner 零人设承诺 → persona=""）
            # tool_definitions_block=""：Planner 不感知动作工具清单（决策器非工具调用者）
            try:
                assembler_inputs = AssemblerInputs(
                    persona="",
                    tool_definitions_block="",
                    current_stage_label=None,
                    stage_descriptions=agenda_text or "",
                    timeline_blocks=[],
                    recent_chat_window=recent_chat_window,
                    environment=env_block,
                    working_memory=None,
                    memory_recall_section=memory_recall_section,
                    reply_intent="",
                    topic_subset="",
                    agent_kind="planner",
                )
                snapshot_assembled = self._assembler.assemble(assembler_inputs)
            except Exception as e:
                self.logger.error(f"PlannerAssembler 组装失败: {e}", exc_info=True)
                self.last_failure = f"assembler_failed: {e}"
                return None
            context_block = snapshot_assembled.rendered_text
        else:
            context_block = recent_chat_window

        # 渲染 prompt（★ 四个变量：context_block / forced / proactive / behavior_style）
        #    forced / proactive 透传为字符串（"true"/"false"），对齐模板中的文档约定。
        #    behavior_style 仅注入 Planner 决策侧（指导"何时发言/聊什么/何时沉默"），
        #    personality/style_constraints/bot_name 仍仅由 Replyer 表达侧注入，
        #    身份/表达与行动准则分离。behavior_style 空串时使用占位文本避免字面 $behavior_style。
        behavior_style_render = self._behavior_style or "（未配置行动准则，请依据直播间态势与对话历史自行决策）"
        game_narrative_render = game_narrative or "（当前没有游戏叙事）"
        try:
            prompt = self._prompt_service.render_safe(
                self.TEMPLATE_NAME,
                context_block=context_block,
                forced=str(forced).lower(),
                proactive=str(proactive).lower(),
                behavior_style=behavior_style_render,
                game_narrative=game_narrative_render,
            )
        except Exception as e:
            self.logger.error(f"渲染 Planner prompt 失败: {e}", exc_info=True)
            self.last_failure = f"prompt_render_failed: {e}"
            return None

        # 调用 LLM（★ 标准 function calling：produce_plan 是 Agent 内部协议）
        # Planner 不执行动作工具；唯一的"工具"声明是 produce_plan，用于结构化输出
        # DecisionPlan 字段（不进 ToolRegistry）。
        try:
            response = await self._llm_service.call_tools(
                prompt=prompt,
                tools=[PRODUCE_PLAN_FN_DEF],
                client_type=self.planner_llm,
            )
        except Exception as e:
            self.logger.warning(f"Planner LLM 调用异常，返回 None 由调用方降级: {e}")
            self.last_failure = f"llm_error: {e}"
            return None

        # 提取 tool_calls（标准 function calling 输出形态）
        tool_calls = self._extract_tool_calls(response)
        if tool_calls is None:
            self.logger.warning("Planner LLM 返回无 tool_calls 或调用失败（success=False）")
            self.last_failure = "llm_no_tool_calls"
            return None

        arguments_str = self._find_produce_plan_arguments(tool_calls)
        if arguments_str is None:
            self.logger.warning("Planner LLM 未调用 produce_plan 工具（决策结构缺失）")
            self.last_failure = "produce_plan_not_called"
            return None

        try:
            parsed = json.loads(arguments_str)
        except json.JSONDecodeError as e:
            self.logger.warning(f"Planner produce_plan arguments 解析失败: {e}, 原始前 200 字: {arguments_str[:200]}")
            self.last_failure = f"json_parse_failed: {e}"
            return None

        if not isinstance(parsed, dict):
            self.logger.warning(f"Planner produce_plan arguments 顶层非对象: {type(parsed).__name__}")
            self.last_failure = "json_not_object"
            return None

        # 可观测副产品：原始输出记 arguments_str（实际决策结构）便于观察器定位
        self.last_raw_content = arguments_str
        self.last_request_id = getattr(response, "request_id", None) or None

        plan = self._build_plan(parsed)
        if plan is None:
            self.last_failure = "plan_build_failed"
            return None

        # 决策一致性校验：低置信度却要回复 → 矛盾决策，降级静默。
        # 强制场景（SC/礼物/上舰）除外——付费内容必须回应，置信度不影响。
        if not forced and plan.should_reply and plan.confidence < _MIN_CONFIDENCE_FOR_REPLY:
            self.logger.info(
                f"Planner 低置信度决策降级静默 "
                f"(confidence={plan.confidence:.2f}, topic_summary={plan.topic_summary[:30]!r})"
            )
            self.last_failure = None
            return DecisionPlan(
                should_reply=False,
                target=None,
                topic_summary="",
                reply_guidance="",
                confidence=plan.confidence,
                silent_reason="low_confidence",
            )

        self.last_failure = None
        return plan

    # ==================== 辅助方法 ====================

    @staticmethod
    def _extract_tool_calls(response: Any) -> Optional[List[Dict[str, Any]]]:
        """从 LLM 响应中提取 tool_calls 列表。

        适配 ``LLMResponse.tool_calls`` 字段（OpenAI function calling 标准形态）；
        调用失败（success=False）或 tool_calls 为空时返回 None。

        Args:
            response: LLMResponse 鸭子类型

        Returns:
            tool_calls 列表；失败或缺失时返回 None
        """
        success = getattr(response, "success", True)
        if success is False:
            return None
        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            return None
        return tool_calls

    @staticmethod
    def _find_produce_plan_arguments(tool_calls: List[Dict[str, Any]]) -> Optional[str]:
        """在 tool_calls 中查找 produce_plan 调用的 arguments（JSON 字符串）。

        多个 tool_calls 时优先取第一个名为 produce_plan 的；找不到返回 None。

        Args:
            tool_calls: LLM 调用的工具列表

        Returns:
            produce_plan.arguments 字符串；未找到时返回 None
        """
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.get("name") == "produce_plan":
                args = tc.get("arguments")
                if isinstance(args, str):
                    return args
                if isinstance(args, dict):
                    # 容错：部分客户端可能直接返回 dict（已解析）
                    return json.dumps(args, ensure_ascii=False)
        return None

    def _build_plan(self, parsed: dict) -> Optional[DecisionPlan]:
        """从解析后的 JSON 字典构造 DecisionPlan。

        Args:
            parsed: JSON 解析后的字典

        Returns:
            DecisionPlan 实例；字段类型不匹配时返回 None
        """
        try:
            return DecisionPlan(
                should_reply=bool(parsed.get("should_reply", False)),
                target=(parsed.get("target") or None),
                reply_to=(str(parsed.get("reply_to")) if parsed.get("reply_to") else None),
                topic_summary=str(parsed.get("topic_summary", "") or ""),
                reply_guidance=str(parsed.get("reply_guidance", "") or ""),
                confidence=float(parsed.get("confidence", 0.0) or 0.0),
            )
        except (TypeError, ValueError) as e:
            self.logger.warning(f"构造 DecisionPlan 失败（字段类型不匹配）: {e}")
            return None
        except Exception as e:
            # Pydantic ValidationError 等其他异常也走降级
            self.logger.warning(f"构造 DecisionPlan 失败: {e}")
            return None

    async def _recall_memory(
        self,
        snapshot: RoomStateSnapshot,
        batch: List[Any],
    ) -> str:
        """调记忆后端召回相关历史片段，格式化为 prompt 注入文本。

        Query 组装策略（任务约定）：
        - 基础 = RoomState.topic_summary（非空时）
        - 拼接 = 本批弹幕文本前 200 字符
        - 无 memory / 无 hits / 异常 → 空串；Assembler 兜底 "（暂无）"

        Args:
            snapshot: RoomState 快照（取 topic_summary）
            batch: 当前批弹幕（取前 200 字符文本）

        Returns:
            多行召回文本；无内容时返回空串
        """
        if self._memory is None:
            return ""

        topic_summary = (getattr(snapshot, "topic_summary", "") or "").strip()
        batch_text = self._render_batch(batch)
        # batch_text 已是 MessageBuffer 格式化的 "name: text" 行；这里只截前 200 字符
        batch_head = (batch_text or "").strip()[:_RECALL_QUERY_BATCH_CHARS]
        query_parts = [p for p in (topic_summary, batch_head) if p]
        query = "\n".join(query_parts) if query_parts else ""
        if not query:
            return ""

        try:
            hits: List[MemoryHit] = await self._memory.recall(query, top_k=self._recall_top_k)
        except Exception as exc:
            # 召回失败不阻断决策——整体降级静默
            self.logger.warning(f"记忆召回失败: {exc}")
            return ""

        if not hits:
            return ""

        lines: List[str] = []
        for hit in hits:
            text = (getattr(hit, "text", "") or "").replace("\n", " ").strip()
            if len(text) > _RECALL_HIT_TEXT_CHARS:
                text = text[:_RECALL_HIT_TEXT_CHARS].rstrip() + "…"
            score = getattr(hit, "score", 0.0) or 0.0
            metadata = getattr(hit, "metadata", {}) or {}
            source = metadata.get("source", "unknown")
            lines.append(f"- [{score:.2f} | src={source}] {text}")

        return "\n".join(lines)

    def _render_room_state(self, snapshot: RoomStateSnapshot) -> str:
        """将直播间态势快照渲染为人类可读的文本块（供 prompt 注入）。

        Args:
            snapshot: RoomState.get_snapshot() 返回的快照

        Returns:
            多行文本，包含热度 / 话题 / SC 队列 / 话题摘要
        """
        parts: List[str] = []

        # 热度
        heat_map = {"low": "冷场（弹幕稀少）", "medium": "正常节奏", "high": "高热（弹幕密集）"}
        heat_desc = heat_map.get(getattr(snapshot, "heat", "low"), snapshot.heat)
        parts.append(f"- 热度等级: {heat_desc}")

        # 话题关键词
        topics = getattr(snapshot, "topics", None) or []
        if topics:
            parts.append(f"- 话题关键词: {', '.join(topics)}")

        # SC 队列
        sc_queue = getattr(snapshot, "sc_queue", None) or []
        if sc_queue:
            parts.append(f"- 待处理 SC/礼物/上舰: {len(sc_queue)} 条")

        # 话题摘要（background 低频 LLM 摘要填充，默认空）
        topic_summary = getattr(snapshot, "topic_summary", "") or ""
        if isinstance(topic_summary, str) and topic_summary:
            # 标注摘要年龄：摘要刷新周期分钟级，而决策每秒级，
            # 过时摘要会误导话题方向，年龄信息让 LLM 自行权衡
            last_update = getattr(snapshot, "last_update_ms", 0)
            summary_at = getattr(snapshot, "topic_summary_at_ms", 0)
            age_ms = 0
            if isinstance(last_update, int) and isinstance(summary_at, int):
                age_ms = max(last_update - summary_at, 0)
            age_text = format_duration_ms(age_ms)
            parts.append(f"- 话题摘要: {topic_summary}（{age_text}前更新）")

        return "\n".join(parts) if parts else "- （暂无态势数据）"

    def _render_batch(self, batch: List[Any]) -> str:
        """将一批弹幕渲染为文本块（供 prompt 注入）。

        复用 MessageBuffer.render_batch_text 的格式约定（同一 message_type 在
        Planner 与 MessageBuffer 渲染输出一致），但保持独立实现以避免对
        MessageBuffer 的硬依赖（Planner 不关心缓冲逻辑）。

        每行末尾追加 ``[id:...]`` 消息编号——Planner 输出 ``reply_to`` 时必须
        引用该编号（回复关联 reply_to_message_id 的来源）；无 ID 的消息省略。

        Args:
            batch: 弹幕消息列表

        Returns:
            多行文本，每行一条弹幕；空批返回占位文本
        """
        if not batch:
            return "（本批无弹幕）"

        lines: List[str] = []
        for msg in batch:
            text = getattr(msg, "text", None) or str(msg)
            nickname = getattr(msg, "user_nickname", None) or getattr(msg, "user_id", None) or "观众"
            data_type = getattr(msg, "data_type", "text") or "text"
            spec = require_message_type(data_type)
            line = spec.prompt_template.format(text=text, nickname=nickname)
            message_id = getattr(msg, "message_id", None)
            if message_id:
                line = f"{line} [id:{message_id}]"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_history(history: Optional[List[Any]]) -> str:
        """将会话历史渲染为供 prompt 注入的文本块。

        统一契约：``history`` 元素为 ``ConversationMessage`` 鸭子类型，
        ``role`` 属性可能是 ``MessageRole`` 枚举（用 ``getattr(role, "value", str(role))``
        取值）或纯字符串；``content`` 为字符串。

        与 ``RoomStateLoop._format_history`` 同构：逐条渲染为 ``<role>: <content>`` 行，
        从旧到新用换行拼接。``history`` 为 ``None`` 或空列表时返回占位文本。

        Args:
            history: 最近对话历史列表。``None`` 或空列表时返回占位文本。

        Returns:
            多行文本，每行格式为 ``<role>: <content>``；空历史返回占位文本
        """
        if not history:
            return "（暂无对话历史）"

        lines: List[str] = []
        for msg in history:
            role = getattr(msg, "role", None)
            role_str = getattr(role, "value", str(role)) if role else "user"
            content = getattr(msg, "content", "") or ""
            # 主动发言写入历史的 user 占位（"（主动发言，主题：...）"）是系统元数据，
            # 不是观众弹幕——若原样渲染为 user 行，Planner 会误认为"该话题观众已聊过"，
            # 反重复原则被误触发导致每次主动发言都开新话题（内容不连续）。
            # 渲染为 [系统] 标注，语义与 RoomStateLoop._is_real_danmaku 过滤逻辑对齐。
            if role_str == "user" and content.startswith("（主动发言"):
                lines.append(f"[系统] {content}")
                continue
            lines.append(f"{role_str}: {content}")
        return "\n".join(lines)
