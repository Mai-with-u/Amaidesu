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
        ──▶ RoundOutcome（replied / speech / silent_reason / steps / tool_trace）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer.delegation_workbench import DelegationWorkbench, observation_data
from src.agents.streamer.memory.render import (
    render_archive_number,
    render_commitment_number,
    render_spoken_archive,
    render_spoken_memory,
)
from src.agents.streamer.persona_rules import boundaries_line
from src.agents.streamer.plan import ReplyRoundAttachment, assistant_speech_lines
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler, age_text
from src.agents.streamer.planner_render import (
    Intent,
    assemble_request_messages,
    forced_situation,
    render_delegations,
    render_observation,
    render_recent_intents,
    render_recent_relays,
    render_silence,
    reply_last,
    step_extra_messages,
    tool_call_parts,
)
from src.agents.streamer.game_ledger import GameLedger, sole_chat_player
from src.agents.streamer.term_sanitizer import sanitize_internal_terms
from src.agents.streamer.timeline import GameEventBatch
from src.modules.context import TransientImages, absorb_result_images, is_visual_caller
from src.modules.llm.capabilities import ProfileVisualCapabilityLookup, validate_llm_capability
from src.modules.agents.control import PROVIDER_NAME as FRAMEWORK_PROVIDER
from src.modules.llm.payload import response_to_assistant_message
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.tools.conversation import to_conversation_view
from src.modules.tools.llm_defs import function_definition
from src.modules.tools.models import ToolInvocation

from .room_state import RoomState
from .thinking_stream import ThinkingStreamContext
from .trigger_cause import NO_INPUT_PROACTIVE_KINDS, TriggerCause, TriggerKind

__all__ = ["Planner", "RoundOutcome"]


#: Planner 绑定的 LLM profile（代码显式声明，配置不承载绑定，无静默兜底）。
PLANNER_PROFILE: str = "planner"


#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8

#: 画面帧龄告警的最小间隔（毫秒）：断流告警限频，避免长冷场时逐窗刷日志
_STALE_WARN_INTERVAL_MS: int = 5 * 60 * 1000


class PlannerConfig(BaseConfig):
    """Planner 配置 Schema（构造期由装配方从 StreamerConfig 派生注入）。

    仅声明 Planner 直接使用的字段；装配方把 StreamerConfig 上的对应值
    （含从 proactive.cold_timeout_ms 折算的冷场阈值）抄录成构造参数，
    Planner 不再感知完整 Agent 配置形状。LLM profile 用途名
    （"planner"/"replyer"/"summary"）由 StreamerAgent 装配期硬编码，
    不作为配置字段透传。
    """

    planner_max_steps: int = Field(default=_DEFAULT_MAX_STEPS, title="决策最大步数")
    planner_recent_intents_max: int = Field(default=60, ge=0, title="回看最近几轮打算")
    planner_recent_relays_max: int = Field(default=4, ge=0, title="回看最近几次补充要求")
    reference_tools: List[str] = Field(default_factory=list, title="每轮自动参考的只读工具")
    # 主播多久没开口算冷场：超过后参考段出现【冷场】行，提示词据此把"找话说"设为默认。
    # 装配期取主动发言的冷场阈值（proactive.cold_timeout_ms），0 = 不提示
    cold_silence_ms: int = Field(default=45_000, ge=0, title="冷场提示阈值（毫秒）")


# 点名编号的两类：承诺（约#7）与档案行（档#41）——两个编号空间，同号不同义
MEMORY_REF_COMMITMENT = "commitment"
MEMORY_REF_ARCHIVE = "archive"


def _parse_memory_ref(raw: Any) -> Optional[Tuple[str, int]]:
    """点名编号解析：承诺接受"约#7""#7""7"三种写法，档案行只认"档#41"；其余返回 None。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    kind = MEMORY_REF_COMMITMENT
    if "#" in text:
        head, _sep, tail = text.rpartition("#")
        head = head.strip()
        if head.startswith("档"):
            kind = MEMORY_REF_ARCHIVE
        elif head and not head.startswith("约"):
            return None
        text = tail.strip()
    return (kind, int(text)) if text.isdigit() else None


def _single_memory_ref(raw: Any, *, logger: Any) -> Optional[str]:
    """把 use_memory 收敛成一个编号文本。

    字符串原样返回（空串按没填）；数组只接受一个非空字符串元素——不同服务端
    对 string 参数的回传形态不一致，常见的是一元素数组。多元素、空数组与其他
    类型记日志后按没填处理。
    """
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    if isinstance(raw, list):
        items = [item for item in raw if isinstance(item, str) and item.strip()]
        if len(items) == 1:
            return str(items[0]).strip()
        if items:
            logger.warning(f"use_memory 传了 {len(items)} 个编号，一次只能点名一条，按没填处理：{raw!r}")
        return None
    if raw is not None:
        logger.warning(f"use_memory 的类型认不出（{type(raw).__name__}），按没填处理：{raw!r}")
    return None


def _as_id_str(value: Any) -> str:
    """消息 ID 字段收敛：仅接受 str，其余（None/Mock 等）按空处理。"""
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class RoundOutcome:
    """一个决策窗的完整收场（:meth:`Planner.plan` 产物，Agent 内部值对象）。

    ``replied=True`` 时 speech/emotion/target/reply_payload 等字段承载 reply 工具
    的产出；``replied=False`` 时以 ``silent_reason`` 说明未说话原因
    （natural=自然终止 / max_steps=超步 / llm_error 等），``error`` 携带降级
    细节（无异常为 None）。决策执行器据字段直取组装 result 视图与
    planner.decision 事件——决策事实的键名在此单点定义，不再以 dict 键手抄。
    """

    replied: bool = False
    speech: Optional[str] = None
    emotion: Optional[str] = None
    emotion_intensity: float = 0.5
    target: Optional[str] = None
    reply_to: Optional[str] = None
    topic_summary: str = ""
    reply_guidance: str = ""
    # 话题短名（3–8 字）：已聊话题清单按它聚合，同一话题合并成一行
    topic_tag: str = ""
    silent_reason: Optional[str] = None
    steps: int = 0
    tool_trace: List[str] = field(default_factory=list)
    error: Optional[str] = None
    raw_content: str = ""
    llm_request_id: Optional[str] = None
    reply_payload: Optional[Dict[str, Any]] = None
    reply_duration_ms: int = 0
    reply_failures: int = 0
    speech_gate_bounces: int = 0
    # 本轮点名的旧事编号（写法如 约#7、档#41；随 streamer.speech 落库，供"用上即停"与复盘核对）
    memory_refs: List[str] = field(default_factory=list)
    # 本窗公屏聊天只来自一位核对过的玩家、且这句不是在回一条弹幕时是 (游戏, UUID)：落库时记成在回应他；
    # 否则两个空串
    reply_player: Tuple[str, str] = ("", "")


@dataclass(frozen=True)
class PlannerEnv:
    """Planner 运行环境：构造期一次性注入的迟绑定依赖集合。

    Provider 类字段全部可选，``None`` 即对应能力降级（参考段缺段等降级
    语义等同于逐项注入时该依赖缺席）。``reply_provider_factory`` 持工厂
    而非实例——ReplyToolProvider 依赖 Agent start 期才构造的实例，工厂
    化解生命周期错位，Planner 在首次取用时才解析。
    """

    reply_provider_factory: Optional[Callable[[], Any]] = None
    elapsed_live_provider: Optional[Callable[[], Optional[int]]] = None
    frame_provider: Optional[Callable[[], Optional[Any]]] = None
    frame_stale_warn_ms: int = 0
    # 常驻简报（每窗按台账快照重渲；空串 = 还没有可记的事）
    brief_provider: Optional[Callable[[], str]] = None
    # 常驻简报里出现的承诺编号（点名校验的合法名单之一）
    brief_ids_provider: Optional[Callable[[], Sequence[int]]] = None
    # 常驻简报里标着编号的档案行（点名校验的合法名单之一）
    brief_archive_ids_provider: Optional[Callable[[], Sequence[int]]] = None
    # 按编号取承诺（点名之后渲染说话档用；异步）
    commitment_lookup: Optional[Callable[[int], Any]] = None
    # 按编号取档案行（点名之后渲染说话档用；异步）
    archive_lookup: Optional[Callable[[int], Any]] = None
    # 线索推送：按本批弹幕与当前开着的游戏算这一批想起来的事（异步）
    memory_cues_provider: Optional[Callable[[List[Any], Sequence[str], Sequence[Tuple[str, str]]], Any]] = None
    # 已启用的游戏工具面（提示行只在这些游戏里取承诺卡；不表示主播正在玩哪一个）
    open_games_provider: Optional[Callable[[], Sequence[str]]] = None
    action_recorder: Optional[Callable[[str, str], None]] = None
    delegation_status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None


def _observation_error(observation: str) -> Optional[str]:
    """工具观察文本是一次失败（``{"ok": false, ...}``）时返回拒绝理由，否则 None。

    读不懂的按没失败算，不因格式问题压住说话。
    """
    try:
        data = json.loads(observation)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("ok") is not False:
        return None
    return str(data.get("error") or "（回执没写理由）")


def _held_reply_words(args: Dict[str, Any]) -> Dict[str, str]:
    """被压住的那句话的原意（话题与指引），原样退回给 Planner 改。"""
    return {key: str(args.get(key) or "") for key in ("topic_summary", "reply_guidance")}


def _held_reply_error(arrangement: str) -> str:
    """同一步给游戏的安排没送到时，压住的那句话退回给 Planner 的理由。"""
    return (
        f"同一步里给游戏的安排（{arrangement}）没送到，这句先不说：嘴上宣布了、游戏里却没动，观众会看到对不上。"
        "被压住的原话在 held_reply，拒绝理由在 refusal。按理由改好安排再说；不改安排的话，"
        "换一句不提这个决定的说法再调用 streamer_reply。这一窗只压这一次，下一句照常说出去。"
    )


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
        config: Optional[PlannerConfig],
        llm_service: Any,
        prompt_service: Any,
        room_state: RoomState,
        tool_registry: Any = None,
        viewer_repo: Any = None,
        context_enabled: bool = True,
        behavior_style: str = "",
        bot_name: str = "麦麦",
        personality: str = "",
        boundaries: str = "",
        inside_jokes: str = "",
        env: Optional[PlannerEnv] = None,
    ) -> None:
        """初始化 Planner。

        Args:
            config: Planner 配置（``PlannerConfig``；None 即全默认）。
            llm_service: LLM 管理器，需提供
                ``async generate(messages, *, tools=..., profile=...) -> payload.Response``
                与 ``is_profile_visual(profile) -> bool``。
            prompt_service: 提示词管理器，需提供 ``render(name, **vars) -> str``。
            room_state: 直播间态势规则层实例。
            tool_registry: 全局 ToolRegistry——ReAct 工具列表来源（信息收集/动作类工具）。
            viewer_repo: ``ViewerRepo``（观众仓储）——当前决策链不直接读它
                （观众画像注入已随旧记忆退役），保留注入点供调用方按需使用；None 合法。
            context_enabled: 组装器路径开关；False 时以直播流窗口文本为 context_block。
            behavior_style: 人设行为准则（决策侧）。
            bot_name: 主播名字（她是谁）。
            personality: 主播性格描述——替她挑话题、定态度的依据。
            boundaries: 边界栏——她不答应的事（人设字段，运营者可改；空串 = 不带）。
            inside_jokes: 老梗栏——运营者手写的直播间老梗（空串 = 不带）。
            env: 运行环境（迟绑定依赖集合，见 :class:`PlannerEnv`）；缺省
                ``PlannerEnv()`` 即全部可选依赖降级缺席。
        """
        # 配置为 typed 子配置对象单轨注入；缺省即全默认（"未配置降级"由字段默认值表达）
        self.typed_config = config if config is not None else PlannerConfig()

        # LLM profile 绑定为代码显式常量（PLANNER_PROFILE），不读配置、无兜底；
        # 本字段保留供内部工具列表与日志输出使用。
        self.profile: str = PLANNER_PROFILE
        self.max_steps: int = self.typed_config.planner_max_steps

        self._llm_service = llm_service
        # llm_service 另需 async generate（决策推理用）；视觉查询契约在装配期校验，
        # 注入了缺方法的对象时启动即报错，而不是运行期静默按非视觉处理
        validate_llm_capability(llm_service, ProfileVisualCapabilityLookup, parameter="llm_service")
        self._prompt_service = prompt_service
        self._room_state = room_state
        self._cold_silence_ms: int = self.typed_config.cold_silence_ms
        self._tool_registry = tool_registry
        self._viewer_repo = viewer_repo
        self._context_enabled = context_enabled
        self._behavior_style: str = behavior_style or ""
        self._bot_name: str = bot_name or "麦麦"
        self._personality: str = personality or ""
        # 边界栏原文：替她挑话题、写交接稿时同样不答应线下见面、改称呼这类事（渲染见 _boundaries_rule）
        self._boundaries: str = boundaries or ""
        # 老梗栏：只进 Planner 系统提示词，观众提起时才接（渲染见 _inside_jokes_block）
        self._inside_jokes: str = inside_jokes or ""
        self._reply_provider: Any = None
        self._reply_provider_factory: Optional[Callable[[], Any]] = env.reply_provider_factory if env else None
        self._elapsed_live_provider = env.elapsed_live_provider if env else None
        # 直播画面：返回最新一帧画面快照或 None（经环境注入，live_frame 关闭时缺席）。
        # 每个决策窗的第一次请求必挂最新帧（查询式取自暂存 latest 槽位），
        # 同窗后续 ReAct 步不重复挂——frame_tail 每窗只算一次、只拼进第一步请求
        self._frame_provider = env.frame_provider if env else None
        self._frame_stale_warn_ms = env.frame_stale_warn_ms if env else 0
        self._last_stale_warn_ms = 0
        # "手头的事"工作台：委派受理、递话回执、最近打算的状态与拦截规则都在这里
        # （Agent 内部件，不进工具注册表；本窗工具回执也在此记账）
        self.workbench = DelegationWorkbench(
            tool_registry,
            intents_max=self.typed_config.planner_recent_intents_max,
            relays_max=self.typed_config.planner_recent_relays_max,
            action_recorder=env.action_recorder if env else None,
            delegation_status_provider=env.delegation_status_provider if env else None,
        )
        # 本决策窗的游戏事件素材：本轮新入账批次（决策窗边界批量追加进时间线的那一批，
        # 每轮 plan() 入口重置）。状态跃迁事实块（planner 参考段与表达侧交接各一份）
        # 随批次由账本产出；回执随本窗 ReAct 循环累积，reply 交接时合成全量
        self._round_facts_text = ""
        self._round_game_events: List[str] = []
        # 本窗公屏聊天只来自一位核对过的玩家时是 (游戏, UUID)：这一窗说的话落库时记成在回应他
        self._round_reply_player: Tuple[str, str] = ("", "")
        # 本窗有没有弹幕：她这句点了回应对象、本窗又有弹幕时，这句在回那条弹幕，不记成在回应玩家
        self._round_has_danmaku = False
        # 本窗在游戏里开口或冲她做动作的玩家：线索推送据此推玩家卡
        self._round_game_players: Tuple[Tuple[str, str], ...] = ()
        # 本窗是否豁免发言闸的台词重合度检查（收播道别窗：道别话题常与收尾环节发言字面重合）
        self._round_bypass_topic_gate: bool = False
        # 本场备忘录全文查询（经环境注入；空串 = 还没写过，段整段省略）
        self._brief_provider = env.brief_provider if env else None
        self._brief_ids_provider = env.brief_ids_provider if env else None
        self._commitment_lookup = env.commitment_lookup if env else None
        self._brief_archive_ids_provider = env.brief_archive_ids_provider if env else None
        self._archive_lookup = env.archive_lookup if env else None
        self._memory_cues_provider = env.memory_cues_provider if env else None
        self._open_games_provider = env.open_games_provider if env else None

        # 本窗合法的点名编号与卡片称呼（每窗在 plan() 入口重算，这里给默认值）
        self._round_memory_ids: Set[int] = set()
        # 本窗能点名的档案行：常驻简报里标着编号的加本批卡片里的
        self._round_archive_ids: Set[int] = set()
        self._memory_labels: Dict[int, str] = {}

        self._assembler = PlannerAssembler()
        # 工具刚返回的截图：观察里只留文字，真图挂在下一步请求的尾部让模型看一次
        self._transient_images = TransientImages()

        self.logger = get_logger("Planner")

    @property
    def reply_provider(self) -> Any:
        """reply 局部工具 Provider（经工厂迟解析并缓存；工厂未配置为 None）。

        ReplyToolProvider 依赖 Agent start 期才构造的实例，PlannerEnv 持工厂
        化解生命周期错位；本属性只作引用/诊断，reply 调用本体走 ToolRegistry。
        """
        if self._reply_provider is None and self._reply_provider_factory is not None:
            self._reply_provider = self._reply_provider_factory()
        return self._reply_provider

    def _live_frame_message(self) -> Optional[Dict[str, Any]]:
        """本决策窗第一次请求尾部挂最新直播画面（每窗必看，静止画面重复看是设计内行为）。

        画面从暂存的最新帧槽位查询式取得；帧龄超过告警阈值时限频记一条警告
        ——OBS 画面断流（采样一直失败、槽位停在旧帧）由此从静默变可见。
        返回 user 消息（说明 + 图片句柄），不写进消息序列；画面带来的印象
        由模型这一步的思考与决定带下去。
        """
        if self._frame_provider is None or not is_visual_caller(self._llm_service, PLANNER_PROFILE):
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
        # 画面里的聊天栏不当证据：实测截图上"我"正在输入框里打的问候（开头是对方的名字）被读成
        # 对方说了"这是我的"，凭空多出一句"认领"，一路递进游戏里成了质问对方"先认领又否认"
        note = (
            f"【直播画面】观众此刻看到的画面（{age_text(age_ms)}截取）。描述观感、氛围时可以参考；"
            "游戏里的数值和进展以游戏记录和查询结果为准，画面里看不清的不要猜。"
            "画面里的建筑与方块可能是本来就有的；画面不能告诉你东西是谁放的、什么时候放的——"
            "不能因为画面里有墙，就当成'你刚安排的活干出来了'；刚安排的事做没做成，只认游戏记录和查询结果。"
            "画面里聊天栏的字不算谁说过的话：截图小、字容易认错，输入框里还可能是我自己正在打的字；"
            "谁说了什么以对话记录里的游戏聊天条目为准。"
        )
        return {"role": "user", "content": [{"type": "text", "text": note}, {"type": "image_ref", "ref": ref}]}

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
        resume_replay: str = "",
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        trigger_cause: Optional[TriggerCause] = None,
    ) -> RoundOutcome:
        """对一个决策窗跑 ReAct 循环，产出本轮收场（RoundOutcome）。

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
            resume_replay: 续播重放的玩家游戏发言（【重启前可能还没回的话】段注入
                参考块；读取即清空，注入一次制）。
            thinking: 思考流上下文（可选；提供时每次 LLM 调用的 reasoning
                增量经旁路通道外发）。
            round_id: 决策轮次 ID（工具调用经 ToolInvocation.round_id 透传到
                tool.result 事件，供观察器归属；空串表示无轮次关联）。
            trigger_cause: 本窗触发原因（类型化值对象）；用于判断这一窗
                有没有新的外部输入，没有时不给手上的游戏活补充要求。
                缺省（None）按未知批次触发处理——不做任何豁免。


        Returns:
            RoundOutcome：replied / speech / emotion / target / reply_to /
            topic_summary / reply_guidance / silent_reason / steps / tool_trace /
            error / raw_content / llm_request_id / reply_payload /
            reply_duration_ms / reply_failures / speech_gate_bounces——字段语义见类型注释。
        """
        # 上一窗没来得及看的截图属于旧画面，新的决策窗不带
        self._transient_images.drain()
        # 本窗游戏事件素材重置（本轮新入账批次；回执随 ReAct 循环逐步累积，reply 交接时取全量）
        resolved_batch = game_batch or GameEventBatch()
        self._round_facts_text = resolved_batch.facts_text
        self._round_game_events = list(resolved_batch.events)
        self._round_reply_player = sole_chat_player(resolved_batch.chat_speakers)
        self._round_has_danmaku = bool(batch)
        self._round_game_players = resolved_batch.addressed_players
        self.workbench.begin_round()
        # 游戏聊天原话（非私聊）随本轮说话交给表达侧；私聊行只进本窗参考段
        fresh_game_chat = list(resolved_batch.fresh_chat)
        private_game_chat = list(resolved_batch.private_chat)
        # 收播道别窗与断线续播窗整窗豁免台词重合度检查（见 _round_bypass_topic_gate 声明处）
        self._round_bypass_topic_gate = trigger_cause is not None and trigger_cause.kind in (
            TriggerKind.SIGNOFF,
            TriggerKind.RESUME,
        )
        # 发言闸打回计数是本窗口径：由 Provider 检测 round_id 变化清零（每窗
        # 首次 reply 调用前生效，时序与本入口等价）

        outcome = RoundOutcome()

        system_prompt, prompt_failure = self._render_system_prompt()
        if system_prompt is None:
            return replace(outcome, error=prompt_failure, silent_reason="prompt_render_failed")
        # 常驻简报每窗按台账快照重渲：接在系统提示词末尾，表没变时逐字节相同，
        # 仍和人设一起吃前缀缓存（参考段在不断增长的对话历史之后，放那里每个决策窗都要重新计费）
        brief_text = self._brief_text()
        if brief_text:
            system_prompt = f"{system_prompt}\n\n{brief_text}"
        # 线索推送每批评一次：卡片与提示行进参考段，编号进本窗合法点名名单
        memory_cues = await self._collect_memory_cues(batch)
        self._round_memory_ids = self._round_commitment_ids(memory_cues)
        self._round_archive_ids = self._round_archive_line_ids(memory_cues)
        self._memory_labels = dict(getattr(memory_cues, "labels", {}) or {})

        # 决策前自动读一次配置的只读参考工具（如游戏现场速览）：结果直接进参考段，
        # 不必先花一步请求去查再决定说什么。
        overview = await self._read_reference_tools(round_id)
        reference_text, reference_failure = await self._assemble_reference(
            batch,
            history,
            rundown_text,
            forced,
            proactive,
            reminders,
            resume_replay=resume_replay,
            game_events=self._round_game_events,
            fresh_game_chat=fresh_game_chat,
            private_game_chat=private_game_chat,
            overview=overview,
            trigger_cause=trigger_cause,
            memory_cues=memory_cues,
        )
        if reference_text is None:
            return replace(outcome, error=reference_failure, silent_reason="assembler_failed")

        tool_list = self._build_tool_list()
        # 消息序列：[system] + 历史（user/assistant 原生消息）+ 本批（user）+ 参考段（user，序列尾）；
        # ReAct 循环的 assistant/tool 消息 append-only 追加在参考段之后，绝不插中间。
        messages = assemble_request_messages(
            system_prompt, self._build_dialogue_messages(batch, history), reference_text
        )
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
            and trigger_cause is not None
            and trigger_cause.kind in NO_INPUT_PROACTIVE_KINDS
            and not (reminders or "").strip()
            and not fresh_game_chat
        )
        # 发言闸语料：本窗历史里的已说台词，窗起点算一次随单携带（reply 工具
        # 不再为查重单独读历史——读是纯读，无决策窗出账副作用）
        speech_history = assistant_speech_lines(history)

        # 直播画面只挂在本窗第一次请求的尾部：后续 ReAct 步骤靠模型第一步的判断继续
        frame_tail = self._live_frame_message()
        # 本窗压住过的那句话（给游戏的安排被拒时）：每窗最多压一次，免得一窗里接连被拒、整窗哑掉；
        # 压过之后这一窗没再开口，静默原因记成 reply_held，复盘看得出是被压住的
        held_once = False
        steps = 0
        while steps < self.max_steps:
            steps += 1
            outcome = replace(outcome, steps=steps)

            # 上一步工具返回的截图只挂在这一次请求尾部、不写回消息序列：下一步请求时它已消失，
            # 前缀与这一次逐字一致；画面内容由模型这一步的思考与决定带下去
            tail = self._transient_images.drain()
            extra = step_extra_messages(frame_tail, tail, steps)
            try:
                response = await self._llm_service.generate(
                    [*messages, *extra] if extra else messages,
                    tools=tool_list,
                    profile=PLANNER_PROFILE,
                    on_delta=thinking.callback_for("planner", steps) if thinking else None,
                )
            except Exception as e:
                self.logger.warning(f"Planner LLM 调用异常: {e}")
                return replace(outcome, error=f"llm_error: {e}", silent_reason="llm_error")

            if not response.ok:
                error = response.error or "unknown"
                return replace(outcome, error=f"llm_failed: {error}", silent_reason="llm_failed")

            outcome = replace(
                outcome,
                raw_content=response.content or "",
                llm_request_id=response.request_id or None,
            )

            # engine 返回的中立 ToolCall（扁平对象）收敛为内部扁平 dict，供本模块消费
            tool_calls = [
                {"id": tc.id, "name": tc.name, "arguments": dict(tc.arguments)} for tc in (response.tool_calls or [])
            ]
            # 响应转回 assistant 消息（含线协议转换与思考回传）由 LLM 模块统一承载；
            # 下一轮从聊天记录重建历史，思考不跨轮保留
            messages.append(response_to_assistant_message(response))

            # 自然终止：LLM 不再调用任何工具 = 本轮不说话
            if not tool_calls:
                return replace(outcome, silent_reason="reply_held" if held_once else "natural")

            replied = False
            # 同一步里给游戏的安排（开始做事 / 补充要求）没送到时，记下是哪个调用和它的拒绝回执：这一步的话先不说
            arrangement_failed = ""
            arrangement_refusal = ""
            # 同一步里并列的委派/环节切换先执行、说话最后收尾：reply 一成功本轮即结束，
            # 排在它后面的调用若不先做就会被丢掉（例如说完话却没切到下一个环节）
            for call in reply_last(tool_calls):
                if not isinstance(call, dict):
                    continue
                name, args = tool_call_parts(call)
                if not name:
                    continue
                outcome = replace(outcome, tool_trace=[*outcome.tool_trace, name])

                if name == "streamer_reply" and arrangement_failed and not held_once:
                    # 安排被拒 -> 这句不出口 -> 原话连同拒绝理由退回给我 -> 改好安排或换个说法，下一步再说。
                    # 实测委派被拒后照样说出"那我自己招一只……来"，观众听到了决定，游戏那边什么也没收到
                    held_once = True
                    held = _held_reply_words(args)
                    self.logger.info(f"同一步给游戏的安排（{arrangement_failed}）被拒，压住这句：{held}")
                    observation = json.dumps(
                        {
                            "ok": False,
                            "error": _held_reply_error(arrangement_failed),
                            "held_reply": held,
                            "refusal": arrangement_refusal,
                        },
                        ensure_ascii=False,
                    )
                elif name == "streamer_reply":
                    observation, outcome, replied = await self._invoke_reply(
                        args,
                        outcome,
                        batch=batch,
                        thinking=thinking,
                        round_id=round_id,
                        game_chat=fresh_game_chat,
                        speech_history=speech_history,
                    )
                else:
                    observation = await self._invoke_registry_tool(
                        name,
                        args,
                        round_id=round_id,
                        source_dialogue=source_dialogue,
                        relay_without_input=relay_without_input,
                    )
                    refusal = _observation_error(observation) if self.workbench.handles(name) else None
                    if refusal is not None:
                        arrangement_failed, arrangement_refusal = name, refusal
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "") or ""),
                        "content": observation,
                    }
                )
                if replied:
                    return outcome

        # 超步：未完成说话即收场（压住过一句又没再开口时记成 reply_held）
        return replace(outcome, silent_reason="reply_held" if held_once else "max_steps")

    async def _resolve_use_memory(self, args: Dict[str, Any]) -> Tuple[str, List[str]]:
        """解析 reply 的 use_memory：编号在本窗名单内才认，渲染成说话档交表达侧。

        返回（说话档文本, 编号写法列表）。编号不在本窗常驻简报或本批卡片里、取不到那一条、
        或没注入对应查询时一律按没填处理并记日志——点名不打回，最多这句没用上旧事。
        """
        raw = args.get("use_memory")
        ref_text = _single_memory_ref(raw, logger=self.logger)
        if ref_text is None:
            return "", []
        parsed = _parse_memory_ref(ref_text)
        if parsed is None:
            self.logger.warning(f"use_memory 的编号认不出（{ref_text!r}）：只认 约#N / #N / N 与 档#N")
            return "", []
        kind, number = parsed
        if kind == MEMORY_REF_ARCHIVE:
            return await self._resolve_archive_ref(number)
        commitment_id = number
        if commitment_id not in self._round_memory_ids:
            self.logger.info(f"use_memory 的编号 {commitment_id} 不在本窗简报或卡片里，忽略")
            return "", []
        if self._commitment_lookup is None:
            self.logger.debug("未注入承诺查询，use_memory 忽略")
            return "", []
        try:
            commitment = await self._commitment_lookup(commitment_id)
        except Exception as exc:  # noqa: BLE001 - 取不到就当没填，台词照常生成
            self.logger.warning(f"取承诺 {commitment_id} 失败，本轮不用旧事: {exc}", exc=True)
            return "", []
        if commitment is None:
            self.logger.info(f"承诺 {commitment_id} 不在了，use_memory 忽略")
            return "", []
        label = self._memory_labels.get(commitment_id) or "大家"
        spoken = render_spoken_memory(target_label=label, commitment=commitment)
        return spoken, [render_commitment_number(commitment_id)]

    async def _resolve_archive_ref(self, line_id: int) -> Tuple[str, List[str]]:
        """点名一行档案：在本窗名单里、取得到这一行，台词拿到它的标题（不带编号与原话）。"""
        if line_id not in self._round_archive_ids:
            self.logger.info(f"use_memory 的档案行编号 {line_id} 不在本窗简报或卡片里，忽略")
            return "", []
        if self._archive_lookup is None:
            self.logger.debug("未注入档案行查询，use_memory 忽略")
            return "", []
        try:
            line = await self._archive_lookup(line_id)
        except Exception as exc:  # noqa: BLE001 - 取不到就当没填，台词照常生成
            self.logger.warning(f"取档案行 {line_id} 失败，本轮不用旧事: {exc}", exc=True)
            return "", []
        if line is None:
            self.logger.info(f"档案行 {line_id} 不在了，use_memory 忽略")
            return "", []
        spoken = render_spoken_archive(line)
        return (spoken, [render_archive_number(line_id)]) if spoken else ("", [])

    # ==================== 提示词与上下文 ====================

    def _render_system_prompt(self) -> Tuple[Optional[str], Optional[str]]:
        """渲染 ReAct 系统提示词，返回（提示词文本, 失败原因）。

        成功时失败原因为 None；失败时提示词为 None，失败原因随 outcome.error
        写进决策事件（本窗按 prompt_render_failed 静默收场）。
        """
        behavior_style_render = self._behavior_style or "（未配置行动准则，请依据直播间态势与对话历史自行决策）"
        # 人设是全程不变的稳定段，放系统提示词里吃前缀缓存；没配性格时如实说明，不编一个
        personality_render = self._personality or "（未配置性格描述，按直播间里她说过的话揣摩）"
        try:
            return (
                self._prompt_service.render(
                    self.TEMPLATE_NAME,
                    bot_name=self._bot_name,
                    personality=personality_render,
                    behavior_style=behavior_style_render,
                    boundaries_rule=self._boundaries_rule(),
                    inside_jokes=self._inside_jokes_block(),
                    game_rules=self._render_game_rules(),
                ),
                None,
            )
        except Exception as e:
            self.logger.exception(f"渲染 Planner 系统提示词失败: {e}")
            return None, f"prompt_render_failed: {e}"

    def _inside_jokes_block(self) -> str:
        """老梗栏渲染：运营者手写的老梗，抬头写明只在观众提起时才接；空串时整段省略。

        只进 Planner——Replyer 与快嘴若各自玩梗会反复出现同一段子；Planner 用交接稿
        把态度交过去就够。内容来自配置、整场不随表变化，与人设一起吃前缀缓存。
        """
        text = self._inside_jokes.strip()
        if not text:
            return ""
        return "## 直播间老梗（运营者写的；观众提起时才接，别自己翻出来，冷场时也别拿来凑话）\n" + text

    def _boundaries_rule(self) -> str:
        """「她是谁」一节末尾、AI 声明之后的边界栏一行（带前导空行）；人设里没写时为空串。

        观众刷屏哄她许诺（"下次线下见""以后都叫你老公"）时，房管写交接稿也得替她守住：
        人设里列了她不答应的事，就在 AI 声明旁边多一行"不答应的事：…"，并提醒交接稿里
        别替她答应、碰到就让她婉拒。措辞与多行折成一行的规则归 persona_rules，三条说话
        通路同源；没写（空串或只有空白）时整行省略，模板里不留空行。AI 声明写死在模板里，
        不受这里影响。
        """
        line = boundaries_line(self._boundaries)
        return f"\n\n{line}（交接稿里别替她答应这些事，碰到就让她婉拒）" if line else ""

    def _brief_text(self) -> str:
        """系统提示词末尾的常驻简报（每决策窗按台账快照重渲；表没变时逐字节相同），没有可记的事时为空串。"""
        if self._brief_provider is None:
            return ""
        try:
            return (self._brief_provider() or "").strip()
        except Exception as exc:
            # 简报读不到只省这一段，不挡决策
            self.logger.warning(f"读取常驻简报失败，本场系统提示词省略简报: {exc}", exc=True)
            return ""

    async def _collect_memory_cues(self, batch: List[Any]) -> Any:
        """算本批的线索（承诺卡、老观众在场一行、回执行）；没注入或出错都返回 None。"""
        if self._memory_cues_provider is None:
            return None
        open_games: Sequence[str] = ()
        if self._open_games_provider is not None:
            try:
                open_games = tuple(self._open_games_provider() or ())
            except Exception as exc:
                self.logger.warning(f"读当前开着的游戏失败（本批不出游戏承诺卡）: {exc}", exc=True)
        try:
            return await self._memory_cues_provider(list(batch), open_games, self._round_game_players)
        except Exception as exc:
            self.logger.warning(f"线索推送失败（本批不带提示行，不挡决策）: {exc}", exc=True)
            return None

    def _round_commitment_ids(self, cues: Any) -> Set[int]:
        """本窗合法的点名编号：常驻简报里的加本批卡片里的。"""
        ids: Set[int] = set()
        if self._brief_ids_provider is not None:
            try:
                ids.update(int(item) for item in self._brief_ids_provider() or ())
            except Exception as exc:
                self.logger.warning(f"读常驻简报的承诺编号失败（本窗只能点名本批卡片）: {exc}", exc=True)
        card_ids = getattr(cues, "commitment_ids", ()) if cues is not None else ()
        ids.update(int(item) for item in card_ids)
        return ids

    def _round_archive_line_ids(self, cues: Any) -> Set[int]:
        """本窗合法的档案行点名编号：常驻简报里标着编号的加本批卡片里的。"""
        ids: Set[int] = set()
        if self._brief_archive_ids_provider is not None:
            try:
                ids.update(int(item) for item in self._brief_archive_ids_provider() or ())
            except Exception as exc:
                self.logger.warning(f"读常驻简报的档案行编号失败（本窗只能点名本批卡片）: {exc}", exc=True)
        card_ids = getattr(cues, "archive_ids", ()) if cues is not None else ()
        ids.update(int(item) for item in card_ids)
        return ids

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
        resume_replay: str = "",
        game_events: Optional[List[str]] = None,
        fresh_game_chat: Optional[List[str]] = None,
        private_game_chat: Optional[List[str]] = None,
        overview: str = "",
        trigger_cause: Optional[TriggerCause] = None,
        memory_cues: Any = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """构造参考段，返回（参考段文本, 失败原因）。

        内容 = 情境标注（强制/主动，按触发原因取条件子句）+ 私密游戏聊天 + 运营提醒 +
        组装器元数据段（环节描述 / 直播间快照）+ 本批线索块。对话内容与游戏事件
        不在此处——历史（含 game 条目）与本批走 canonical 映射的原生消息通道；
        常驻简报与老梗栏在系统提示词里，不占参考段。
        组装失败时文本为 None，失败原因随 outcome.error 写进决策事件。
        """
        signoff_window = trigger_cause is not None and trigger_cause.kind is TriggerKind.SIGNOFF
        resume_window = trigger_cause is not None and trigger_cause.kind is TriggerKind.RESUME
        lines: List[str] = []
        if forced and not (signoff_window or resume_window):
            # signoff / resume 的 forced 只是闸门豁免语义（绕过台词重合度检查），不是
            # 付费/点名的强制回应情境，不按 forced_situation 措辞标注
            lines.append(forced_situation(batch))
        if signoff_window:
            lines.append(
                "【情境】场次已结束（收播道别）：向观众道别收束本场；若收尾环节刚已道别过，"
                "则简短收束即可，不必重复告别。"
            )
        elif resume_window:
            lines.append(
                "【情境】直播刚才因程序中断过，现在已恢复（断线续播）：用一句话自然接回直播"
                "——告诉观众信号回来了、接着刚才的内容继续；不要重新自我介绍或重新开场，"
                "也不必对中断过度道歉，按对话历史里刚才聊到的地方顺下去。"
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
        silence = render_silence(self._room_state.last_speech_ms, self._cold_silence_ms, current_ms)
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
        state_facts = self._round_facts_text
        if state_facts:
            # 状态跃迁事实块：只收本轮新入账批次里的死亡/重生/任务开始结束/交付类条目，
            # 供客观核对——叙事讲经历，这里只列可核对的状态
            lines.append(f"【当前状态（客观事实）】\n{state_facts}")
        work = render_delegations(self.workbench.delegations, self.workbench.delegation_status_provider, current_ms)
        if work:
            # 我手头在游戏里做的事：最近一次开始做的原话与进展。看得到自己在忙什么，
            # 观众补充或催促时才会补充要求，而不是每条弹幕都换一个新目标
            lines.append(f"【我手头在游戏里做的事】\n{work}")
        relays = render_recent_relays(self.workbench.recent_relays, current_ms)
        if relays:
            # 补充过的要求紧跟在手头的事后面：同一件事已经交代过什么一眼可见，没有新要求就不再补
            lines.append(f"【我最近给手上的事补充过的要求】\n{relays}")
        intents = render_recent_intents(self.workbench.recent_intents, self.workbench.delegations, current_ms)
        if intents:
            # 已聊话题清单 + 硬规则：复盘发现 46% 的决策窗里模型自己写"别复述"却做不到——
            # 把"重叠必须有新角度"写成显式注入规则，而不是靠模型自觉。新角度既认信息上的新，
            # 也认表达上的新（新态度、新联想、旧事）；线上回放里 30 句有 27 句在复述同一个打算，
            # 所以讲过的结论和打算单独点名不再复述；拿不出新角度先换话题，再考虑沉默
            lines.append(
                "【已聊话题】（由旧到新，同一话题合并成一行：话题名（轮数，时间跨度）：最新状态；"
                "说出口的原话见对话历史）\n"
                f"{intents}\n"
                "硬规则：本轮想说的话题若与上述清单重叠，必须换一个实质性新角度"
                "（新事实/新进展/新互动对象，或她对这件事的新态度、新联想、相关旧事；"
                "血量、数量、倒计时这类数值变化不算新角度）；清单里讲过的结论和打算不再复述。"
                "拿不出新角度就换个话题，或者本轮沉默。"
            )
        if resume_replay:
            # 续播时补回的玩家聊天：上一进程最后一轮播出台词的决策之后才到的那几句，随重启没接上；
            # 找不到那一轮时是重启前几分钟整段，里面可能有她回过的，所以标题写明回过的别再回（读取即清空，注入一次制）
            lines.append(
                "【重启前可能还没回的话】（程序重启前最后几分钟里游戏里玩家说的话，可能还没回，"
                "也可能已经回过；对话历史里回过的别再回）\n" + resume_replay
            )
        if reminders:
            # 运营提醒：后台递话，必达素材——不伪装观众弹幕、不进对话历史，
            # 仅进本轮参考块（消费即送达，由调用方在决策窗入口取空队列）。
            lines.append(f"【运营提醒】{reminders}")
        situation_text = "\n".join(lines)

        if not self._context_enabled:
            # 裸消息路径：跳过环境快照与记忆召回，仅保留情境标注与私密聊天等本窗素材
            return situation_text, None

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
            unread_summary=self._room_state.consume_topic_summary(),
        )

        try:
            assembler_inputs = AssemblerInputs(
                stage_descriptions=rundown_text or "",
                environment=env_block,
            )
            assembled_text = self._assembler.assemble(assembler_inputs)
        except Exception as e:
            self.logger.exception(f"PlannerAssembler 组装失败: {e}")
            return None, f"assembler_failed: {e}"

        cue_text = (getattr(memory_cues, "text", "") or "").strip() if memory_cues is not None else ""
        parts = [block for block in (situation_text, assembled_text, cue_text) if block]
        return "\n\n".join(parts), None

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
        # 定义附上下文计量归属（MCP / 内置工具），上下文面板据此分段；
        # 定义形状由工具域的 LLM 转换层统一生成（llm_defs）
        return [function_definition(spec, self._tool_registry.category_of(spec.full_name)) for spec in specs]

    async def _invoke_reply(
        self,
        args: Dict[str, Any],
        outcome: RoundOutcome,
        *,
        batch: Optional[List[Any]] = None,
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        game_chat: Optional[List[str]] = None,
        speech_history: Optional[List[str]] = None,
    ) -> tuple[str, RoundOutcome, bool]:
        """执行 reply 工具（经 ToolRegistry 统一调用）；成功时把产出并进 outcome 并返回 (观察, 更新后 outcome, replied=True)。

        统一调用路径：reply 与其他工具一样经 ``registry.invoke``（观测/停用/
        熔断复用既有机制，tool.result 事件照发）。本窗交接材料（thinking 回调/
        弹幕批/游戏聊天/状态事实块/闸豁免/闸语料）打包进 ``ReplyRoundAttachment``、
        随 ``ToolInvocation.attachment`` 传入——材料生命周期 = 调用生命周期，
        调用即弃，无跨轮残留。
        发言闸（新颖自检 + 台词级重合度检查）在 reply 工具内部执行：打回以
        工具错误形态回到 ReAct 循环；收播道别窗整窗豁免——道别话题与收尾
        环节发言字面重合是常态，闸会系统性误杀道别。
        """
        if self._tool_registry is None:
            return (
                json.dumps({"ok": False, "error": "reply 工具不可用（tool_registry 未注入）"}, ensure_ascii=False),
                replace(outcome, reply_failures=outcome.reply_failures + 1),
                False,
            )
        invoke_started_ms = now_ms()
        try:
            remembered, memory_ids = await self._resolve_use_memory(args)
            attachment = ReplyRoundAttachment(
                # 思考流回调只在调用单上活一次，不经 LLM、模型不可伪造
                thinking_callback=thinking.callback_for("replyer", 1) if thinking else None,
                # 点名的那条旧事（说话档写法）：表达侧只有它，没有编号与原话
                remembered=remembered,
                # 表达侧要看到观众原话才能接上话
                round_batch=list(batch or []),
                # 游戏里有人搭话促发的一轮弹幕批是空的，原话同样交给表达侧
                game_chat=list(game_chat or []),
                # 状态跃迁事实块：表达侧的客观事实底稿（含本轮观察工具回执要点）
                state_facts=GameLedger.compose_facts(self._round_facts_text, self.workbench.round_receipts),
                # 道别窗豁免台词重合度检查
                gate_exempt=self._round_bypass_topic_gate,
                # 闸语料：本窗起点算好的已说台词，重试共用（None = 本窗无历史可查）
                speech_history=speech_history,
            )
            result = await self._tool_registry.invoke(
                ToolInvocation(
                    tool_name="streamer_reply",
                    arguments=args,
                    source="planner-react",
                    round_id=round_id,
                    caller_profile=PLANNER_PROFILE,
                    attachment=attachment,
                )
            )
        except Exception as e:
            self.logger.warning(f"reply 工具执行异常: {e}", exc=True)
            return (
                json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False),
                replace(
                    outcome,
                    reply_duration_ms=outcome.reply_duration_ms + now_ms() - invoke_started_ms,
                    reply_failures=outcome.reply_failures + 1,
                ),
                False,
            )
        outcome = replace(outcome, reply_duration_ms=outcome.reply_duration_ms + now_ms() - invoke_started_ms)

        if not result.success:
            updates: Dict[str, Any] = {"reply_failures": outcome.reply_failures + 1}
            # 发言闸打回也是工具失败的一种：计数随失败结果的 structured_content 回传供观测
            bounce_payload = result.structured_content
            if isinstance(bounce_payload, dict) and isinstance(bounce_payload.get("speech_gate_bounces"), int):
                updates["speech_gate_bounces"] = bounce_payload["speech_gate_bounces"]
            return (
                json.dumps({"ok": False, "error": result.error_message or "reply 失败"}, ensure_ascii=False),
                replace(outcome, **updates),
                False,
            )

        payload = result.structured_content if isinstance(result.structured_content, dict) else {}
        emotion = payload.get("emotion") if isinstance(payload.get("emotion"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        reply_to = args.get("target")
        # 这句点了要回的弹幕（本窗确有弹幕）：对象是那位观众。即使本窗只有一位玩家在公屏开口，也不记成在
        # 回应他——否则答应观众的事会被整理记到玩家头上
        answers_danmaku = isinstance(reply_to, str) and bool(reply_to) and self._round_has_danmaku
        outcome = replace(
            outcome,
            replied=True,
            speech=payload.get("speech"),
            emotion=emotion.get("name"),
            emotion_intensity=emotion.get("intensity", 0.5),
            reply_payload=payload,
            target=metadata.get("target") or args.get("target"),
            topic_summary=str(args.get("topic_summary", "") or ""),
            reply_guidance=str(args.get("reply_guidance", "") or ""),
            topic_tag=str(args.get("topic_tag", "") or ""),
            reply_to=reply_to if isinstance(reply_to, str) and reply_to else None,
            memory_refs=memory_ids,
            reply_player=("", "") if answers_danmaku else self._round_reply_player,
        )
        # 话说出口了才记下这轮的打算；表达失败或被净化丢弃的不算，观众没听到
        self.workbench.note_intent(
            Intent(
                at_ms=now_ms(),
                topic_summary=outcome.topic_summary,
                reply_guidance=outcome.reply_guidance,
                topic_tag=outcome.topic_tag,
                speech=str(payload.get("speech") or ""),
            )
        )

        self.logger.info(f"Planner ReAct 收尾：reply 成功 (target={outcome.target!r}, steps={outcome.steps})")
        return json.dumps({"ok": True, "speech_delivered": True}, ensure_ascii=False), outcome, True

    async def _invoke_registry_tool(
        self,
        name: str,
        args: Dict[str, Any],
        round_id: str = "",
        source_dialogue: Optional[List[Dict[str, Any]]] = None,
        relay_without_input: bool = False,
    ) -> str:
        """经 ToolRegistry 执行工具调用，返回观察 JSON 文本。

        委派型游戏的两个框架工具名（framework_delegate / framework_prompt）整体
        委托给"手头的事"工作台受理——状态迁移规则在其拦截规则表里，不在本循环。
        其余工具走本方法：视觉调用方且工具结果带图时，图经 absorb_result_images
        入库换句柄、登记到易逝图，下一步请求时挂在序列最后让模型看一次；
        观察文本里图位是占位说明。
        """
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        if self.workbench.handles(name):
            return await self.workbench.handle(
                name,
                args,
                round_id=round_id,
                caller_profile=PLANNER_PROFILE,
                source_dialogue=source_dialogue,
                relay_without_input=relay_without_input,
                on_result=partial(absorb_result_images, self._transient_images, self._llm_service, PLANNER_PROFILE),
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
        absorb_result_images(self._transient_images, self._llm_service, PLANNER_PROFILE, name, result)
        if result.success:
            data = observation_data(result)
            # 本轮回执进状态素材：reply 交接时的状态跃迁事实块从这里取观察工具的关键字段
            self.workbench.note_receipt(name, data)
        else:
            data = {"ok": False, "error": result.error_message or "工具执行失败"}
        return render_observation(data)
