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
        ──▶ 消息序列 = [system] + 历史消息（user=观众 / assistant=主播，
            canonical 映射）+ 本批消息（user）+ 参考段（user，固定序列尾）
        ──▶ llm_service.chat_messages(messages, tools=工具列表, client_type=planner_profile)
        ──▶ 循环：assistant/tool 消息 append-only 追加在参考段之后
        ──▶ outcome dict（replied / speech / silent_reason / steps / tool_trace）
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler
from src.modules.llm.manager import normalize_tool_calls_for_protocol
from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation

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

#: 历史消息总字符预算：12000 由 32K 窗口倒推——最坏输入 = 系统提示词 1.5K
#: + 参考段 ≤2.6K（含游戏叙事满格）+ 工具观察 ≤16K（8 步 × 2000）+ 对话
#: ≤12K ≈ 32K 字符（约 21-23K token），留余量防越窗；正常 30 条历史约
#: 1.5-2.4K 字符，预算只兜长内容病理输入。与条数上限 history_limit=30
#: 构成双上限、先到先丢（成块丢最旧，见 canonical.drop_oldest_blocks）。
_HISTORY_CHAR_BUDGET: int = 12000

#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8


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


def _as_id_str(value: Any) -> str:
    """消息 ID 字段收敛：仅接受 str，其余（None/Mock 等）按空处理。"""
    return value if isinstance(value, str) else ""


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
            prompt_service: 提示词管理器，需提供 ``render(name, **vars) -> str``。
            room_state: 直播间态势规则层实例。
            tool_registry: 全局 ToolRegistry——ReAct 工具列表来源（信息收集/动作类工具）。
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

        reference_text = await self._assemble_reference(batch, history, rundown_text, forced, proactive, game_narrative)
        if reference_text is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "assembler_failed"
            return outcome

        tool_list = self._build_tool_list()
        # 消息序列：[system] + 历史（user/assistant 原生消息）+ 本批（user）+ 参考段（user，序列尾）；
        # ReAct 循环的 assistant/tool 消息 append-only 追加在参考段之后，绝不插中间。
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._build_dialogue_messages(batch, history))
        if reference_text:
            messages.append({"role": "user", "content": reference_text})
        elif len(messages) == 1:
            # 空窗兜底：对话与参考全空时保底一条 user 消息（部分协议要求非 system 消息存在）
            messages.append({"role": "user", "content": "（本窗无对话与参考内容）"})

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
            return self._prompt_service.render(
                self.TEMPLATE_NAME,
                behavior_style=behavior_style_render,
            )
        except Exception as e:
            self.logger.error(f"渲染 Planner 系统提示词失败: {e}", exc_info=True)
            self.last_failure = f"prompt_render_failed: {e}"
            return None

    async def _assemble_reference(
        self,
        batch: List[Any],
        history: Optional[List[Any]],
        rundown_text: Optional[str],
        forced: bool,
        proactive: bool,
        game_narrative: str,
    ) -> Optional[str]:
        """构造参考段（一条 user 消息，固定在消息序列尾）。

        内容 = 情境标注（强制/主动）+ 游戏叙事 + 组装器元数据段
        （环节描述 / 直播间快照 / 记忆召回）。对话内容不在此处——历史与本批
        走 canonical 映射的原生消息通道。
        """
        lines: List[str] = []
        if forced:
            lines.append("【情境】本批为强制回应触发（SC / 礼物 / 上舰）——观众付费点名，应优先回应。")
        if proactive:
            lines.append("【情境】本窗为主动发言触发（冷场/定时）——弹幕可能为空，基于房间态势决定是否主动开口。")
        if game_narrative:
            # 叙事是注入到参考段的单项内容，同样受单项 2000 字符帽约束
            # （canonical.SINGLE_ITEM_MAX_CHARS，与消息单项截断同一规则）。
            lines.append(f"【游戏叙事】{canonical.truncate_item(game_narrative)}")
        situation_text = "\n".join(lines)

        if not self._context_enabled:
            # 裸消息路径：跳过环境快照与记忆召回，仅保留情境标注与叙事
            return situation_text

        snapshot = self._room_state.get_snapshot()
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
                stage_descriptions=rundown_text or "",
                environment=env_block,
                memory_recall_section=memory_recall_section,
            )
            assembled_text = self._assembler.assemble(assembler_inputs)
        except Exception as e:
            self.logger.error(f"PlannerAssembler 组装失败: {e}", exc_info=True)
            self.last_failure = f"assembler_failed: {e}"
            return None

        parts = [block for block in (situation_text, assembled_text) if block]
        return "\n\n".join(parts)

    def _build_dialogue_messages(
        self,
        batch: List[Any],
        history: Optional[List[Any]],
    ) -> List[Dict[str, Any]]:
        """历史 + 本批 → 原生消息序列（user=观众 / assistant=主播）。

        均经 canonical 单一映射（批与历史同形）；历史尾部与本批同源的消息
        剔除——弹幕先落库再进入决策，窗口尾部会与本批重复（按消息 ID 精确
        匹配、原始文本兜底；仅剔尾部连续命中段，更早的同文历史保留为有效
        上下文）。去重在 canonical 化之前做：昵称渲染差异不影响判定。
        """
        batch_messages = [canonical.batch_item_to_message(msg) for msg in batch]
        if not history:
            return batch_messages

        batch_ids = {_as_id_str(getattr(msg, "message_id", None)) for msg in batch} - {""}
        batch_texts = {(getattr(msg, "text", "") or "").strip() for msg in batch} - {""}
        end = len(history)
        while end > 0:
            turn = history[end - 1]
            turn_id = _as_id_str(getattr(turn, "message_id", None))
            turn_text = (getattr(turn, "content", "") or "").strip()
            if (turn_id and turn_id in batch_ids) or (turn_text and turn_text in batch_texts):
                end -= 1
            else:
                break

        history_messages = [canonical.turn_to_message(turn) for turn in history[:end]]
        # 字符预算（成块丢最旧，与条数上限 history_limit=30 双上限先到先丢）：
        # 条数上限在历史读取处已生效，此处补字符维度——超预算时从最旧整条丢弃。
        history_messages = canonical.drop_oldest_blocks(history_messages, _HISTORY_CHAR_BUDGET)
        return history_messages + batch_messages

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
        - 无 memory / 无 hits / 异常 → 空串；Assembler 对空段整段省略
        """
        if self._memory is None:
            return ""

        topic_summary = (getattr(snapshot, "topic_summary", "") or "").strip()
        batch_messages = [canonical.batch_item_to_message(msg) for msg in batch]
        batch_head = "\n".join(m["content"] for m in batch_messages).strip()[:_RECALL_QUERY_BATCH_CHARS]
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
