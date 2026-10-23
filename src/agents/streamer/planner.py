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
2. **人设分工**：Planner 注入 ``$bot_name`` / ``$personality``——知道她是谁，才能替她挑话题、
   在回复指引里定下她对这件事的态度；措辞风格 ``$style_constraints`` 仍只注入 Replyer。
   身份仍是房管：交接稿的事实部分保持客观可核对，态度与语气只写在回复指引里。
3. **高质量模型**：Planner profile 默认 ``planner``（StreamerAgent 装配期硬编码
   传入 `_PROFILE_PLANNER`）——ReAct 决策核心做工具编排与表达意图构思，
   质量敏感（乱调工具/意图偏差的代价高于延迟）。
4. **工具列表**：全局 ToolRegistry 动态拉取 + reply 局部工具 function 定义；
   过滤 provider=="streamer" 的 spec（Agent 内部协议防重入）。
5. **观察作为观察返回**：工具结果以 OpenAI ``tool`` role + ``tool_call_id`` 关联重新写入。
6. **有界循环**：``planner_max_steps``（默认 8）防失控；直播节奏要求快进快出。
7. **降级安全**：LLM 异常 / 超步 / reply 不可用均产出 silent outcome（不抛异常）。

数据流：
    batch + room_state.snapshot + history + forced/proactive + behavior_style + 人设（bot_name/personality）
        ──▶ render('amaidesu_planner_react')（系统提示词）
        ──▶ 消息序列 = [system] + 历史消息（user=观众 / assistant=主播，
            canonical 映射）+ 本批消息（user）+ 参考段（user，固定序列尾）
        ──▶ llm_service.generate(messages, tools=工具列表, profile=PLANNER_PROFILE)
        ──▶ 循环：assistant/tool 消息 append-only 追加在参考段之后
        ──▶ outcome dict（replied / speech / silent_reason / steps / tool_trace）
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler, age_text
from src.agents.streamer.scene_card import GoalLine, GoalStatus
from src.agents.streamer.state_facts import extract_state_facts
from src.agents.streamer.term_sanitizer import sanitize_internal_terms
from src.agents.streamer.timeline import GameEventBatch
from src.modules.context import TailImages
from src.modules.agents.control import PROVIDER_NAME as FRAMEWORK_PROVIDER
from src.modules.llm.payload import Reasoning
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.tools.conversation import extract_image_data_urls, to_conversation_view
from src.modules.tools.models import ToolInvocation
from src.modules.vision.image_store import get_image_store

from .room_state import RoomState
from .thinking_stream import ThinkingStreamContext

__all__ = ["Planner"]


#: Planner 绑定的 LLM profile（代码显式声明，配置不承载绑定，无静默兜底）。
PLANNER_PROFILE: str = "planner"


def _render_observation(data: Any) -> str:
    """工具结果 → 对话观察文本。

    字段与真实值全部保留（完整观察原则），但 base64 二进制载荷（截图等）
    替换为占位符——二进制属 tool.result 事件的 UI 展示通道，整包进对话
    会把单笔请求的图片 token 推到数十万量级。
    """
    return json.dumps(to_conversation_view(data), ensure_ascii=False, default=str)


#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8

#: 画面帧龄告警的最小间隔（毫秒）：断流告警限频，避免长冷场时逐窗刷日志
_STALE_WARN_INTERVAL_MS: int = 5 * 60 * 1000

# 在途委派的账本多久没有刷新就向模型提示"没有新进展"：实测主播约 4 分钟就开始
# 盲发重复指令，提示线压在这个节奏之前（数据源是任务账本的时间戳，渲染时现算）
_DELEG_STALL_HINT_MS: int = 4 * 60 * 1000


@dataclass
class _Delegation:
    """一次已受理的委派：交给谁、主播自己的原话、何时交出，以及身体是否已上报。"""

    agent: str
    instruction: str
    task_id: str
    at_ms: int
    report_kind: str = ""  # delivery=已交付 / escalation=卡住待定夺；空=还在做
    reported_ms: int = 0


@dataclass(frozen=True)
class _Intent:
    """一轮已说出口的打算：何时说的、围绕什么话题、给表达侧的指引、说出的台词。

    台词一并保留：发言闸的台词级重合度检查需要拿"说出口的原话"做比对——
    指引只是给表达侧的方向，最终落在观众耳朵里的是台词。
    """

    at_ms: int
    topic_summary: str
    reply_guidance: str
    speech: str = ""


@dataclass(frozen=True)
class _Relay:
    """一次已送达的补充要求：补给哪个游戏、主播自己的原话、何时补的。"""

    at_ms: int
    agent: str
    content: str


class _PlannerConfig(BaseConfig):
    """Planner 配置 Schema（StreamerConfig 最小子集）。

    仅声明 Planner 直接使用的字段；当从完整 StreamerConfig 加载时，
    BaseConfig.from_dict() 的漂移检测会自动剥离其余字段。

    planner_max_steps 从 StreamerConfig.planner_max_steps 注入；LLM profile
    用途名（"planner"/"replyer"/"summary"）由 StreamerAgent 装配期硬编码，
    不再作为配置字段透传。
    """

    planner_max_steps: int = Field(default=_DEFAULT_MAX_STEPS, title="决策最大步数")
    planner_recent_intents_max: int = Field(default=60, ge=0, title="回看最近几轮打算")
    planner_recent_relays_max: int = Field(default=4, ge=0, title="回看最近几次补充要求")
    reference_tools: List[str] = Field(default_factory=list, title="每轮自动参考的只读工具")
    # 主播多久没开口算冷场：超过后参考段出现【冷场】行，提示词据此把"找话说"设为默认。
    # 装配期取主动发言的冷场阈值（proactive.cold_timeout_ms），0 = 不提示
    cold_silence_ms: int = Field(default=45_000, ge=0, title="冷场提示阈值（毫秒）")


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


# 没有观众、运营或游戏侧新输入的主动触发原因；这些窗口不给游戏活补充要求。
# signoff（收播道别）同样没有任何新输入：场次已结账，不给游戏活递补充要求。
_NO_INPUT_PROACTIVE_REASONS = frozenset({"rundown", "schedule", "cold", "signoff"})


class Planner:
    """主播 Agent 决策核心：ReAct 循环（查→想→说）。

    非线程安全；仅在 StreamerAgent 的单一 asyncio 事件循环内使用。
    通过 ``plan()`` 方法驱动，**不订阅 EventBus**——它是 Agent 内部子组件。
    """

    #: Planner 专用系统提示词模板名（零表达人设注入）
    TEMPLATE_NAME: str = "amaidesu_planner_react"
    #: 委派型游戏守则模板名：有能接收委派的 Agent 时追加进【当前游戏的玩法】
    DELEGATE_RULES_TEMPLATE: str = "amaidesu_planner_delegate_rules"
    #: 没有任何游戏工具面时的玩法段：明说没有游戏，模型不对不存在的游戏工具硬凑动作
    NO_GAME_RULES: str = "当前没有启用任何游戏：没有游戏工具可用，不要安排主播在游戏里动手，让她专心陪观众聊天。"

    def __init__(
        self,
        config: Any,
        llm_service: Any,
        prompt_service: Any,
        room_state: RoomState,
        tool_registry: Any = None,
        profile_repo: Any = None,
        viewer_repo: Any = None,
        context_enabled: bool = True,
        behavior_style: str = "",
        bot_name: str = "麦麦",
        personality: str = "",
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
            profile_repo: 观众画像查询（``ViewerProfileRepo`` 结构契约，只用
                ``get_profile_text``）；None 时无画像注入。
            viewer_repo: ``ViewerRepo``（观众统计仓储）——画像注入时经它实时取
                昵称（昵称会漂移不进画像文本）；None 时注入行回退 platform/user_id。
            context_enabled: 组装器路径开关；False 时以直播流窗口文本为 context_block。
            behavior_style: 人设行为准则（决策侧）。
            bot_name: 主播名字（她是谁）。
            personality: 主播性格描述——替她挑话题、定态度的依据。
            reply_provider: reply 局部工具的 Provider（ReplyToolProvider）；
                start 前由 StreamerAgent 经 ``bind_reply_provider`` 注入也可。
            elapsed_live_provider: 整场开播时长查询（``RundownState.get_elapsed_live_ms``，
                返回 Unix 毫秒或 None=未开播）；None 时快照不含开播时长行。
        """
        if config is None:
            self.typed_config = _PlannerConfig()
        elif hasattr(config, "planner_max_steps"):
            defaults = _PlannerConfig()
            self.typed_config = _PlannerConfig(
                planner_max_steps=getattr(config, "planner_max_steps", _DEFAULT_MAX_STEPS),
                planner_recent_intents_max=getattr(
                    config, "planner_recent_intents_max", defaults.planner_recent_intents_max
                ),
                planner_recent_relays_max=getattr(
                    config, "planner_recent_relays_max", defaults.planner_recent_relays_max
                ),
                reference_tools=list(getattr(config, "reference_tools", None) or []),
                cold_silence_ms=getattr(
                    getattr(config, "proactive", None), "cold_timeout_ms", defaults.cold_silence_ms
                ),
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
        self._cold_silence_ms: int = self.typed_config.cold_silence_ms
        self._tool_registry = tool_registry
        self._profile_repo = profile_repo
        self._viewer_repo = viewer_repo
        self._context_enabled = context_enabled
        self._behavior_style: str = behavior_style or ""
        self._bot_name: str = bot_name or "麦麦"
        self._personality: str = personality or ""
        self._reply_provider = reply_provider
        self._elapsed_live_provider = elapsed_live_provider
        # 委派受理后把"我在哪个游戏里开始做什么"记进时间线（StreamerAgent 绑定）；
        # 参考段只留每个游戏最近一次委派，时间线里留下整场的先后
        self._action_recorder: Optional[Callable[[str, str], None]] = None
        # 直播画面（StreamerAgent 在启用时绑定）：返回最新一帧画面快照或 None。
        # 每个决策窗的第一次请求必挂最新帧（查询式取自暂存 latest 槽位），
        # 同窗后续 ReAct 步不重复挂——frame_tail 每窗只算一次、只拼进第一步请求
        self._frame_provider: Optional[Callable[[], Optional[Any]]] = None
        self._frame_stale_warn_ms = 0
        self._last_stale_warn_ms = 0
        # 每个执行 Agent 最近一次受理的委派：原话、时间、身体是否已上报。实测半小时委派 27 次、
        # 目标来回反转——主播看不到身体手上正做着什么，就会把每条弹幕都改派成新目标
        self._delegations: Dict[str, _Delegation] = {}
        # 委派的机器状态查询（StreamerAgent 构造期绑定，读任务账本）：任务号 →
        # {"status", "updated_at_ms", "snapshot"} 或 None（终态已移除/查不到）
        self._delegation_status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
        # 最近几轮自己说出口的打算（话题 + 指引）。对话历史里只有最终台词，看不出"我已经连着
        # 几轮在讲同一件事"——实测主播连续十分钟句句提掉血，每轮都把它当新消息开口
        self._recent_intents: Deque[_Intent] = deque(maxlen=self.typed_config.planner_recent_intents_max)
        # 最近几次给手上的游戏活补充过的要求（主播自己的原话，不含附带的来源对话）：
        # 看得到自己刚交代过什么，没有新要求时才不会把同样的话再补一遍
        self._recent_relays: Deque[_Relay] = deque(maxlen=self.typed_config.planner_recent_relays_max)
        # 本决策窗的游戏事件素材：本轮新入账批次（决策窗边界批量追加进时间线的那一批，
        # 每轮 plan() 入口重置）。状态跃迁事实块（planner 参考段与表达侧交接各一份）
        # 由 state_facts 提取器从这批行与本轮回执生成
        self._round_game_events: List[str] = []
        self._round_tool_receipts: List[Tuple[str, Any]] = []
        # 本窗是否豁免发言闸的台词重合度检查（收播道别窗：道别话题常与收尾环节发言字面重合）
        self._round_bypass_topic_gate: bool = False
        # 本场备忘录全文查询（StreamerAgent 构造期绑定；空串 = 还没写过，段整段省略）
        self._notebook_text_provider: Optional[Callable[[], str]] = None

        self._assembler = PlannerAssembler()
        # 工具刚返回的截图：观察里只留文字，真图挂在下一步请求的尾部让模型看一次
        self._tail_images = TailImages()

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

    def bind_frame_provider(self, provider: Callable[[], Optional[Any]], *, stale_warn_ms: int) -> None:
        """注入直播画面来源与帧龄告警阈值（StreamerAgent 在 live_frame 启用时绑定）。"""
        self._frame_provider = provider
        self._frame_stale_warn_ms = stale_warn_ms

    def bind_notebook_provider(self, provider: Callable[[], str]) -> None:
        """注入本场备忘录全文查询（StreamerAgent 构造期绑定，同 bind 模式）。"""
        self._notebook_text_provider = provider

    def bind_delegation_status_provider(self, provider: Callable[[str], Optional[Dict[str, Any]]]) -> None:
        """注入委派的机器状态查询（StreamerAgent 构造期绑定，读任务账本，同 bind 模式）。"""
        self._delegation_status_provider = provider

    def _live_frame_message(self) -> Optional[Dict[str, Any]]:
        """本决策窗第一次请求尾部挂最新直播画面（每窗必看，静止画面重复看是设计内行为）。

        画面从暂存的最新帧槽位查询式取得；帧龄超过告警阈值时限频记一条警告
        ——OBS 画面断流（采样一直失败、槽位停在旧帧）由此从静默变可见。
        返回 user 消息（说明 + 图片句柄），不写进消息序列；画面带来的印象
        由模型这一步的思考与决定带下去。
        """
        if self._frame_provider is None or not self._visual_caller():
            return None
        try:
            frame = self._frame_provider()
        except Exception as exc:  # noqa: BLE001 - 取不到画面就不看，决策照常
            self.logger.warning(f"读取直播画面失败，本窗不看画面：{exc}", exc=True)
            return None
        ref = getattr(frame, "ref", "") if frame is not None else ""
        if not ref:
            return None
        current_ms = now_ms()
        age_ms = current_ms - int(getattr(frame, "timestamp_ms", 0) or 0)
        if age_ms > self._frame_stale_warn_ms and current_ms - self._last_stale_warn_ms >= _STALE_WARN_INTERVAL_MS:
            self._last_stale_warn_ms = current_ms
            self.logger.warning(f"直播画面已 {age_text(age_ms)}未更新（超过告警阈值），请检查画面来源是否断流")
        note = (
            f"【直播画面】观众此刻看到的画面（{age_text(age_ms)}截取）。描述观感、氛围时可以参考；"
            "游戏里的数值和进展以游戏记录和查询结果为准，画面里看不清的不要猜。"
        )
        return {"role": "user", "content": [{"type": "text", "text": note}, {"type": "image_ref", "ref": ref}]}

    def bind_action_recorder(self, recorder: Callable[[str, str], None]) -> None:
        """注入委派记录回调（执行者名, 主播自己的原话），同 bind 模式。"""
        self._action_recorder = recorder

    def note_game_report(self, game: str, report_kind: str) -> None:
        """游戏侧上报（交付/卡住待定夺）到达：把对应执行 Agent 手头那次委派标成已上报。

        游戏 Agent 以自己的注册名作为上报的 game 字段，按名字对上即可。
        """
        delegation = self._delegations.get(game)
        if delegation is not None and report_kind in {"delivery", "escalation"}:
            delegation.report_kind = report_kind
            delegation.reported_ms = now_ms()

    def current_goals(self) -> List[GoalLine]:
        """手上的事（嘴的现场卡用）：每个游戏最近一次开始做的事、原话（已脱敏）与进展。"""
        goals: List[GoalLine] = []
        for delegation in self._delegations.values():
            status: GoalStatus = "进行中"
            if delegation.report_kind == "delivery":
                status = "做完了"
            elif delegation.report_kind == "escalation":
                status = "卡住了"
            goals.append(
                GoalLine(
                    game=delegation.agent,
                    instruction=sanitize_internal_terms(delegation.instruction),
                    started_ms=delegation.at_ms,
                    status=status,
                    status_ms=delegation.reported_ms,
                )
            )
        return goals

    def _render_delegations(self, current_ms: int) -> str:
        """我手头在游戏里做的事：每个游戏最近一次开始做的事、距今多久、做完了还是卡住了。

        用第一人称写：对观众来说只有一个主播，游戏里的操作也是"我"在做，
        写成"交给谁""身体在做"会让表达里冒出"活已经派下去了"这种让观众困惑的话。
        """
        rows: List[str] = []
        for delegation in self._delegations.values():
            game = f"在 {delegation.agent} 里" if delegation.agent else "在游戏里"
            # 任务号（deleg_*）是 minecraft 侧的检索地址，对决策无信息量且会被念给观众：
            # 同一执行者手上只保留最近一次委派，注入前转成人类可读描述；原话同样脱敏
            head = f"{age_text(current_ms - delegation.at_ms)}{game}开始做一个任务：{sanitize_internal_terms(delegation.instruction)}"
            rows.append(f"- {head}\n  {self._delegation_state_line(delegation, current_ms)}")
        return "\n".join(rows)

    def _delegation_state_line(self, delegation: _Delegation, current_ms: int) -> str:
        """委派的状态行：机器账（保底事实）与 LLM 交付语合并显示。

        机器状态查询式取自任务账本（绑定 ``bind_delegation_status_provider``；
        None = 已终态或查不到——终态条目按账本约定移除，此时以 LLM 交付语或
        "没有回音"的保底说法为准）。在途时按账本时间戳补"已 X 无进展"与
        游戏侧的停滞标记，主播据此不再把在途的活当成没派出去而重复派。
        """
        record = (
            self._delegation_status_provider(delegation.task_id)
            if self._delegation_status_provider is not None and delegation.task_id
            else None
        )
        if delegation.report_kind == "delivery":
            return f"已在{age_text(current_ms - delegation.reported_ms)}做完，结果见游戏记录。"
        if delegation.report_kind == "escalation":
            return f"在{age_text(current_ms - delegation.reported_ms)}卡住了，正等我和观众一起定夺（见游戏记录）。"
        if record is None:
            if self._delegation_status_provider is None:
                # 未绑定机器账查询（缺 task_ledger 的装配）：维持只有交付语的渲染
                return "还在做，还没做完。"
            return "账面上已了结这次委派，结果见游戏记录。"
        if record.get("status") == "waiting_for_decision":
            return f"停着等我和观众拿主意（{age_text(current_ms - int(record.get('updated_at_ms') or 0))}前记的）。"
        stalled = (record.get("snapshot") or {}).get("stalled_goals") or {}
        if stalled:
            names = "、".join(sorted({str(name) for name in stalled.values()}))
            return f"还在做，但游戏说有的目标停着没动静（{names}）。"
        idle_ms = current_ms - int(record.get("updated_at_ms") or 0)
        if idle_ms >= _DELEG_STALL_HINT_MS:
            return f"还在做，账面已 {age_text(idle_ms)}没有新进展。"
        return "还在做，还没做完。"

    def _render_recent_intents(self, current_ms: int) -> str:
        """我最近几轮想说的：由旧到新只列每轮的话题一句话，作为"已聊话题"清单供重叠判断。

        指引全文不在此重复注入——那轮说出口的原话（指引的最终落点）已在对话历史
        assistant 消息里，同一信息双份会让每轮参考段平白多出几百字。游戏回执到达前
        说出口的条目里，对游戏进展的说法只是当时的猜测（"还没挖进包"），回执才是
        权威事实；已被回执覆盖的条目标注以游戏记录为准，避免模型在两条都标
        "刚刚"的矛盾说法里任选一条播报。
        """
        lines: List[str] = []
        for intent in self._recent_intents:
            line = f"- {age_text(current_ms - intent.at_ms)} 话题：{intent.topic_summary or '（无）'}"
            if self._superseded_by_game_report(intent):
                line += "（其中对游戏进展的说法已被游戏回执覆盖，以游戏记录为准）"
            lines.append(line)
        return "\n".join(lines)

    def _render_silence(self, current_ms: int) -> str:
        """冷场行：她最近一次开口距今超过冷场阈值时，写出已经沉默多久；否则为空。

        从没开过口（开播第一句前）不算冷场——开场白由流程单的开场环节负责；
        阈值为 0 时整行关闭。
        """
        last_speech_ms = self._room_state.last_speech_ms
        if self._cold_silence_ms <= 0 or last_speech_ms is None:
            return ""
        silent_ms = current_ms - last_speech_ms
        if silent_ms < self._cold_silence_ms:
            return ""
        return f"【冷场】她已经 {silent_ms // 1000} 秒没开口（超过 {self._cold_silence_ms // 1000} 秒算冷场）"

    def _superseded_by_game_report(self, intent: _Intent) -> bool:
        """该条目对游戏进展的说法是否已被后续回执覆盖：说出口的时刻落在某次
        委派的"开始 → 已上报"区间内，即属于回执到达前的在途叙述。"""
        return any(
            delegation.report_kind != "" and delegation.at_ms <= intent.at_ms <= delegation.reported_ms
            for delegation in self._delegations.values()
        )

    def _render_recent_relays(self, current_ms: int) -> str:
        """我最近给手上的事补充过的要求：由旧到新，用第一人称写"在哪个游戏里补了什么"。"""
        return "\n".join(
            f"- {age_text(current_ms - relay.at_ms)}{f'在 {relay.agent} 里' if relay.agent else '在游戏里'}"
            f"补充：{relay.content}"
            for relay in self._recent_relays
        )

    # ==================== 主入口 ====================

    async def plan(
        self,
        batch: List[Any],
        *,
        forced: bool = False,
        proactive: bool = False,
        history: Optional[List[Any]] = None,
        rundown_text: Optional[str] = None,
        game_batch: Optional[GameEventBatch] = None,
        reminders: str = "",
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        trigger_reason: str = "",
        session_recap: str = "",
    ) -> Dict[str, Any]:
        """对一个决策窗跑 ReAct 循环，产出 outcome dict。

        Args:
            batch: 本批弹幕列表（可为空——proactive 触发时基于房间状态独立判断）。
            forced: 是否为强制回应批次（SC / 礼物 / 上舰）。
            proactive: 是否为主动发言触发（冷场/定时/环节/游戏动静；batch 可能为空）。
            history: 最近对话历史（可选；反重复用）。游戏事件不在此重放——它们在
                决策窗边界批量追加进了历史的 game 条目，更早的细节查历史与游戏状态工具。
            rundown_text: 当前流程单渲染文本（可选）。
            game_batch: 本轮新入账的游戏事件批次（决策窗边界批量追加进时间线的那一批；
                行文进状态事实块与新鲜语料，聊天原话随说话交给表达侧，私聊只进本窗参考段）。
            reminders: 运营递话留言（【运营提醒】段注入参考块；消费即送达）。
            thinking: 思考流上下文（可选；提供时每次 LLM 调用的 reasoning
                增量经旁路通道外发）。
            round_id: 决策轮次 ID（工具调用经 ToolInvocation.round_id 透传到
                tool.result 事件，供观察器归属；空串表示无轮次关联）。
            trigger_reason: 本窗触发原因码（如 ``proactive:rundown``）；用于判断这一窗
                有没有新的外部输入，没有时不给手上的游戏活补充要求。
            session_recap: 上场回顾文本（开播后固定，整场不变；空串 = 没有上一场或未启用）。

        Returns:
            outcome dict：
            - replied: 是否完成说话（reply 工具成功）
            - speech / emotion / emotion_intensity: reply 产出（StreamerAgent 送发言管线）
            - target / reply_to / topic_summary / reply_guidance: reply 意图
            - silent_reason: 未说话原因（natural=自然终止 / max_steps=超步 / llm_error 等）
            - steps / tool_trace: 循环步数与执行过的工具名序列（可观测）
            - error: 异常原因（无异常为 None）
            - reply_payload: reply 工具完整结果（发言管线消费）
            - reply_duration_ms: reply 工具调用累计耗时（毫秒；未调用为 0）
            - reply_failures: reply 工具失败次数（成功轮为 0）
            - speech_gate_bounces: 发言闸台词重合度检查打回 reply 的次数（本轮内累计）
        """
        self.last_raw_content = ""
        self.last_request_id = None
        self.last_failure = None
        # 上一窗没来得及看的截图属于旧画面，新的决策窗不带
        self._tail_images.drain()
        # 本窗游戏事件素材重置（本轮新入账批次；回执随 ReAct 循环逐步累积，reply 交接时取全量）
        resolved_batch = game_batch or GameEventBatch()
        self._round_game_events = list(resolved_batch.events)
        self._round_tool_receipts = []
        # 游戏聊天原话（非私聊）随本轮说话交给表达侧；私聊行只进本窗参考段
        fresh_game_chat = list(resolved_batch.fresh_chat)
        private_game_chat = list(resolved_batch.private_chat)
        # 收播道别窗整窗豁免台词重合度检查（见 _round_bypass_topic_gate 声明处）
        self._round_bypass_topic_gate = trigger_reason == "proactive:signoff"
        # 发言闸打回计数是本窗口径：Provider 槽位跨窗存活，每窗入口清零
        reset_gate = getattr(self._reply_provider, "reset_speech_gate", None)
        if reset_gate is not None:
            reset_gate()

        outcome: Dict[str, Any] = {
            "replied": False,
            "speech": None,
            "emotion": None,
            "emotion_intensity": 0.5,
            "target": None,
            "reply_to": None,
            "topic_summary": "",
            "reply_guidance": "",
            "silent_reason": None,
            "steps": 0,
            "tool_trace": [],
            "error": None,
            "reply_payload": None,
            "reply_duration_ms": 0,
            "reply_failures": 0,
            "speech_gate_bounces": 0,
        }

        system_prompt = self._render_system_prompt()
        if system_prompt is None:
            outcome["error"] = self.last_failure
            outcome["silent_reason"] = "prompt_render_failed"
            return outcome

        # 决策前自动读一次配置的只读参考工具（如游戏现场速览）：结果直接进参考段，
        # 不必先花一步请求去查再决定说什么。
        overview = await self._read_reference_tools(round_id)
        reference_text = await self._assemble_reference(
            batch,
            history,
            rundown_text,
            forced,
            proactive,
            reminders,
            game_events=self._round_game_events,
            fresh_game_chat=fresh_game_chat,
            private_game_chat=private_game_chat,
            overview=overview,
            session_recap=session_recap,
            trigger_reason=trigger_reason,
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
        # 流程单推进、冷场、定时这类主动窗没有观众、运营或游戏聊天的新输入：此时再给手上的游戏活
        # 补充要求，只会把游戏侧自己汇报过的进展或我的猜测转述回去，变成过时或互相矛盾的指令。
        # 游戏聊天只认本批新到的：更早的聊天已在历史里，拿历史判断会让几分钟前的
        # 一句话一直算作"有新输入"，冷场窗照样往游戏里递话
        relay_without_input = (
            proactive
            and trigger_reason.removeprefix("proactive:") in _NO_INPUT_PROACTIVE_REASONS
            and not (reminders or "").strip()
            and not fresh_game_chat
        )
        if reference_text:
            messages.append({"role": "user", "content": reference_text})
        elif len(messages) == 1:
            # 空窗兜底：对话与参考全空时保底一条 user 消息（部分协议要求非 system 消息存在）
            messages.append({"role": "user", "content": "（本窗无对话与参考内容）"})

        # 直播画面只挂在本窗第一次请求的尾部：后续 ReAct 步骤靠模型第一步的判断继续
        frame_tail = self._live_frame_message()
        steps = 0
        while steps < self.max_steps:
            steps += 1
            outcome["steps"] = steps

            # 上一步工具返回的截图只挂在这一次请求尾部、不写回消息序列：下一步请求时它已消失，
            # 前缀与这一次逐字一致；画面内容由模型这一步的思考与决定带下去
            tail = self._tail_images.drain()
            extra = [item for item in (frame_tail if steps == 1 else None, tail) if item is not None]
            try:
                response = await self._llm_service.generate(
                    [*messages, *extra] if extra else messages,
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
            # 模型开着回传开关时，这一步的思考随本轮历史带回给它，下一步接着这段推理决策；
            # 下一轮从聊天记录重建历史，思考不跨轮保留
            echo = getattr(response, "reasoning_echo", None)
            if isinstance(echo, Reasoning):
                assistant_msg["reasoning"] = echo.model_dump()
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
                        args,
                        outcome,
                        batch=batch,
                        thinking=thinking,
                        round_id=round_id,
                        game_chat=fresh_game_chat,
                    )
                else:
                    observation = await self._invoke_registry_tool(
                        name,
                        args,
                        round_id=round_id,
                        source_dialogue=source_dialogue,
                        relay_without_input=relay_without_input,
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
        """渲染 ReAct 系统提示词（她是谁 + 行为准则 + 当前游戏的玩法 + 工具守则）。"""
        behavior_style_render = self._behavior_style or "（未配置行动准则，请依据直播间态势与对话历史自行决策）"
        # 人设是全程不变的稳定段，放系统提示词里吃前缀缓存；没配性格时如实说明，不编一个
        personality_render = self._personality or "（未配置性格描述，按直播间里她说过的话揣摩）"
        try:
            return self._prompt_service.render(
                self.TEMPLATE_NAME,
                bot_name=self._bot_name,
                personality=personality_render,
                behavior_style=behavior_style_render,
                game_rules=self._render_game_rules(),
            )
        except Exception as e:
            self.logger.exception(f"渲染 Planner 系统提示词失败: {e}")
            self.last_failure = f"prompt_render_failed: {e}"
            return None

    def _render_game_rules(self) -> str:
        """拼装【当前游戏的玩法】：各工具面的使用说明 + 委派型游戏的守则。

        说明来自注册表里对主播可见的各工具面（每个游戏 Agent 自己说这款游戏是
        委派型还是遥控型、该调哪些工具）；框架的委派工具面只在有能接收委派的
        Agent 时才给说明，这时再追加委派型游戏的完整守则（开始做事、补充要求、
        完成口径）。什么说明都没有就明说没有游戏——接哪款游戏只由启用名单决定，
        这里不写死任何游戏名。
        """
        guides: List[tuple[str, str]] = []
        if self._tool_registry is not None:
            try:
                guides = list(self._tool_registry.guides(for_agent="streamer"))
            except Exception as exc:
                self.logger.warning(f"读取工具面使用说明失败（本轮按没有游戏处理）: {exc}", exc=True)
                guides = []
        if not guides:
            return self.NO_GAME_RULES
        lines = [f"- {text}" for _provider, text in guides]
        if any(provider == FRAMEWORK_PROVIDER for provider, _text in guides):
            lines.append(self._prompt_service.render(self.DELEGATE_RULES_TEMPLATE))
        return "\n".join(lines)

    async def _assemble_reference(
        self,
        batch: List[Any],
        history: Optional[List[Any]],
        rundown_text: Optional[str],
        forced: bool,
        proactive: bool,
        reminders: str = "",
        game_events: Optional[List[str]] = None,
        fresh_game_chat: Optional[List[str]] = None,
        private_game_chat: Optional[List[str]] = None,
        overview: str = "",
        session_recap: str = "",
        trigger_reason: str = "",
    ) -> Optional[str]:
        """构造参考段（一条 user 消息，固定在消息序列尾）。

        内容 = 情境标注（强制/主动，按触发原因取条件子句）+ 私密游戏聊天 + 运营提醒 +
        组装器元数据段（上场回顾 / 环节描述 / 直播间快照 / 人物画像）。对话内容与
        游戏事件不在此处——历史（含 game 条目）与本批走 canonical 映射的原生消息通道。
        """
        signoff_window = trigger_reason == "proactive:signoff"
        lines: List[str] = []
        if forced and not signoff_window:
            # signoff 的 forced 只是闸门豁免语义（绕过台词重合度检查保住道别），不是
            # 付费/点名的强制回应情境，不按 _forced_situation 措辞标注
            lines.append(_forced_situation(batch))
        if signoff_window:
            lines.append(
                "【情境】场次已结束（收播道别）：向观众道别收束本场；若收尾环节刚已道别过，"
                "则简短收束即可，不必重复告别。"
            )
        elif proactive:
            # 主动窗不只来自冷场/定时：环节切换、游戏上报、游戏里有人说话也会唤醒，按实际可能来源说明
            lines.append(
                "【情境】本窗为主动发言触发（冷场、定时、环节推进或游戏里有新动静）——弹幕可能为空，"
                "结合房间态势与对话历史里刚入账的游戏条目决定是否开口、做什么；"
                "游戏里没有新动静不等于没话可说。"
            )
        current_ms = now_ms()
        activity = self._room_state.activity_line()
        if activity:
            # 房间活跃度事实一行（观众增量+弹幕量）：互动话术密度与发言策略的校准依据，
            # 空房间连续提问/求押注只会自问自答
            lines.append(f"【房间活跃度】{activity}")
        silence = self._render_silence(current_ms)
        if silence:
            # 冷场行紧跟活跃度：线上回放里她只在游戏出事时开口，游戏一平静就整段不说话，
            # 而模型看不到"已经多久没开口"——把沉默时长摆出来，提示词据此把找话说设为默认
            lines.append(silence)
        if private_game_chat:
            # 私密游戏聊天：只发给我一个人的话，不进对话历史（表达侧看不到），只在本窗参考段出现。
            # 决策据此安排回应，但原话与内容不能被当众念出或转述
            lines.append("【私密游戏聊天】（只发给我，不在直播里念出或转述）\n" + "\n".join(private_game_chat))
        if overview:
            # 本轮决策前自动读取的现场：已经足够时直接据此决定，不再调用同样的查询工具
            lines.append(f"【现场速览（本轮决策前自动读取）】\n{overview}")
        state_facts = extract_state_facts(self._round_game_events)
        if state_facts:
            # 状态跃迁事实块：只收本轮新入账批次里的死亡/重生/任务开始结束/交付类条目，
            # 供客观核对——叙事讲经历，这里只列可核对的状态
            lines.append(f"【当前状态（客观事实）】\n{state_facts}")
        work = self._render_delegations(current_ms)
        if work:
            # 我手头在游戏里做的事：最近一次开始做的原话与进展。看得到自己在忙什么，
            # 观众补充或催促时才会补充要求，而不是每条弹幕都换一个新目标
            lines.append(f"【我手头在游戏里做的事】\n{work}")
        relays = self._render_recent_relays(current_ms)
        if relays:
            # 补充过的要求紧跟在手头的事后面：同一件事已经交代过什么一眼可见，没有新要求就不再补
            lines.append(f"【我最近给手上的事补充过的要求】\n{relays}")
        intents = self._render_recent_intents(current_ms)
        if intents:
            # 已聊话题清单 + 硬规则：复盘发现 46% 的决策窗里模型自己写"别复述"却做不到——
            # 把"重叠必须有新角度"写成显式注入规则，而不是靠模型自觉。新角度既认信息上的新，
            # 也认表达上的新（新态度、新联想、旧事）；线上回放里 30 句有 27 句在复述同一个打算，
            # 所以讲过的结论和打算单独点名不再复述；拿不出新角度先换话题，再考虑沉默
            lines.append(
                "【已聊话题】（由旧到新，只列话题一句话；说出口的原话见对话历史）\n"
                f"{intents}\n"
                "硬规则：本轮想说的话题若与上述清单重叠，必须换一个实质性新角度"
                "（新事实/新进展/新互动对象，或她对这件事的新态度、新联想、相关旧事；"
                "血量、数量、倒计时这类数值变化不算新角度）；清单里讲过的结论和打算不再复述。"
                "拿不出新角度就换个话题，或者本轮沉默。"
            )
        if reminders:
            # 运营提醒：后台递话，必达素材——不伪装观众弹幕、不进对话历史，
            # 仅进本轮参考块（消费即送达，由调用方在决策窗入口取空队列）。
            lines.append(f"【运营提醒】{reminders}")
        situation_text = "\n".join(lines)

        if not self._context_enabled:
            # 裸消息路径：跳过环境快照与记忆召回，仅保留情境标注与私密聊天等本窗素材
            return situation_text

        duration_so_far_ms = 0
        if self._elapsed_live_provider is not None:
            try:
                duration_so_far_ms = int(self._elapsed_live_provider() or 0)
            except Exception as exc:
                self.logger.warning(f"读取开播时长失败（按 0 处理，快照省略该行）: {exc}")
        # RoomState.topics 是字符级词频（落库统计口径），单字进 prompt 是噪声，
        # 话题信息由 unread_summary（LLM 摘要）承载。
        # 未读摘要消费语义：本决策窗取走即清空，后台只在有新弹幕时才生成新摘要，
        # 同一段摘要在多轮请求里反复出现会诱导主播重复播报同一件事；
        # 快照的热度/词频等记账面字段由 background 自行读取，决策面不再整快照
        env_block = EnvironmentBlock(
            minute_bucket_ms=(current_ms // 60000) * 60000,
            duration_so_far_ms=duration_so_far_ms,
            current_stage_label=None,
            unread_summary=self._room_state.consume_topic_summary(),
            key_changes=[],
        )

        person_profile_section = await self._collect_person_profiles(batch)

        notebook_text = ""
        if self._notebook_text_provider is not None:
            try:
                notebook_text = self._notebook_text_provider() or ""
            except Exception as exc:
                # 备忘录读不到只省段，不挡决策
                self.logger.warning(f"读取备忘录失败，本轮参考段省略备忘录段: {exc}", exc=True)

        try:
            assembler_inputs = AssemblerInputs(
                session_recap_section=session_recap or "",
                stage_descriptions=rundown_text or "",
                environment=env_block,
                person_profile_section=person_profile_section,
                notebook_section=notebook_text,
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
        # 历史已由时间线控制在预算内（超预算的旧段换成了前情提要），这里原样映射
        history_messages = [canonical.turn_to_message(turn) for turn in canonical.trim_batch_echo(history, batch)]
        return history_messages + batch_messages

    def _visual_caller(self) -> bool:
        """本档位主模型能否看图（能力协商出口，ADR-037）。

        引擎不可查（测试假件 / 未注入）或查询异常一律按非视觉——判错方向
        是多一次 VLM 转述，而不是把图塞给看不懂的模型。
        """
        getter = getattr(self._llm_service, "is_profile_visual", None)
        if not callable(getter):
            return False
        try:
            return getter(PLANNER_PROFILE) is True
        except Exception as e:
            self.logger.warning(f"视觉能力查询失败，按非视觉处理: {e}", exc=True)
            return False

    async def _read_reference_tools(self, round_id: str) -> str:
        """逐个调用配置的只读参考工具（无参数），按工具名列出结果；失败照实写出原因，不中断决策。"""
        if self._tool_registry is None or not self.typed_config.reference_tools:
            return ""
        parts: List[str] = []
        for name in self.typed_config.reference_tools:
            try:
                result = await self._tool_registry.invoke(
                    ToolInvocation(
                        tool_name=name,
                        arguments={},
                        source="planner-reference",
                        round_id=round_id,
                        caller_profile=PLANNER_PROFILE,
                    )
                )
            except Exception as e:
                self.logger.warning(f"参考工具 '{name}' 调用异常: {e}", exc=True)
                parts.append(f"{name}: 读取失败（{type(e).__name__}: {e}）")
                continue
            if not result.success:
                parts.append(f"{name}: 读取失败（{result.error_message or '未知原因'}）")
                continue
            body = (
                result.structured_content
                if isinstance(result.structured_content, dict) and result.structured_content
                else result.content
            )
            # 参考段与工具观察同走对话安全视图：参考工具返回截图等二进制载荷时以占位符进文本
            text = (
                body
                if isinstance(body, str)
                else json.dumps(to_conversation_view(body), ensure_ascii=False, default=str)
            )
            # 现场速览来自 minecraft 查询工具，回执里常带观测引用等内部标识：进参考段前脱敏
            parts.append(f"{name}: {sanitize_internal_terms(text)}")
        return "\n".join(parts)

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
        # 定义附上下文计量归属（MCP / 内置工具），上下文面板据此分段
        return [self._tool_registry.function_definition(spec) for spec in specs]

    async def _invoke_reply(
        self,
        args: Dict[str, Any],
        outcome: Dict[str, Any],
        *,
        batch: Optional[List[Any]] = None,
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        game_chat: Optional[List[str]] = None,
    ) -> tuple[str, bool]:
        """执行 reply 工具（经 ToolRegistry 统一调用）；成功时把产出写进 outcome 并返回 (观察, replied=True)。

        统一调用路径：reply 与其他工具一样经 ``registry.invoke``（观测/停用/
        熔断复用既有机制，tool.result 事件照发）。thinking 回调槽位机制保留：
        调用前把 replyer 阶段回调设到 Provider 的临时槽位，调用后立即清理
        （一次性语义，防跨轮残留）——槽位挂在 Provider 实例上，不经注册表。
        发言闸（新颖自检 + 台词级重合度检查）在 reply 工具内部执行：打回以
        工具错误形态回到 ReAct 循环；收播道别窗整窗豁免——道别话题与收尾
        环节发言字面重合是常态，闸会系统性误杀道别。
        """
        if self._tool_registry is None:
            outcome["reply_failures"] += 1
            return json.dumps(
                {"ok": False, "error": "reply 工具不可用（tool_registry 未注入）"}, ensure_ascii=False
            ), False
        set_thinking = getattr(self._reply_provider, "set_thinking_callback", None)
        set_batch = getattr(self._reply_provider, "set_round_batch", None)
        set_game_chat = getattr(self._reply_provider, "set_round_game_chat", None)
        set_state_facts = getattr(self._reply_provider, "set_round_state_facts", None)
        set_gate_exempt = getattr(self._reply_provider, "set_speech_gate_exempt", None)
        invoke_started_ms = now_ms()
        try:
            if set_thinking is not None:
                set_thinking(thinking.callback_for("replyer", 1) if thinking else None)
            if set_batch is not None:
                # 表达侧要看到观众原话才能接上话；槽位一次性，调用后立即清空
                set_batch(batch)
            if set_game_chat is not None:
                # 游戏里有人搭话促发的一轮弹幕批是空的，原话同样交给表达侧，调用后清空
                set_game_chat(game_chat)
            if set_state_facts is not None:
                # 状态跃迁事实块：表达侧的客观事实底稿（含本轮观察工具回执要点），调用后清空
                set_state_facts(
                    extract_state_facts(
                        self._round_game_events,
                        self._round_tool_receipts,
                    )
                )
            if set_gate_exempt is not None:
                # 道别窗豁免台词重合度检查：槽位一次性，调用后清空
                set_gate_exempt(self._round_bypass_topic_gate)
            result = await self._tool_registry.invoke(
                ToolInvocation(
                    tool_name="streamer_reply",
                    arguments=args,
                    source="planner-react",
                    round_id=round_id,
                    caller_profile=PLANNER_PROFILE,
                )
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
            if set_game_chat is not None:
                set_game_chat(None)
            if set_state_facts is not None:
                set_state_facts(None)
            if set_gate_exempt is not None:
                set_gate_exempt(False)
        outcome["reply_duration_ms"] += now_ms() - invoke_started_ms

        if not result.success:
            outcome["reply_failures"] += 1
            # 发言闸打回也是工具失败的一种：计数从 Provider 槽位读出供观测
            gate_bounces = getattr(self._reply_provider, "gate_bounces", None)
            if isinstance(gate_bounces, int):
                outcome["speech_gate_bounces"] = gate_bounces
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
        reply_to = args.get("target")
        outcome["reply_to"] = reply_to if isinstance(reply_to, str) and reply_to else None
        # 话说出口了才记下这轮的打算；表达失败或被净化丢弃的不算，观众没听到
        self._recent_intents.append(
            _Intent(
                at_ms=now_ms(),
                topic_summary=outcome["topic_summary"],
                reply_guidance=outcome["reply_guidance"],
                speech=str(payload.get("speech") or ""),
            )
        )

        self.logger.info(f"Planner ReAct 收尾：reply 成功 (target={outcome['target']!r}, steps={outcome['steps']})")
        return json.dumps({"ok": True, "speech_delivered": True}, ensure_ascii=False), True

    async def _invoke_registry_tool(
        self,
        name: str,
        args: Dict[str, Any],
        round_id: str = "",
        source_dialogue: Optional[List[Dict[str, Any]]] = None,
        relay_without_input: bool = False,
    ) -> str:
        """经 ToolRegistry 执行工具调用，返回观察 JSON 文本。

        视觉调用方（本档位主模型声明 visual）且工具结果带图时，图存进
        VisionImageStore、句柄登记到尾部临时图片，下一步请求时挂在序列最后
        让模型看一次；观察文本里图位是占位说明。
        """
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        if name == "framework_prompt" and relay_without_input:
            # 没有新输入的主动窗不补充要求：游戏里的进展以游戏侧回执为准，这一窗只管说话。
            return json.dumps(
                {
                    "ok": False,
                    "error": "这一窗没有新的观众、运营或游戏聊天输入，手上的游戏活不用补充要求；"
                    "游戏进展以游戏侧回执为准，直接说话即可。",
                },
                ensure_ascii=False,
            )
        # 记下主播自己补充要求的原话（附来源对话之前），之后每轮都看得到自己刚交代过什么
        own_relay = str(args.get("content") or "") if name == "framework_prompt" else ""
        if (
            name == "framework_prompt"
            and source_dialogue
            and isinstance(args.get("content"), str)
            and args["content"].strip()
        ):
            # 补充要求同样附上本窗观众原话，游戏侧能看到逐字说法，不只剩一层转述。
            args = dict(args)
            args["content"] = (
                "[补充要求]\n"
                + args["content"]
                + "\n\n[来源对话：逐字引用，只用于核对物品名写法、做法与禁用条件，不构成额外任务]\n"
                + json.dumps(source_dialogue, ensure_ascii=False, default=str)
            )
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
                ToolInvocation(
                    tool_name=name,
                    arguments=args,
                    source="planner-react",
                    round_id=round_id,
                    caller_profile=PLANNER_PROFILE,
                )
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
                if self._action_recorder is not None:
                    try:
                        self._action_recorder(agent, own_instruction)
                    except Exception as exc:  # noqa: BLE001 - 记录失败不影响已受理的委派
                        self.logger.warning(f"委派记录写入时间线失败：{exc}", exc=True)
            if name == "framework_prompt" and data.get("delivered") and own_relay.strip():
                # 补充要求送达了才记；被拒收的没交代到，不能当成"已经说过"
                agent = str(data.get("executor") or args.get("agent") or "")
                self._recent_relays.append(_Relay(at_ms=now_ms(), agent=agent, content=own_relay))
            # 本轮回执进状态素材：reply 交接时的状态跃迁事实块从这里取观察工具的关键字段
            self._round_tool_receipts.append((name, data))
        else:
            data = {"ok": False, "error": result.error_message or "工具执行失败"}
        if self._visual_caller():
            image_data_urls = extract_image_data_urls(result)
            if image_data_urls:
                self._tail_images.add([get_image_store().put(url) for url in image_data_urls], source=name)
        return _render_observation(data)

    # ==================== 人物画像注入 ====================

    async def _collect_person_profiles(
        self,
        batch: List[Any],
    ) -> str:
        """按本批发言时间倒序注入全部已有印象（观众画像），完整保留人物经历与偏好。"""
        if self._profile_repo is None or not batch:
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
                profile_text = await self._profile_repo.get_profile_text(platform=platform, user_id=user_id)
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
