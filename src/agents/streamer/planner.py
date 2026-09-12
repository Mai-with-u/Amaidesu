"""Planner - 主播 Agent 决策核心（ReAct 循环）

职责（Agent 内部件，**不是工具**）：
- 决策主体：每个决策窗内跑一次有界 ReAct 循环——查信息（registry 工具）
  → 决定说不说 → 调 reply 局部工具收尾
- reply 是循环内的局部工具（``tools/reply_tool.py`` 的 Provider 直连，
  不进 ToolRegistry——Agent 内部件协议）
- 自然终止（LLM 无工具调用）= 本轮不说话

核心契约：
1. **人设行为准则注入**：系统提示词注入 ``$behavior_style``（行动准则：
   何时参与聊天、如何观察局面、何时保持安静）——来自 [persona].behavior_style。
2. **身份/表达人设隔离**：Planner 不传 ``$personality`` / ``$style_constraints`` /
   ``$bot_name``——表达侧三件套仅注入 Replyer。behavior_style = 决策侧。
3. **高质量模型**：Planner profile 默认 ``planner``（StreamerAgent 装配期硬编码
   传入 `_PROFILE_PLANNER`）——ReAct 决策核心做工具编排与表达意图构思，
   质量敏感（乱调工具/意图偏差的代价高于延迟）。
4. **工具列表**：全局 ToolRegistry 动态拉取 + reply 局部工具 function 定义；
   过滤 provider=="streamer" 的 spec（Agent 内部协议防重入）。
5. **观察作为观察返回**：工具结果以 OpenAI ``tool`` role + ``tool_call_id`` 关联重新写入。
6. **有界循环**：``planner_max_steps``（默认 8）防失控；直播节奏要求快进快出。
7. **降级安全**：LLM 异常 / 超步 / reply 不可用均产出 silent outcome（不抛异常）。

数据流：
    batch + room_state.snapshot + history + forced/proactive + behavior_style
        ──▶ render('amaidesu_planner_react')（系统提示词）
        ──▶ 首轮 user 消息 = context_block（组装器/裸消息路径）+ 情境标注
        ──▶ llm_service.chat_messages(messages, tools=工具列表, client_type=planner_profile)
        ──▶ 循环：tool_calls 串行执行（reply → 局部 Provider；其余 → registry）
        ──▶ outcome dict（replied / speech / silent_reason / steps / tool_trace）
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from src.modules.config.schemas.base import BaseConfig
from src.modules.context.assembler import AssemblerInputs, PlannerAssembler
from src.modules.context.snapshot import EnvironmentBlock
from src.modules.llm.manager import normalize_tool_calls_for_protocol
from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation
from src.modules.types.message_type import require_message_type

from .room_state import RoomState, RoomStateSnapshot
from .thinking_stream import ThinkingStreamContext
from .tools.rundown_tool import build_rundown_control_function_def

__all__ = ["Planner"]


#: 默认记忆召回条数（Planner 每轮决策注入的 hit 上限）。
#: 配置面不允许暴露——记忆质量先稳定再调参，避免污染用户配置文件。
_DEFAULT_RECALL_TOP_K: int = 3

#: 弹幕批次拼接到召回 query 的最大字符数（控 query 长度，避免污染召回）。
_RECALL_QUERY_BATCH_CHARS: int = 200

#: 单条召回 hit 文本截断长度（控制 prompt 体积）。
_RECALL_HIT_TEXT_CHARS: int = 80

#: 单条工具观察作为观察返回的最大字符数（超长观察截断，控上下文体积）。
_OBSERVATION_MAX_CHARS: int = 2000

#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8

#: 会话历史角色 → 直播流渲染标签：直播流是多对一弹幕墙，
#: 透传 LLM 角色标记（user:/assistant:）会污染 user 消息内的文本结构
_HISTORY_ROLE_LABELS: Dict[str, str] = {
    "user": "观众",
    "assistant": "主播",
    "system": "系统",
}


class _PlannerConfig(BaseConfig):
    """Planner 配置 Schema（StreamerConfig 最小子集）。

    仅声明 Planner 直接使用的字段；当从完整 StreamerConfig 加载时，
    BaseConfig.from_dict() 的漂移检测会自动剥离其余字段。

    planner_max_steps 从 StreamerConfig.planner_max_steps 注入；LLM profile
    用途名（"planner"/"replyer"/"summary"）由 StreamerAgent 装配期硬编码，
    不再作为配置字段透传。
    """

    planner_max_steps: int = _DEFAULT_MAX_STEPS


def _spec_to_fn_def(spec: Any) -> Dict[str, Any]:
    """ToolSpec → OpenAI function def（name 用派生全名，与调用契约一致）。"""
    entry: Dict[str, Any] = {"name": spec.full_name, "description": spec.description}
    if spec.parameters_schema is not None:
        entry["parameters"] = spec.parameters_schema
    return entry


def _tool_call_parts(call: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """解析 tool_call 的 (name, arguments)，兼容扁平与完整 OpenAI 形态。"""
    fn = call.get("function") if isinstance(call.get("function"), dict) else call
    name = str(fn.get("name", "") or "")
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args


class Planner:
    """主播 Agent 决策核心：ReAct 循环（查→想→说）。

    非线程安全；仅在 StreamerAgent 的单一 asyncio 事件循环内使用。
    通过 ``plan()`` 方法驱动，**不订阅 EventBus**——它是 Agent 内部子组件。
    """

    #: Planner 专用系统提示词模板名（零表达人设注入）
    TEMPLATE_NAME: str = "amaidesu_planner_react"

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
        reply_provider: Any = None,
        elapsed_live_provider: Optional[Callable[[], Optional[int]]] = None,
        rundown_provider: Any = None,
    ) -> None:
        """初始化 Planner。

        Args:
            config: 配置字典或已解析对象（profile / planner_max_steps）。
            llm_service: LLM 管理器，需提供
                ``async chat_messages(messages, tools=..., client_type=...) -> LLMResponse``。
            prompt_service: 提示词管理器，需提供 ``render_safe(name, **vars) -> str``。
            room_state: 直播间态势规则层实例。
            tool_registry: 全局 ToolRegistry——ReAct 工具列表来源（信息收集/动作工具）。
            memory: 记忆后端（鸭子类型 ``MemoryProvider``）；None 时无记忆决策。
            recall_top_k: 每轮注入 prompt 的最大命中条数。
            context_enabled: 组装器路径开关；False 时以直播流窗口文本为 context_block。
            behavior_style: 人设行为准则（决策侧）。
            reply_provider: reply 局部工具的 Provider（ReplyToolProvider）；
                start 前由 StreamerAgent 经 ``bind_reply_provider`` 注入也可。
            elapsed_live_provider: 整场开播时长查询（``RundownState.get_elapsed_live_ms``，
                返回 Unix 毫秒或 None=未开播）；None 时快照不含开播时长行。
        """
        if config is None:
            self.typed_config = _PlannerConfig()
        elif hasattr(config, "planner_max_steps"):
            self.typed_config = _PlannerConfig(
                planner_max_steps=getattr(config, "planner_max_steps", _DEFAULT_MAX_STEPS),
            )
        elif isinstance(config, dict):
            self.typed_config = _PlannerConfig.from_dict(config)
        else:
            self.typed_config = _PlannerConfig.from_dict(dict(config))

        # LLM profile 用途名由 StreamerAgent 装配期硬编码传入（_PROFILE_PLANNER）；
        # 本字段保留以兼容 Planner 内部工具列表与日志输出（profile 名仅展示用）。
        self.profile: str = getattr(config, "profile", "llm") if config is not None else "llm"
        self.max_steps: int = self.typed_config.planner_max_steps

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._room_state = room_state
        self._tool_registry = tool_registry
        self._memory = memory
        self._recall_top_k = recall_top_k
        self._context_enabled = context_enabled
        self._behavior_style: str = behavior_style or ""
        self._reply_provider = reply_provider
        self._elapsed_live_provider = elapsed_live_provider
        self._rundown_provider = rundown_provider

        self._assembler = PlannerAssembler()

        # 可观测副产品（决策事件消费；每轮 plan() 入口重置）
        self.last_raw_content: str = ""
        self.last_request_id: Optional[str] = None
        self.last_failure: Optional[str] = None

        self.logger = get_logger("Planner")

    def bind_reply_provider(self, provider: Any) -> None:
        """注入 reply 局部工具 Provider（StreamerAgent start 时绑定）。"""
        self._reply_provider = provider

    def bind_elapsed_live_provider(self, provider: Callable[[], Optional[int]]) -> None:
        """注入开播时长查询（StreamerAgent 构造 RundownState 后绑定，同 bind 模式）。"""
        self._elapsed_live_provider = provider

    def bind_rundown_provider(self, provider: Any) -> None:
        """注入流程单控制 Provider（StreamerAgent 装配 RundownState 后绑定）。

        绑定且流程单激活时，工具列表追加 ``rundown_control``——Agent 自主推进环节。
        """
        self._rundown_provider = provider

    # ==================== 主入口 ====================

    async def plan(
        self,
        batch: List[Any],
        *,
        forced: bool = False,
        proactive: bool = False,
        history: Optional[List[Any]] = None,
        rundown_text: Optional[str] = None,
        game_narrative: str = "",
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
    ) -> Dict[str, Any]:
        """对一个决策窗跑 ReAct 循环，产出 outcome dict。

        Args:
            batch: 本批弹幕列表（可为空——proactive 触发时基于房间状态独立判断）。
            forced: 是否为强制回应批次（SC / 礼物 / 上舰）。
            proactive: 是否为主动发言触发（冷场/定时；batch 可能为空）。
            history: 最近对话历史（可选；反重复用）。
            rundown_text: 当前流程单渲染文本（可选）。
            game_narrative: 游戏叙事文本（game.* 事件摘要；可主动经工具查询更多）。
            thinking: 思考流上下文（可选；提供时每次 LLM 调用的 reasoning
                增量经旁路通道外发）。
            round_id: 决策轮次 ID（工具调用经 ToolInvocation.round_id 透传到
                tool.result 事件，供观察器归属；空串表示无轮次关联）。

        Returns:
            outcome dict：
            - replied: 是否完成说话（reply 工具成功）
            - speech / emotion / emotion_intensity: reply 产出（StreamerAgent 送发言管线）
            - target / reply_to / topic_summary / reply_guidance / confidence: reply 意图
            - silent_reason: 未说话原因（natural=自然终止 / max_steps=超步 / llm_error 等）
            - steps / tool_trace: 循环步数与执行过的工具名序列（可观测）
            - error: 异常原因（无异常为 None）
            - reply_payload: reply 工具完整结果（发言管线消费）
        """
        self.last_raw_content = ""
        self.last_request_id = None
        self.last_failure = None

        outcome: Dict[str, Any] = {
            "replied": False,
            "speech": None,
            "emotion": None,
            "emotion_intensity": 0.5,
            "target": None,
            "reply_to": None,
            "topic_summary": "",
            "reply_guidance": "",
            "confidence": None,
            "silent_reason": None,
            "steps": 0,
            "tool_trace": [],
            "error": None,
            "reply_payload": None,
        }

        system_prompt = self._render_system_prompt()
        if system_prompt is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "prompt_render_failed"
            return outcome

        context_block = await self._assemble_context(batch, history, rundown_text)
        if context_block is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "assembler_failed"
            return outcome

        user_message = self._render_situational_message(context_block, forced, proactive, game_narrative)

        tool_list = self._build_tool_list()
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        steps = 0
        while steps < self.max_steps:
            steps += 1
            outcome["steps"] = steps

            try:
                response = await self._llm_service.chat_messages(
                    messages=messages,
                    tools=tool_list,
                    client_type=self.profile,
                    on_delta=thinking.callback_for("planner", steps) if thinking else None,
                )
            except Exception as e:
                self.logger.warning(f"Planner LLM 调用异常: {e}")
                self.last_failure = f"llm_error: {e}"
                outcome["error"] = self.last_failure
                outcome["silent_reason"] = "llm_error"
                return outcome

            if not getattr(response, "success", False):
                error = getattr(response, "error", None) or "unknown"
                self.last_failure = f"llm_failed: {error}"
                outcome["error"] = self.last_failure
                outcome["silent_reason"] = "llm_failed"
                return outcome

            self.last_raw_content = (getattr(response, "content", None) or "")[:2000]
            self.last_request_id = getattr(response, "request_id", None) or None

            tool_calls = list(getattr(response, "tool_calls", None) or [])
            assistant_content = getattr(response, "content", None)
            if assistant_content is not None and not isinstance(assistant_content, str):
                # OpenAI 协议要求 content 为 string/null，部分 client 返回结构化内容
                assistant_content = json.dumps(assistant_content, ensure_ascii=False, default=str)
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_msg["tool_calls"] = normalize_tool_calls_for_protocol(tool_calls)
            messages.append(assistant_msg)

            # 自然终止：LLM 不再调用任何工具 = 本轮不说话
            if not tool_calls:
                outcome["silent_reason"] = "natural"
                return outcome

            replied = False
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name, args = _tool_call_parts(call)
                if not name:
                    continue
                outcome["tool_trace"].append(name)

                if name == "streamer_reply":
                    observation, replied = await self._invoke_reply(args, outcome, thinking=thinking, round_id=round_id)
                elif name == "rundown_control" and self._rundown_provider is not None:
                    observation = self._invoke_rundown_control(args)
                else:
                    observation = await self._invoke_registry_tool(name, args, round_id=round_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "") or ""),
                        "content": observation,
                    }
                )
                if replied:
                    return outcome

        # 超步：未完成说话即收场
        outcome["silent_reason"] = "max_steps"
        self.last_failure = f"max_steps_exceeded: {self.max_steps}"
        return outcome

    # ==================== 提示词与上下文 ====================

    def _render_system_prompt(self) -> Optional[str]:
        """渲染 ReAct 系统提示词（行为准则 + 工具守则）。"""
        behavior_style_render = self._behavior_style or "（未配置行动准则，请依据直播间态势与对话历史自行决策）"
        try:
            return self._prompt_service.render_safe(
                self.TEMPLATE_NAME,
                behavior_style=behavior_style_render,
            )
        except Exception as e:
            self.logger.error(f"渲染 Planner 系统提示词失败: {e}", exc_info=True)
            self.last_failure = f"prompt_render_failed: {e}"
            return None

    async def _assemble_context(
        self,
        batch: List[Any],
        history: Optional[List[Any]],
        rundown_text: Optional[str],
    ) -> Optional[str]:
        """组装决策上下文块（组装器路径 / 裸消息路径）。"""
        snapshot = self._room_state.get_snapshot()
        danmaku_text = self._render_batch(batch)
        batch_texts = {
            (getattr(msg, "text", "") or "").strip() for msg in batch if (getattr(msg, "text", "") or "").strip()
        }
        history_text = self._render_history(history, exclude_tail_texts=batch_texts)

        recent_chat_parts: List[str] = []
        if history_text and history_text != "（暂无对话历史）":
            recent_chat_parts.append(history_text)
        if danmaku_text and danmaku_text != "（本批无弹幕）":
            recent_chat_parts.append(danmaku_text)
        recent_chat_window = "\n\n".join(recent_chat_parts) if recent_chat_parts else "（暂无）"

        if not self._context_enabled:
            return recent_chat_window

        current_ms = now_ms()
        duration_so_far_ms = 0
        if self._elapsed_live_provider is not None:
            try:
                duration_so_far_ms = int(self._elapsed_live_provider() or 0)
            except Exception as exc:
                self.logger.warning(f"读取开播时长失败（按 0 处理，快照省略该行）: {exc}")
        # key_changes 留空：RoomState.topics 是字符级词频（落库统计口径），
        # 单字进 prompt 是噪声，话题信息由 unread_summary（LLM 摘要）承载
        env_block = EnvironmentBlock(
            minute_bucket_ms=(current_ms // 60000) * 60000,
            duration_so_far_ms=duration_so_far_ms,
            current_stage_label=None,
            unread_summary=getattr(snapshot, "topic_summary", "") or "",
            key_changes=[],
        )

        memory_recall_section = await self._recall_memory(snapshot, batch)

        try:
            assembler_inputs = AssemblerInputs(
                persona="",
                tool_definitions_block="",
                current_stage_label=None,
                stage_descriptions=rundown_text or "",
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
        return snapshot_assembled.rendered_text

    def _render_situational_message(
        self,
        context_block: str,
        forced: bool,
        proactive: bool,
        game_narrative: str,
    ) -> str:
        """情境标注 + 上下文块 → 首轮 user 消息。"""
        lines: List[str] = []
        if forced:
            lines.append("【情境】本批为强制回应触发（SC / 礼物 / 上舰）——观众付费点名，应优先回应。")
        if proactive:
            lines.append("【情境】本窗为主动发言触发（冷场/定时）——弹幕可能为空，基于房间态势决定是否主动开口。")
        if game_narrative:
            lines.append(f"【游戏叙事】{game_narrative}")
        if lines:
            lines.append("")
        lines.append(context_block)
        return "\n".join(lines)

    # ==================== 工具列表与执行 ====================

    def _build_tool_list(self) -> List[Dict[str, Any]]:
        """LLM 工具列表 = 注册表按可见名单计算（for_agent="streamer"）。

        唯一例外：rundown_control 是动态工具——按流程单激活状态条件追加
        （注册表条目已在 for_agent 结果中，跳过防重）。
        """
        tool_list: List[Dict[str, Any]] = []
        # 流程单激活时追加 rundown_control——环节推进是决策脑的职责（推进权归 Agent）
        if self._rundown_provider is not None and self._rundown_provider.is_active():
            tool_list.append(build_rundown_control_function_def())
        if self._tool_registry is None:
            return tool_list
        try:
            specs = self._tool_registry.list_tools(for_agent="streamer")
        except Exception as e:
            self.logger.warning(f"Planner 拉取工具列表失败（本轮仅保留 rundown_control）: {e}")
            return tool_list
        for spec in specs:
            # rundown_control 由上面的激活条件追加（动态工具的已知例外，防重复条目）
            if getattr(spec, "provider", "") == "rundown":
                continue
            tool_list.append(_spec_to_fn_def(spec))
        return tool_list

    async def _invoke_reply(
        self,
        args: Dict[str, Any],
        outcome: Dict[str, Any],
        *,
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
    ) -> tuple[str, bool]:
        """执行 reply 工具（经 ToolRegistry 统一调用）；成功时把产出写进 outcome 并返回 (观察, replied=True)。

        统一调用路径：reply 与其他工具一样经 ``registry.invoke``（观测/停用/
        熔断复用既有机制，tool.result 事件照发）。thinking 回调槽位机制保留：
        调用前把 replyer 阶段回调设到 Provider 的临时槽位，调用后立即清理
        （一次性语义，防跨轮残留）——槽位挂在 Provider 实例上，不经注册表。
        """
        if self._tool_registry is None:
            return json.dumps(
                {"ok": False, "error": "reply 工具不可用（tool_registry 未注入）"}, ensure_ascii=False
            ), False
        set_thinking = getattr(self._reply_provider, "set_thinking_callback", None)
        try:
            if set_thinking is not None:
                set_thinking(thinking.callback_for("replyer", 1) if thinking else None)
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name="streamer_reply", arguments=args, source="planner-react", round_id=round_id)
            )
        except Exception as e:
            self.logger.warning(f"reply 工具执行异常: {e}", exc_info=True)
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), False
        finally:
            if set_thinking is not None:
                set_thinking(None)

        if not result.success:
            return json.dumps({"ok": False, "error": result.error_message or "reply 失败"}, ensure_ascii=False), False

        payload = result.structured_content if isinstance(result.structured_content, dict) else {}
        emotion = payload.get("emotion") if isinstance(payload.get("emotion"), dict) else {}
        outcome["replied"] = True
        outcome["speech"] = payload.get("speech")
        outcome["emotion"] = emotion.get("name")
        outcome["emotion_intensity"] = emotion.get("intensity", 0.5)
        outcome["reply_payload"] = payload
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        outcome["target"] = metadata.get("target") or args.get("target")
        outcome["topic_summary"] = str(args.get("topic_summary", "") or "")
        outcome["reply_guidance"] = str(args.get("reply_guidance", "") or "")
        try:
            outcome["confidence"] = float(args.get("confidence")) if args.get("confidence") is not None else None
        except (TypeError, ValueError):
            outcome["confidence"] = None
        reply_to = args.get("target")
        outcome["reply_to"] = reply_to if isinstance(reply_to, str) and reply_to else None

        self.logger.info(f"Planner ReAct 收尾：reply 成功 (target={outcome['target']!r}, steps={outcome['steps']})")
        return json.dumps({"ok": True, "speech_delivered": True}, ensure_ascii=False), True

    def _invoke_rundown_control(self, args: Dict[str, Any]) -> str:
        """执行 rundown_control 局部工具（状态机方法同步非阻塞），返回观察 JSON 文本。"""
        if self._rundown_provider is None:
            return json.dumps({"ok": False, "error": "rundown_control 不可用（Provider 未绑定）"}, ensure_ascii=False)
        try:
            return self._rundown_provider.invoke(args)
        except Exception as e:
            self.logger.warning(f"rundown_control 执行异常: {e}", exc_info=True)
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

    async def _invoke_registry_tool(self, name: str, args: Dict[str, Any], round_id: str = "") -> str:
        """经 ToolRegistry 执行工具调用，返回观察 JSON 文本。"""
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        try:
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name=name, arguments=args, source="planner-react", round_id=round_id)
            )
        except Exception as e:
            self.logger.warning(f"工具 '{name}' 执行异常: {e}", exc_info=True)
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

        if result.success:
            data = result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
        else:
            data = {"ok": False, "error": result.error_message or "工具执行失败"}
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) > _OBSERVATION_MAX_CHARS:
            text = text[:_OBSERVATION_MAX_CHARS] + "…（截断）"
        return text

    # ==================== 记忆召回与渲染 ====================

    async def _recall_memory(
        self,
        snapshot: RoomStateSnapshot,
        batch: List[Any],
    ) -> str:
        """调记忆后端召回相关历史片段，格式化为 prompt 注入文本。

        - 基础 query = RoomState.topic_summary（非空时）+ 本批弹幕前 200 字符
        - 无 memory / 无 hits / 异常 → 空串；Assembler 兜底 "（暂无）"
        """
        if self._memory is None:
            return ""

        topic_summary = (getattr(snapshot, "topic_summary", "") or "").strip()
        batch_text = self._render_batch(batch)
        batch_head = (batch_text or "").strip()[:_RECALL_QUERY_BATCH_CHARS]
        query_parts = [p for p in (topic_summary, batch_head) if p]
        query = "\n".join(query_parts) if query_parts else ""
        if not query:
            return ""

        try:
            hits: List[MemoryHit] = await self._memory.recall(query, top_k=self._recall_top_k)
        except Exception as exc:
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

    def _render_batch(self, batch: List[Any]) -> str:
        """将一批弹幕渲染为文本块（每行带 [id:...] 编号，供 reply target 引用）。"""
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
    def _render_history(history: Optional[List[Any]], exclude_tail_texts: Optional[set] = None) -> str:
        """将会话历史渲染为直播流文本块（中文角色标签；主动发言占位标注 [系统]）。

        exclude_tail_texts：从尾部剔除与本批弹幕同文本的消息——弹幕先落库
        再进入决策，窗口内会与本批渲染重复；仅剔尾部连续命中段，更早的同文
        历史保留（高频弹幕如"666"的更早记录仍是有效上下文）。
        """
        if not history:
            return "（暂无对话历史）"

        excluded = exclude_tail_texts or set()
        end = len(history)
        while end > 0:
            tail_content = (getattr(history[end - 1], "content", "") or "").strip()
            if tail_content and tail_content in excluded:
                end -= 1
            else:
                break

        lines: List[str] = []
        for msg in history[:end]:
            role = getattr(msg, "role", None)
            role_str = getattr(role, "value", str(role)) if role else "user"
            content = getattr(msg, "content", "") or ""
            # 主动发言写入历史的 user 占位是系统元数据，非观众弹幕——
            # 渲染为 [系统] 标注避免反重复误判
            if role_str == "user" and content.startswith("（主动发言"):
                lines.append(f"[系统] {content}")
                continue
            label = _HISTORY_ROLE_LABELS.get(role_str, role_str)
            lines.append(f"{label}: {content}")
        return "\n".join(lines)
