"""Planner - 主播 Agent 决策核心（ReAct 循环）

职责（Agent 内部件，**不是工具**）：
- 决策主体：每个决策窗内跑一次有界 ReAct 循环——查信息（registry 工具）
  → 决定说不说 → 调 reply 工具收尾
- 全部工具调用统一经 ToolRegistry（``registry.invoke``），工具列表统一
  来自 ``list_tools(for_agent="streamer")``——单一路径，无局部直连分支
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
        ──▶ llm_service.generate(messages, tools=工具列表, profile=PLANNER_PROFILE)
        ──▶ 循环：assistant/tool 消息 append-only 追加在参考段之后
        ──▶ outcome dict（replied / speech / silent_reason / steps / tool_trace）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler, age_text
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation

from .room_state import RoomState
from .thinking_stream import ThinkingStreamContext

__all__ = ["Planner"]


#: Planner 绑定的 LLM profile（代码显式声明，配置不承载绑定，无静默兜底）。
PLANNER_PROFILE: str = "planner"


def _render_observation(data: Any) -> str:
    """完整保留工具结果，让模型读取所有字段及其真实值。"""
    return json.dumps(data, ensure_ascii=False, default=str)


#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8


@dataclass
class _Delegation:
    """一次已受理的委派：交给谁、主播自己的原话、何时交出，以及身体是否已上报。"""

    agent: str
    instruction: str
    task_id: str
    at_ms: int
    report_kind: str = ""  # delivery=已交付 / escalation=卡住待定夺；空=还在做
    reported_ms: int = 0


class _PlannerConfig(BaseConfig):
    """Planner 配置 Schema（StreamerConfig 最小子集）。

    仅声明 Planner 直接使用的字段；当从完整 StreamerConfig 加载时，
    BaseConfig.from_dict() 的漂移检测会自动剥离其余字段。

    planner_max_steps 从 StreamerConfig.planner_max_steps 注入；LLM profile
    用途名（"planner"/"replyer"/"summary"）由 StreamerAgent 装配期硬编码，
    不再作为配置字段透传。
    """

    planner_max_steps: int = Field(default=_DEFAULT_MAX_STEPS, title="决策最大步数")


def _spec_to_fn_def(spec: Any) -> Dict[str, Any]:
    """ToolSpec → OpenAI function def（name 用派生全名，与调用契约一致）。"""
    entry: Dict[str, Any] = {"name": spec.full_name, "description": spec.description}
    if spec.parameters_schema is not None:
        entry["parameters"] = spec.parameters_schema
    return entry


def _as_id_str(value: Any) -> str:
    """消息 ID 字段收敛：仅接受 str，其余（None/Mock 等）按空处理。"""
    return value if isinstance(value, str) else ""


def _same_json_document(text: str, structured: Any) -> bool:
    """正文是否只是结构化结果的 JSON 序列化（解析后逐字段相等）。"""
    if not isinstance(structured, dict) or not structured or not text.lstrip().startswith("{"):
        return False
    try:
        return json.loads(text) == structured
    except json.JSONDecodeError:
        return False


#: 付费消息类型 → 情境标注用词（强制回应的情境按本批实际消息类型说，不一律当付费）
_PAID_TYPE_LABELS: Dict[str, str] = {"super_chat": "SC", "gift": "礼物", "guard": "上舰"}


def _forced_situation(batch: List[Any]) -> str:
    """强制回应批次的情境标注：本批真有付费消息才说"付费点名"。

    控制台点名同样强制回应，但它是以观众身份说的话、不是 SC——当成付费
    会让主播对每句调试话都"谢谢老板 SC"。
    """
    paid = [
        _PAID_TYPE_LABELS[message_type]
        for message_type in dict.fromkeys(getattr(msg, "message_type", "") for msg in batch)
        if message_type in _PAID_TYPE_LABELS
    ]
    if paid:
        return f"【情境】本批为强制回应（含付费消息：{' / '.join(paid)}）——观众付费点名，应优先回应。"
    # 控制台点名就是观众的话，只是必须回应；写成"运营"会让模型把它降级成仅供参考的建议
    return (
        "【情境】本批为强制回应（控制台以观众身份点名的消息：必须回应，但不是 SC / 礼物 / 上舰，没有人付费）"
        "——优先回应消息本身，把它当成观众的要求认真对待，不要致谢付费。"
    )


def _reply_last(tool_calls: List[Any]) -> List[Any]:
    """同一步的工具调用按原序执行，只把说话（streamer_reply）挪到最后。"""
    others = [call for call in tool_calls if not (isinstance(call, dict) and call.get("name") == "streamer_reply")]
    replies = [call for call in tool_calls if isinstance(call, dict) and call.get("name") == "streamer_reply"]
    return others + replies


def _tool_call_parts(call: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """解析中立扁平 tool_call（id/name/arguments）的 (name, arguments)。"""
    name = str(call.get("name", "") or "")
    args = call.get("arguments")
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
        viewer_repo: Any = None,
        context_enabled: bool = True,
        behavior_style: str = "",
        reply_provider: Any = None,
        elapsed_live_provider: Optional[Callable[[], Optional[int]]] = None,
    ) -> None:
        """初始化 Planner。

        Args:
            config: 配置字典或已解析对象（planner_max_steps）。
            llm_service: LLM 管理器，需提供
                ``async generate(messages, *, tools=..., profile=...) -> payload.Response``。
            prompt_service: 提示词管理器，需提供 ``render(name, **vars) -> str``。
            room_state: 直播间态势规则层实例。
            tool_registry: 全局 ToolRegistry——ReAct 工具列表来源（信息收集/动作类工具）。
            memory: 观众画像/事实读写服务（鸭子类型 ``SimpleMemory``）；None 时无画像注入。
            viewer_repo: ``ViewerRepo``（观众统计仓储）——画像注入时经它实时取
                昵称（昵称会漂移不进画像文本）；None 时注入行回退 platform/user_id。
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

        # LLM profile 绑定为代码显式常量（PLANNER_PROFILE），不读配置、无兜底；
        # 本字段保留供内部工具列表与日志输出使用。
        self.profile: str = PLANNER_PROFILE
        self.max_steps: int = self.typed_config.planner_max_steps

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._room_state = room_state
        self._tool_registry = tool_registry
        self._memory = memory
        self._viewer_repo = viewer_repo
        self._context_enabled = context_enabled
        self._behavior_style: str = behavior_style or ""
        self._reply_provider = reply_provider
        self._elapsed_live_provider = elapsed_live_provider
        # 每个执行 Agent 最近一次受理的委派：原话、时间、身体是否已上报。实测半小时委派 27 次、
        # 目标来回反转——主播看不到身体手上正做着什么，就会把每条弹幕都改派成新目标
        self._delegations: Dict[str, _Delegation] = {}

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

    def note_game_report(self, game: str, report_kind: str) -> None:
        """游戏侧上报（交付/卡住待定夺）到达：把对应执行 Agent 手头那次委派标成已上报。

        游戏 Agent 以自己的注册名作为上报的 game 字段，按名字对上即可。
        """
        delegation = self._delegations.get(game)
        if delegation is not None and report_kind in {"delivery", "escalation"}:
            delegation.report_kind = report_kind
            delegation.reported_ms = now_ms()

    def _render_delegations(self, current_ms: int) -> str:
        """我手头在游戏里做的事：每个游戏最近一次开始做的事、距今多久、做完了还是卡住了。

        用第一人称写：对观众来说只有一个主播，游戏里的操作也是"我"在做，
        写成"交给谁""身体在做"会让表达里冒出"活已经派下去了"这种让观众困惑的话。
        """
        rows: List[str] = []
        for delegation in self._delegations.values():
            game = f"在 {delegation.agent} 里" if delegation.agent else "在游戏里"
            head = (
                f"{age_text(current_ms - delegation.at_ms)}{game}开始做（任务 {delegation.task_id}）："
                f"{delegation.instruction}"
            )
            if delegation.report_kind == "delivery":
                state = f"已在{age_text(current_ms - delegation.reported_ms)}做完，结果见【游戏叙事】。"
            elif delegation.report_kind == "escalation":
                state = (
                    f"在{age_text(current_ms - delegation.reported_ms)}卡住了，正等我和观众一起定夺（见【游戏叙事】）。"
                )
            else:
                state = "还在做，还没做完。"
            rows.append(f"- {head}\n  {state}")
        return "\n".join(rows)

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
        body_narrative: str = "",
        reminders: str = "",
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        game_chat: str = "",
    ) -> Dict[str, Any]:
        """对一个决策窗跑 ReAct 循环，产出 outcome dict。

        Args:
            batch: 本批弹幕列表（可为空——proactive 触发时基于房间状态独立判断）。
            forced: 是否为强制回应批次（SC / 礼物 / 上舰）。
            proactive: 是否为主动发言触发（冷场/定时/环节/游戏动静；batch 可能为空）。
            history: 最近对话历史（可选；反重复用）。
            rundown_text: 当前流程单渲染文本（可选）。
            game_narrative: 游戏叙事文本（game.* 事件摘要；可主动经工具查询更多）。
            body_narrative: 身体侧近况（game.body.* 摘要：被袭击/死亡/重生/紧急反应）。
            reminders: 运营递话留言（【运营提醒】段注入参考块；消费即送达）。
            game_chat: 游戏里的聊天（game.chat.* 摘要：其他玩家的话与系统消息）。
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
            - reply_duration_ms: reply 工具调用累计耗时（毫秒；未调用为 0）
            - reply_failures: reply 工具失败次数（成功轮为 0）
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
            "reply_duration_ms": 0,
            "reply_failures": 0,
        }

        system_prompt = self._render_system_prompt()
        if system_prompt is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "prompt_render_failed"
            return outcome

        reference_text = await self._assemble_reference(
            batch, history, rundown_text, forced, proactive, game_narrative, body_narrative, reminders, game_chat
        )
        if reference_text is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "assembler_failed"
            return outcome

        tool_list = self._build_tool_list()
        # 消息序列：[system] + 历史（user/assistant 原生消息）+ 本批（user）+ 参考段（user，序列尾）；
        # ReAct 循环的 assistant/tool 消息 append-only 追加在参考段之后，绝不插中间。
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._build_dialogue_messages(batch, history))
        # 游戏委派保留当前来源原句，避免把具体物品名称或禁用条件只剩下一层口头概括。
        source_dialogue = [
            message for message in self._build_dialogue_messages(batch, []) if message.get("role") == "user"
        ]
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
                response = await self._llm_service.generate(
                    messages,
                    tools=tool_list,
                    profile=PLANNER_PROFILE,
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

            self.last_raw_content = getattr(response, "content", None) or ""
            self.last_request_id = getattr(response, "request_id", None) or None

            # engine 返回的中立 ToolCall（扁平对象）收敛为内部扁平 dict，供本模块消费与协议喂回
            tool_calls = [
                {"id": tc.id, "name": tc.name, "arguments": dict(tc.arguments)} for tc in (response.tool_calls or [])
            ]
            assistant_content = getattr(response, "content", None)
            if assistant_content is not None and not isinstance(assistant_content, str):
                # OpenAI 协议要求 content 为 string/null，部分 client 返回结构化内容
                assistant_content = json.dumps(assistant_content, ensure_ascii=False, default=str)
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                # 喂回协议要求 arguments 为 JSON 字符串（中立 ToolCall 已解析为对象）
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            # 自然终止：LLM 不再调用任何工具 = 本轮不说话
            if not tool_calls:
                outcome["silent_reason"] = "natural"
                return outcome

            replied = False
            # 同一步里并列的委派/环节切换先执行、说话最后收尾：reply 一成功本轮即结束，
            # 排在它后面的调用若不先做就会被丢掉（例如说完话却没切到下一个环节）
            for call in _reply_last(tool_calls):
                if not isinstance(call, dict):
                    continue
                name, args = _tool_call_parts(call)
                if not name:
                    continue
                outcome["tool_trace"].append(name)

                if name == "streamer_reply":
                    observation, replied = await self._invoke_reply(
                        args, outcome, batch=batch, thinking=thinking, round_id=round_id
                    )
                else:
                    observation = await self._invoke_registry_tool(
                        name, args, round_id=round_id, source_dialogue=source_dialogue
                    )
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
            self.logger.exception(f"渲染 Planner 系统提示词失败: {e}")
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
        body_narrative: str = "",
        reminders: str = "",
        game_chat: str = "",
    ) -> Optional[str]:
        """构造参考段（一条 user 消息，固定在消息序列尾）。

        内容 = 情境标注（强制/主动）+ 游戏叙事 + 身体侧近况 + 游戏里的聊天 + 运营提醒 +
        组装器元数据段（环节描述 / 直播间快照 / 记忆召回）。对话内容不在此
        处——历史与本批走 canonical 映射的原生消息通道。
        """
        lines: List[str] = []
        if forced:
            lines.append(_forced_situation(batch))
        if proactive:
            # 主动窗不只来自冷场/定时：环节切换、游戏上报、游戏里有人说话也会唤醒，按实际可能来源说明
            lines.append(
                "【情境】本窗为主动发言触发（冷场、定时、环节推进或游戏里有新动静）——弹幕可能为空，"
                '结合房间态势与标"新"的游戏条目决定是否开口、做什么。'
            )
        if game_narrative:
            # 游戏进度保留完整叙述，使施工结果和未解决的问题都能进入本轮决策。
            lines.append(f"【游戏叙事】{game_narrative}")
        if body_narrative:
            # 我在游戏里的遭遇（被打/死亡/重生/紧急反应）：单独一段，不与游戏叙事混排——
            # 两者形状与更新频率不同，混在一起会让高频的遭遇把进展叙事挤掉。
            # 标题用第一人称：游戏里挨打的就是主播自己，不是另一个"身体"
            lines.append(f"【我在游戏里的遭遇】{body_narrative}")
        if game_chat:
            # 游戏里的聊天：游戏世界里的玩家在跟我说话、服务器在通知，不是直播间观众——单独一段，
            # 主播才分得清开口说话对方听不到，要回就得在游戏里打字
            lines.append(f"【游戏里的聊天】{game_chat}")
        work = self._render_delegations(now_ms())
        if work:
            # 我手头在游戏里做的事：最近一次开始做的原话与进展。看得到自己在忙什么，
            # 观众补充或催促时才会补充要求，而不是每条弹幕都换一个新目标
            lines.append(f"【我手头在游戏里做的事】\n{work}")
        if reminders:
            # 运营提醒：后台递话，必达素材——不伪装观众弹幕、不进对话历史，
            # 仅进本轮参考块（消费即送达，由调用方在决策窗入口取空队列）。
            lines.append(f"【运营提醒】{reminders}")
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
        # RoomState.topics 是字符级词频（落库统计口径），单字进 prompt 是噪声，
        # 话题信息由 unread_summary（LLM 摘要）承载
        env_block = EnvironmentBlock(
            minute_bucket_ms=(current_ms // 60000) * 60000,
            duration_so_far_ms=duration_so_far_ms,
            current_stage_label=None,
            unread_summary=getattr(snapshot, "topic_summary", "") or "",
            key_changes=[],
        )

        person_profile_section = await self._collect_person_profiles(batch)

        try:
            assembler_inputs = AssemblerInputs(
                stage_descriptions=rundown_text or "",
                environment=env_block,
                person_profile_section=person_profile_section,
            )
            assembled_text = self._assembler.assemble(assembler_inputs)
        except Exception as e:
            self.logger.exception(f"PlannerAssembler 组装失败: {e}")
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
        history_messages = [canonical.turn_to_message(turn) for turn in canonical.trim_batch_echo(history, batch)]
        return history_messages + batch_messages

    # ==================== 工具列表与执行 ====================

    def _build_tool_list(self) -> List[Dict[str, Any]]:
        """LLM 工具列表 = 注册表按可见名单计算（for_agent="streamer"）。

        动态工具（如 rundown_control）的可见性同样由注册表承载——随
        Provider 注册/摘除进出名单，Planner 不做二次筛选。
        """
        if self._tool_registry is None:
            return []
        try:
            specs = self._tool_registry.list_tools(for_agent="streamer")
        except Exception as e:
            self.logger.warning(f"Planner 拉取工具列表失败（本轮无工具）: {e}")
            return []
        return [_spec_to_fn_def(spec) for spec in specs]

    async def _invoke_reply(
        self,
        args: Dict[str, Any],
        outcome: Dict[str, Any],
        *,
        batch: Optional[List[Any]] = None,
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
            outcome["reply_failures"] += 1
            return json.dumps(
                {"ok": False, "error": "reply 工具不可用（tool_registry 未注入）"}, ensure_ascii=False
            ), False
        set_thinking = getattr(self._reply_provider, "set_thinking_callback", None)
        set_batch = getattr(self._reply_provider, "set_round_batch", None)
        invoke_started_ms = now_ms()
        try:
            if set_thinking is not None:
                set_thinking(thinking.callback_for("replyer", 1) if thinking else None)
            if set_batch is not None:
                # 表达侧要看到观众原话才能接上话；槽位一次性，调用后立即清空
                set_batch(batch)
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name="streamer_reply", arguments=args, source="planner-react", round_id=round_id)
            )
        except Exception as e:
            self.logger.warning(f"reply 工具执行异常: {e}", exc=True)
            outcome["reply_duration_ms"] += now_ms() - invoke_started_ms
            outcome["reply_failures"] += 1
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), False
        finally:
            if set_thinking is not None:
                set_thinking(None)
            if set_batch is not None:
                set_batch(None)
        outcome["reply_duration_ms"] += now_ms() - invoke_started_ms

        if not result.success:
            outcome["reply_failures"] += 1
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

    async def _invoke_registry_tool(
        self,
        name: str,
        args: Dict[str, Any],
        round_id: str = "",
        source_dialogue: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """经 ToolRegistry 执行工具调用，返回观察 JSON 文本。"""
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        # 记下主播自己开始做事时的原话（附来源对话之前），之后每轮都能看到自己在游戏里手上正做着什么
        own_instruction = str(args.get("instruction") or "") if name == "framework_delegate" else ""
        if (
            name == "framework_delegate"
            and source_dialogue
            and isinstance(args.get("instruction"), str)
            and args["instruction"].strip()
        ):
            # 做什么由主播拿主意：游戏侧以本次目标为准，原话只补物品名写法和禁用条件。
            # 实测"别死磕这台机器"配上"以原话为准"的"放块铁板试试"，游戏侧收到两个相反要求；
            # 原话离开直播间上下文（"试试看？""那你……"）也读不懂，所以做法建议只在目标采纳时才做。
            # 抬头写"本次目标"而不是"委派"：游戏里动手的也是主播自己，不该读成别人派来的活。
            args = dict(args)
            args["instruction"] = (
                "[本次目标]\n"
                + args["instruction"]
                + "\n\n[来源对话：逐字引用，只用于核对本次目标里的物品名写法与禁用条件，不构成额外任务]\n"
                + json.dumps(source_dialogue, ensure_ascii=False, default=str)
                + "\n做什么、用什么办法以本次目标为准，两者冲突时听本次目标；本次目标没写到的物品名写法、"
                "禁用条件和确认与权限要求按原话补上；原话里的做法建议和其他请求，只有本次目标采纳了才做。"
                "不把转述额外添加的查证或确认步骤视为用户要求。"
            )
        try:
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name=name, arguments=args, source="planner-react", round_id=round_id)
            )
        except Exception as e:
            self.logger.warning(f"工具 '{name}' 执行异常: {e}", exc=True)
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

        if result.success:
            # 结构化结果优先（reply / rundown 等结构化工具的既有契约）。content
            # 文本一并透传：信息获取型工具（web_search / web_fetch_url 等）的
            # 产出就在 content，只回 {"ok": true} 会让 LLM 看不到任何内容。空
            # content 不带键——动作执行型工具的观察形态维持原样。
            data = dict(result.structured_content) if isinstance(result.structured_content, dict) else {}
            data.setdefault("ok", True)
            # MCP 工具常把同一份结构化结果再序列化成正文返回一遍；完全相同时只给一份，避免上下文翻倍
            if (
                result.content
                and "content" not in data
                and not _same_json_document(result.content, result.structured_content)
            ):
                data["content"] = result.content
            if name == "framework_delegate" and data.get("accepted"):
                # 新委派替换同一执行者手上的旧目标：只保留最近一次，状态从"还在做"重新开始
                agent = str(data.get("executor") or args.get("agent") or "")
                self._delegations[agent] = _Delegation(
                    agent=agent, instruction=own_instruction, task_id=str(data.get("task_id") or ""), at_ms=now_ms()
                )
        else:
            data = {"ok": False, "error": result.error_message or "工具执行失败"}
        return _render_observation(data)

    # ==================== 人物画像注入 ====================

    async def _collect_person_profiles(
        self,
        batch: List[Any],
    ) -> str:
        """按本批发言时间倒序注入全部已有画像，完整保留人物经历与偏好。"""
        if self._memory is None or not batch:
            return ""

        # 候选收集：发言时间倒序去重（最新发言优先；batch 本身按时间正序）
        candidates: List[tuple[str, str]] = []  # (platform, user_id)
        seen: set[tuple[str, str]] = set()
        for msg in reversed(batch):
            platform = _as_id_str(getattr(msg, "platform", None))
            user = getattr(msg, "user", None)
            user_id = _as_id_str(getattr(user, "id", None)) if user is not None else ""
            if not platform or not user_id:
                continue
            key = (platform, user_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)

        if not candidates:
            return ""

        lines: List[str] = []
        for platform, user_id in candidates:
            try:
                profile_text = await self._memory.get_viewer_profile(platform=platform, user_id=user_id)
            except Exception as exc:
                self.logger.warning(f"画像查询失败（{platform}/{user_id}，跳过该人）: {exc}")
                continue
            if not profile_text:
                continue  # 无画像不注入、不占位
            text = profile_text.replace("\n", " ").strip()
            display = await self._viewer_nickname(platform, user_id) or f"{platform}/{user_id}"
            line = f"- {display}: {text}"
            lines.append(line)

        if not lines:
            return ""
        header = "（内部参考，帮助识别老观众；不要逐字复述，与当前对话冲突时以当前对话为准）"
        return "\n".join([header, *lines])

    async def _viewer_nickname(self, platform: str, user_id: str) -> str:
        """从 viewers 统计表实时取观众昵称；仓储未注入或查询失败回退空串。"""
        if self._viewer_repo is None:
            return ""
        try:
            row = await self._viewer_repo.get_viewer_stats(platform=platform, user_id=user_id)
            return str(row["user_name"]) if row is not None else ""
        except Exception as exc:
            self.logger.warning(f"观众昵称查询失败（{platform}/{user_id}）: {exc}")
            return ""
