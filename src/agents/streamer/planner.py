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
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer import paid_viewers
from src.agents.streamer.delegation_workbench import DelegationWorkbench, observation_data
from src.agents.streamer.memory.render import (
    render_archive_number,
    render_commitment_number,
    render_spoken_archive,
    render_spoken_memory,
)
from src.agents.streamer.mouth_reconcile import ReconcileVerdict
from src.agents.streamer.persona_rules import boundaries_line
from src.agents.streamer.plan import ReplyRoundAttachment, assistant_speech_lines
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler, age_text
from src.agents.streamer.planner_render import (
    Intent,
    assemble_request_messages,
    forced_situation,
    render_delegation_backlog,
    render_delegations,
    render_game_speakers,
    render_observation,
    render_recent_intents,
    render_recent_relays,
    render_silence,
    render_social_facts,
    reply_last,
    step_extra_messages,
    tool_call_parts,
)
from src.agents.streamer.game_now import GameNow, actual_doing_map, render_game_now
from src.modules.agents.delegation_board import Board
from src.agents.streamer.game_ledger import (
    GameLedger,
    answering_player,
    keep_recent_notes,
    mark_answered,
    sole_chat_player,
    sole_speaker_name,
)
from src.agents.streamer.term_sanitizer import sanitize_internal_terms
from src.agents.streamer.timeline import GameEventBatch, PlayerNote
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


#: 社交事实工具的全名：注册了才在提示词里点名它（作废条款与主动窗行动选项）
SOCIAL_FACT_TOOL_NAME: str = "streamer_social_fact"
#: 场内记忆更正工具的全名：注册了才在主动窗行动选项里点名它
MEMORY_CORRECT_TOOL_NAME: str = "streamer_memory_correct"

#: 作废条款里"记一条取代旧行的社交事实"那一步（模板变量 social_fact_step；工具没注册时为空串）
_SOCIAL_FACT_RETRACTION_STEP: str = "再用 `streamer_social_fact` 把作废记成一条新事实，取代旧的那一行。"

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
    # 本窗因点名本场已提过的旧事被退回的次数（每窗至多一次）：复盘看得出这一窗为什么换了话题或没开口
    memory_repeat_bounces: int = 0
    # 本轮点名的旧事编号（写法如 约#7、档#41；随 streamer.speech 落库，供"用上即停"与复盘核对）
    memory_refs: List[str] = field(default_factory=list)
    # 这句在回应游戏里的哪位玩家，(游戏, UUID)：落库时记成在回应他，口径见 Planner._reply_player_identity；
    # 记不下时两个空串
    reply_player: Tuple[str, str] = ("", "")
    # 出声前最后一次与嘴核对的时刻（Unix 毫秒，0 表示未核对）：执行器在等播放槽位后只补查这一刻之后的新嘴话
    mouth_checked_ms: int = 0
    # 杂谈档话头推进：模型在 streamer_reply 声明本轮已把当前提纲要点/话题卡聊透收尾
    beat_done: bool = False
    # 杂谈档选用哪张话题卡开话头（整数编号；未选用为 None）
    use_card_id: Optional[int] = None


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
    # 本场已点名提过的档案行：没在回观众弹幕时再点名，这句先退回
    used_archive_ids_provider: Optional[Callable[[], Sequence[int]]] = None
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
    delegation_boards_provider: Optional[Callable[[], Mapping[str, Board]]] = None
    # 游戏那边此刻的读数（此刻那一步、游戏那边的判断、她近几分钟的公屏话），按手上的事所在的 Agent 查
    game_now_provider: Optional[Callable[[Sequence[str]], GameNow]] = None
    # 本批付过费的观众身份键（异步；查付费三表）。None = 不标（查不到付费记录的能力缺席）
    paid_identities_provider: Optional[Callable[[List[Any]], Awaitable[Set[str]]]] = None
    # 现行游戏社交事实（异步，每批从库现读）。None = 能力缺席，参考段缺【游戏社交事实】
    social_facts_provider: Optional[Callable[[], Awaitable[List[Any]]]] = None
    # 开播【上次收尾】（异步，开播头两窗或第一件委派受理前返回确定性交接块；其余窗返回 None）
    opening_wrapup_provider: Optional[Callable[[], Awaitable[Optional[Any]]]] = None
    # 本场最近已说台词（含嘴与决策，旧→新）：补齐尚未落库或无库路径下的嘴台词，让表达侧立即可见
    recent_speeches_provider: Optional[Callable[[], Sequence[str]]] = None
    # 出声前与嘴对账（候选台词, 起始时刻 -> 裁决）：本轮开窗后嘴说过话或命中已欢迎名单时做核对
    mouth_reconciler: Optional[Callable[[str, int], Awaitable[ReconcileVerdict]]] = None
    # 对账判定矛盾作废、需要退回 Planner 重想一步时，拉取本窗开窗后的最新现场快照（嘴说的话、新游戏事件、一句话现场）
    fresh_context_since: Optional[Callable[[int], Awaitable[Dict[str, Any]]]] = None
    # 可注入时钟（缺省为真实毫秒时钟）
    clock: Callable[[], int] = now_ms
    # 本轮说话档位查询（"play" 边玩边说 / "talk" 杂谈唠嗑；缺省 "play"）
    speech_mode_provider: Optional[Callable[[], str]] = None
    # 话头工作台本窗渲染（入参 has_viewer_batch；返回【当前话头与本节提纲】或空串）
    talk_workbench_provider: Optional[Callable[[bool], str]] = None
    # 自备话题匣本窗刷新并渲染（入参 has_viewer_batch；异步返回【可抽的话题卡 / 提问箱】或空串）
    talk_cards_provider: Optional[Callable[[bool], Awaitable[str]]] = None
    # 上一轮主动决策窗是否以沉默收场（play 档游戏平静时解开 45 秒冷场死锁）
    proactive_silent_provider: Optional[Callable[[], bool]] = None


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


def _answers_danmaku(target: Any, danmaku: Sequence[Tuple[str, str]]) -> bool:
    """这句的回应目标对不对得上本窗一条弹幕：编号相等，或那条弹幕原文等于、包含这段目标。

    编号按 Planner 照抄的几种写法归一（``[id:m1]`` / ``id:m1`` / ``m1`` 都算 m1）：提示词让它
    照着 ``[id:…]`` 抄，只比裸编号会把回弹幕的这句误记成在回应玩家。
    对不上的不算在回弹幕：她拿公屏上玩家的原话当回应目标时，这句在回玩家，不能因为本窗恰好也有
    弹幕就丢掉回应玩家，否则对他的承诺就平白落空。对不上时也不兜底到最后一条弹幕。
    """
    if not isinstance(target, str) or not target:
        return False
    message = canonical.normalize_message_id(target)
    return any(message == message_id or (text and target in text) for message_id, text in danmaku)


def _repeated_memory_error(number: str) -> str:
    """本场已提过的旧事被主动点名时退回给 Planner 的理由。"""
    return (
        f"{number} 这件事本场已经提过（简报里标着（本场已提过）），这句先不说：同一件旧事一场提一次就够，"
        "再提观众听着就是车轱辘话。换个话题再调用 streamer_reply，或者这一轮不说。"
        "观众或玩家问起这件事时照常点名：回观众时 target 指向那条弹幕，回玩家时 to_player 写他。"
        "这一窗只退这一次：再点名这一条会照常说出去。"
    )


def _mouth_contradicted_error(reason: str) -> str:
    """构思期间现场已变且嘴已先回应、原台词与最新情况矛盾时退回给 Planner 重想一步的理由。"""
    return (
        f"刚才构思这句台词期间游戏现场已变化且嘴已经先回应（{reason}），这句原话已过时未播出。"
        "请结合返回字段里的最新现场决定：还有新的话要补充就按新情况重新调用 streamer_reply，"
        "没有要补充的就直接结束本轮不说话。"
    )


def _viewer_message_in(target: Any, history: Sequence[Any]) -> bool:
    """回应目标是不是本场更早的一条观众消息（上一窗问的、这一窗才回）：编号对得上历史里观众发的那一行。

    游戏条目没有消息编号；只认观众发的（role 为 user），她自己的台词不算。
    """
    message_id = canonical.normalize_message_id(target) if isinstance(target, str) else ""
    if not message_id:
        return False
    for turn in history:
        role = getattr(getattr(turn, "role", ""), "value", getattr(turn, "role", ""))
        if role == "user" and str(getattr(turn, "message_id", "") or "") == message_id:
            return True
    return False


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
    #: 没有任何游戏工具面时的玩法段：明说舞台杂谈语境，模型不对不存在的游戏工具硬凑动作
    NO_GAME_RULES: str = (
        "当前没有启用任何游戏（舞台杂谈模式）：她在镜头前用虚拟形象和观众面对面聊天，没有游戏工具可用，"
        "不要安排她在游戏里动手，下文涉及游戏内行动、现场速览、背包与建筑的规则本场不适用；"
        "让她专心陪观众聊天，可配合情绪与偶尔的 avatar_act 肢体动作。"
    )

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
        persona_profile: str = "",
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
        # 结构化人物设定档案（已渲染文本）：整场静态，拼在「她是谁」里吃前缀缓存
        self._persona_profile: str = (persona_profile or "").strip()
        # 本决策窗说话档位（"play" / "talk"），每窗在 plan() 入口按环节与游戏态势刷新
        self._round_speech_mode: str = "play"
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
            delegation_boards_provider=env.delegation_boards_provider if env else None,
        )
        # 本决策窗的游戏事件素材：本轮新入账批次（决策窗边界批量追加进时间线的那一批，
        # 每轮 plan() 入口重置）。状态跃迁事实块（planner 参考段与表达侧交接各一份）
        # 随批次由账本产出；回执随本窗 ReAct 循环累积，reply 交接时合成全量
        self._round_facts_text = ""
        self._round_game_events: List[str] = []
        # 本窗公屏聊天只来自一位核对过的玩家时是 (游戏, UUID)：这一窗说的话落库时记成在回应他
        self._round_reply_player: Tuple[str, str] = ("", "")
        # 本窗的弹幕（编号, 原文）：她这句的回应目标对得上其中一条时，这句在回那条弹幕，不记成在回应玩家
        self._round_danmaku: Tuple[Tuple[str, str], ...] = ()
        # 本窗付过费的观众身份键集合：这些观众的弹幕行加「（付费观众）」标注，
        # 她据此权衡"谁的要求优先满足"（事实基础，见 paid_viewers 模块）
        self._round_paid_identities: Set[str] = set()
        # 本窗交来的历史：回应目标是更早一窗的观众弹幕时，在这里查得到
        self._round_history: List[Any] = []
        # 本批的现行游戏社交事实（每批从库现读，参考段【游戏社交事实】渲染用）
        self._round_social_facts: List[Any] = []
        # 本窗由观众弹幕或玩家开口推上来的档案行（线索卡来源里有人提到）
        self._round_asked_archive_ids: Set[int] = set()
        # 本窗是否已因点名提过的旧事退回过一次：每窗只退一次，第二次照常说（点名留着，照样记成提过）
        self._round_memory_bounced: bool = False
        # 本窗开窗时刻（毫秒）与是否已因嘴带来的新事实推翻退回过一次（每窗最多退一次重想）
        self._round_started_ms: int = 0
        self._round_mouth_bounced: bool = False
        # 本窗在游戏里开口或冲她做动作的玩家：线索推送据此推玩家卡
        self._round_game_players: Tuple[Tuple[str, str], ...] = ()
        # 玩家找她的条目：本窗新到的（定回应对象）与最近几分钟的（交给表达侧，说清谁说的、谁做的）
        self._round_player_notes: Tuple[PlayerNote, ...] = ()
        self._recent_player_notes: Tuple[PlayerNote, ...] = ()
        # 本场备忘录全文查询（经环境注入；空串 = 还没写过，段整段省略）
        self._brief_provider = env.brief_provider if env else None
        self._social_facts_provider = env.social_facts_provider if env else None
        self._opening_wrapup_provider = env.opening_wrapup_provider if env else None
        self._recent_speeches_provider = env.recent_speeches_provider if env else None
        self._mouth_reconciler = env.mouth_reconciler if env else None
        self._fresh_context_since = env.fresh_context_since if env else None
        self._clock: Callable[[], int] = env.clock if env else now_ms
        self._brief_ids_provider = env.brief_ids_provider if env else None
        self._commitment_lookup = env.commitment_lookup if env else None
        self._brief_archive_ids_provider = env.brief_archive_ids_provider if env else None
        self._used_archive_ids_provider = env.used_archive_ids_provider if env else None
        self._archive_lookup = env.archive_lookup if env else None
        self._memory_cues_provider = env.memory_cues_provider if env else None
        self._open_games_provider = env.open_games_provider if env else None
        self._game_now_provider = env.game_now_provider if env else None
        self._paid_identities_provider = env.paid_identities_provider if env else None
        self._speech_mode_provider = env.speech_mode_provider if env else None
        self._talk_workbench_provider = env.talk_workbench_provider if env else None
        self._talk_cards_provider = env.talk_cards_provider if env else None
        self._proactive_silent_provider = env.proactive_silent_provider if env else None

        # 本窗合法的点名编号与卡片称呼（每窗在 plan() 入口重算，这里给默认值）
        self._round_memory_ids: Set[int] = set()
        # 本窗能点名的档案行：常驻简报里标着编号的加本批卡片里的
        self._round_archive_ids: Set[int] = set()
        self._memory_labels: Dict[int, str] = {}
        # 最近一轮决策前读取到的现场速览文本，供场内更正工具逐字核对游戏现场回执
        self._last_overview_text: str = ""

        self._assembler = PlannerAssembler()
        # 工具刚返回的截图：观察里只留文字，真图挂在下一步请求的尾部让模型看一次
        self._transient_images = TransientImages()

        self.logger = get_logger("Planner")

    @property
    def recent_player_notes(self) -> Tuple[PlayerNote, ...]:
        """最近几分钟游戏里玩家找她的条目（旧→新，只读）：交给表达侧的就是这一份。"""
        return self._recent_player_notes

    def reset_player_notes(self) -> None:
        """场次复位：清空玩家找她的条目，开播与下播各调一次。

        条目按发生时刻留几分钟，同一进程里下播后紧接着开下一场时，上一场末尾的条目还没过期，
        不清的话新一场开头她会接上一场的玩家的话、谢上一场送的礼。
        """
        self._round_player_notes = ()
        self._recent_player_notes = ()

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
            reply_duration_ms / reply_failures——字段语义见类型注释。
        """
        # 上一窗没来得及看的截图属于旧画面，新的决策窗不带
        self._transient_images.drain()
        # 本窗游戏事件素材重置（本轮新入账批次；回执随 ReAct 循环逐步累积，reply 交接时取全量）
        resolved_batch = game_batch or GameEventBatch()
        self._round_facts_text = resolved_batch.facts_text
        self._round_game_events = list(resolved_batch.events)
        self._round_reply_player = sole_chat_player(resolved_batch.chat_speakers)
        self._round_danmaku = tuple(
            (str(getattr(msg, "message_id", "") or ""), str(getattr(msg, "content", "") or "")) for msg in batch
        )
        self._round_history = list(history or [])
        # 本批付过费的观众：查一次（三表只读查询），标注挂在给决策侧的消息行上
        self._round_paid_identities = await self._collect_paid_identities(batch)
        # 本批的现行社交事实从库现读（场内登记下一轮可见；读失败只少一段参考，不挡决策）
        self._round_social_facts = await self._load_social_facts()
        self._round_memory_bounced = False
        self._round_started_ms = self._clock()
        self._round_mouth_bounced = False
        self._round_game_players = resolved_batch.addressed_players
        # 玩家扔来东西常在上一窗入账、这一窗才接话：表达侧拿的是最近几分钟的，不只本窗
        self._round_player_notes = resolved_batch.player_notes
        self._recent_player_notes = keep_recent_notes(self._recent_player_notes, resolved_batch.player_notes, now_ms())
        self.workbench.begin_round()
        # 游戏聊天原话（非私聊）随本轮说话交给表达侧；私聊行只进本窗参考段
        fresh_game_chat = list(resolved_batch.fresh_chat)
        private_game_chat = list(resolved_batch.private_chat)

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
        opening_wrapup = await self._load_opening_wrapup()
        opening_wrapup_text = (getattr(opening_wrapup, "text", "") or "").strip() if opening_wrapup is not None else ""
        opening_wrapup_ids = getattr(opening_wrapup, "commitment_ids", ()) if opening_wrapup is not None else ()
        self._round_memory_ids = self._round_commitment_ids(memory_cues) | {int(item) for item in opening_wrapup_ids}
        self._round_archive_ids = self._round_archive_line_ids(memory_cues)
        self._round_asked_archive_ids = {
            int(item) for item in (getattr(memory_cues, "asked_archive_ids", ()) if memory_cues is not None else ())
        }
        self._memory_labels = dict(getattr(memory_cues, "labels", {}) or {})

        # 决策前自动读一次配置的只读参考工具（如游戏现场速览）：结果直接进参考段，
        # 不必先花一步请求去查再决定说什么。
        self._round_speech_mode = self._resolve_speech_mode()
        talk_workbench_text = self._read_talk_workbench(bool(batch))
        talk_cards_text = await self._read_talk_cards(bool(batch))
        overview = await self._read_reference_tools(round_id)
        self._last_overview_text = overview
        reference_text, reference_failure = await self._assemble_reference(
            batch,
            history,
            rundown_text,
            forced,
            proactive,
            reminders,
            resume_replay=resume_replay,
            opening_wrapup=opening_wrapup_text,
            game_events=self._round_game_events,
            fresh_game_chat=fresh_game_chat,
            private_game_chat=private_game_chat,
            overview=overview,
            trigger_cause=trigger_cause,
            memory_cues=memory_cues,
            talk_workbench_text=talk_workbench_text,
            talk_cards_text=talk_cards_text,
        )
        if reference_text is None:
            return replace(outcome, error=reference_failure, silent_reason="assembler_failed")

        tool_list = self._build_tool_list()
        # 消息序列：[system] + 历史（user/assistant 原生消息）+ 本批（user）+ 参考段（user，序列尾）；
        # ReAct 循环的 assistant/tool 消息 append-only 追加在参考段之后，绝不插中间。
        messages = assemble_request_messages(
            system_prompt, self._build_dialogue_messages(batch, history, mark_paid=True), reference_text
        )
        # 游戏委派与补充要求同时保留本窗观众原话与近期游戏公屏原话（路点、坐标、物品名逐字送达，不只剩口头概括）
        source_dialogue = self._build_source_dialogue(batch, fresh_game_chat)
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
        # 她本窗说过的台词：本窗历史里的已说台词并上实时口头流水（尚未落库的嘴反应立即可见）
        speech_history = self._collect_speech_history(history)

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
                return replace(outcome, silent_reason=self._silent_reason(held_once, "natural"))

            stop_round = False
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
                    observation, outcome, stop_round = await self._invoke_reply(
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
                if stop_round:
                    return outcome

        # 超步：未完成说话即收场（压住过一句又没再开口时记成 reply_held）
        return replace(outcome, silent_reason=self._silent_reason(held_once, "max_steps"))

    def _silent_reason(self, held_once: bool, default: str) -> str:
        """这一窗没开口的原因：被压住过一句、被嘴的新事实推翻作废过、因提过的旧事被退回过，都单独记。"""
        if held_once:
            return "reply_held"
        if self._round_mouth_bounced:
            return "mouth_superseded"
        return "memory_repeat_held" if self._round_memory_bounced else default

    def _collect_speech_history(self, history: Optional[List[Any]]) -> Optional[List[str]]:
        """合成本窗表达侧可见的已说台词（历史中的台词 + 实时口头流水里尚未入历史的近期台词）。"""
        base = assistant_speech_lines(history)
        if self._recent_speeches_provider is None:
            return base
        try:
            extra = [str(item).strip() for item in (self._recent_speeches_provider() or ()) if str(item).strip()]
        except Exception as exc:  # noqa: BLE001 - 外部流水异常不挡表达
            self.logger.warning(f"读本场已说台词流水失败，退回历史列表: {exc}", exc=True)
            return base
        if not extra:
            return base
        merged = list(base or [])
        for line in extra:
            if line not in merged[-8:]:
                merged.append(line)
        return merged

    async def _load_fresh_context(self, since_ms: int) -> Dict[str, Any]:
        """拉取本窗开窗后新到达的嘴台词、游戏事件与最新现场说明，附在退回观察里供 Planner 重想。"""
        if self._fresh_context_since is None:
            return {}
        try:
            data = await self._fresh_context_since(since_ms)
            return dict(data) if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - 补拉新现场失败时不阻断退回本身
            self.logger.warning(f"拉取对账最新现场失败: {exc}", exc=True)
            return {}

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

    def _repeated_archive_id(self, args: Dict[str, Any]) -> Optional[int]:
        """use_memory 点名的是本场已提过的档案行时返回它的编号，否则 None。

        承诺用上即从简报撤下，不在这里管；读不到已提过的名单时不拦，最多这一句又提一次。
        """
        if self._used_archive_ids_provider is None:
            return None
        ref_text = _single_memory_ref(args.get("use_memory"), logger=self.logger)
        parsed = _parse_memory_ref(ref_text) if ref_text is not None else None
        if parsed is None or parsed[0] != MEMORY_REF_ARCHIVE:
            return None
        try:
            used = {int(item) for item in self._used_archive_ids_provider() or ()}
        except Exception as exc:
            self.logger.warning(f"读本场已提过的档案行失败（这一句不拦复提）: {exc}", exc=True)
            return None
        return parsed[1] if parsed[1] in used else None

    def _reply_player_name(self, args: Dict[str, Any]) -> str:
        """这句在回应游戏里的哪位玩家（口径见 :func:`answering_player`）；点名对不上时记一笔日志。"""
        written = str(args.get("to_player") or "").strip()
        # 本窗新到的加最近几分钟留着的都认：送礼常在上一窗入账、这一窗才谢
        name = answering_player(
            written, str(args.get("topic_summary") or ""), self._round_player_notes, self._recent_player_notes
        )
        if written and not name:
            self.logger.info(f"to_player 写的「{written}」不在最近找她的玩家里，不填回应对象")
        return name

    def _reply_player_identity(self, to_player: str, answering_player: str) -> Tuple[str, str]:
        """这句落库时记成在回应哪位玩家，(游戏, UUID)；记不下时两个空串。

        Planner 在 ``to_player`` 点了名、对得上最近找过她的玩家：按他的条目记（她谢送礼的乙，同窗甲在公屏
        开口也记成回应乙，否则她接住乙的那句进不了玩家自述候选）；他的条目认不出是谁（没有 UUID）时不记。
        没点名时，回应的正是本窗唯一在公屏开口的那位才记。
        """
        if not answering_player:
            return "", ""
        if to_player.strip():
            # 最近的条目在后：同名的取最新那条
            for note in reversed((*self._recent_player_notes, *self._round_player_notes)):
                if note.who == answering_player and note.uuid:
                    return note.game, note.uuid
            return "", ""
        if answering_player == sole_speaker_name(self._round_player_notes):
            return self._round_reply_player
        return "", ""

    def _answers_someone(self, line_id: int, answers_viewer: bool, answering_player: str) -> bool:
        """提过的旧事这一句再接算不算在回人：三种情形任一成立就不退回。

        回的是本场的一条观众弹幕（本窗的，或观众上一窗问、这一窗才回的）；这句在回应游戏里的一位玩家
        （``to_player`` 点名、对得上最近找过她的人，或没点名时话题摘要提到本窗唯一开口的那位——玩家在公屏
        问起旧事、Planner 只在摘要里写了他，退回去她就换话题不答了）；这一条是本窗观众弹幕或玩家开口推上来
        的线索卡。只是本窗有弹幕、有玩家开口不算：热闹时段几乎每窗都有，按这个放行，冷场时主动复提就拦不住了。
        """
        return answers_viewer or bool(answering_player) or line_id in self._round_asked_archive_ids

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
                    social_fact_step=_SOCIAL_FACT_RETRACTION_STEP if self._social_fact_tool_ready() else "",
                ),
                None,
            )
        except Exception as e:
            self.logger.exception(f"渲染 Planner 系统提示词失败: {e}")
            return None, f"prompt_render_failed: {e}"

    def _social_fact_tool_ready(self) -> bool:
        """社交事实工具注册了没有：提示词里点名它的两处（作废条款、主动窗行动选项）只在注册时出现。"""
        return self._tool_registry is not None and self._tool_registry.has(SOCIAL_FACT_TOOL_NAME)

    def _memory_correct_tool_ready(self) -> bool:
        """场内记忆更正工具注册了没有：主动窗行动选项里只在注册时列出。"""
        return self._tool_registry is not None and self._tool_registry.has(MEMORY_CORRECT_TOOL_NAME)

    @property
    def last_overview_text(self) -> str:
        """最近一轮决策前读取到的现场速览文本（供场内记忆更正工具核对现场回执）。"""
        return self._last_overview_text

    def _inside_jokes_block(self) -> str:
        """人物设定档案与老梗栏渲染：都放进「她是谁」一节，整场不随表变化，与人设一起吃前缀缓存。"""
        blocks: List[str] = []
        if self._persona_profile:
            blocks.append(
                f"{self._persona_profile}\n\n"
                "（以上设定档案是她自己的真实底子：自我介绍环节、相关话头展开、或观众问起她的来历/外形/身份时"
                "自然拿来聊；平时聊别的事时不硬扯设定词。）"
            )
        text = self._inside_jokes.strip()
        if text:
            blocks.append("## 直播间老梗（运营者写的；观众提起时才接，别自己翻出来，冷场时也别拿来凑话）\n" + text)
        return "\n\n".join(blocks)

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

    async def _load_opening_wrapup(self) -> Optional[Any]:
        """开播头两窗（且本场尚未受理委派）读取【上次收尾】；其余窗返回 None，失败只告警不挡决策。"""
        if self._opening_wrapup_provider is None:
            return None
        try:
            return await self._opening_wrapup_provider()
        except Exception as exc:  # noqa: BLE001 - 开场交接读不到只少这一块，不挡决策
            self.logger.warning(f"读开播【上次收尾】失败（本窗省略该段）: {exc}", exc=True)
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

    _NON_GAME_GUIDE_PROVIDERS: Tuple[str, ...] = ("avatar", "talk", "streamer", "rundown", "memory")

    def _visible_guides(self) -> List[tuple[str, str]]:
        """注册表里对主播可见的各提供者使用说明；读取失败时降级为空列表。"""
        if self._tool_registry is None:
            return []
        try:
            return list(self._tool_registry.guides(for_agent="streamer"))
        except Exception as exc:
            self.logger.warning(f"读取工具面使用说明失败（本轮按没有游戏处理）: {exc}", exc=True)
            return []

    def _has_delegable_game(self) -> bool:
        """是否存在能接收委派的游戏工具面。"""
        return any(provider == FRAMEWORK_PROVIDER for provider, _text in self._visible_guides())

    def _has_any_game(self) -> bool:
        """是否启用了任何游戏工具面（形象、话题卡等舞台工具不算游戏）。"""
        return any(provider not in self._NON_GAME_GUIDE_PROVIDERS for provider, _text in self._visible_guides())

    def _resolve_speech_mode(self) -> str:
        """读本决策窗说话档位（"talk" / "play"）；未注入或异常时按 "play" 兜底。"""
        if self._speech_mode_provider is None:
            return "play"
        try:
            mode = self._speech_mode_provider()
            return "talk" if mode == "talk" else "play"
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读取说话档位失败（按 play 处理）: {exc}", exc=True)
            return "play"

    def _read_talk_workbench(self, has_viewer_batch: bool) -> str:
        """读取话头工作台本窗渲染文本（【当前话头与本节提纲】）；未注入或异常时为空串。"""
        if self._talk_workbench_provider is None:
            return ""
        try:
            return (self._talk_workbench_provider(has_viewer_batch) or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读取话头工作台失败（本窗省略该段）: {exc}", exc=True)
            return ""

    async def _read_talk_cards(self, has_viewer_batch: bool) -> str:
        """异步刷新话题匣并读取【可抽的话题卡 / 提问箱】文本；未注入或异常时为空串。"""
        if self._talk_cards_provider is None:
            return ""
        try:
            return ((await self._talk_cards_provider(has_viewer_batch)) or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"刷新话题匣失败（本窗不预摆话题卡）: {exc}", exc=True)
            return ""

    def _was_proactive_silent(self) -> bool:
        """上一轮主动决策窗是否以沉默收场（play 档游戏平静时免等满 45 秒即可聊周边素材）。"""
        if self._proactive_silent_provider is None:
            return False
        try:
            return bool(self._proactive_silent_provider())
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读取主动沉默状态失败（按未沉默处理）: {exc}", exc=True)
            return False

    def _render_game_rules(self) -> str:
        """拼装【当前游戏的玩法】：各工具面的使用说明 + 委派型游戏的守则。

        说明来自注册表里对主播可见的各工具面（每个游戏 Agent 自己说这款游戏是
        委派型还是遥控型、该调哪些工具）；框架的委派工具面只在有能接收委派的
        Agent 时才给说明，这时再追加委派型游戏的完整守则（开始做事、补充要求、
        完成口径）。没有任何游戏说明时明说舞台杂谈模式，仍保留形象等非游戏说明。
        """
        guides = self._visible_guides()
        has_game = any(provider not in self._NON_GAME_GUIDE_PROVIDERS for provider, _text in guides)
        lines: List[str] = []
        if not has_game:
            lines.append(self.NO_GAME_RULES)
        lines.extend(f"- {text}" for _provider, text in guides)
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
        opening_wrapup: str = "",
        game_events: Optional[List[str]] = None,
        fresh_game_chat: Optional[List[str]] = None,
        private_game_chat: Optional[List[str]] = None,
        overview: str = "",
        trigger_cause: Optional[TriggerCause] = None,
        memory_cues: Any = None,
        talk_workbench_text: str = "",
        talk_cards_text: str = "",
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
            # signoff / resume 是流程自身触发的收尾轮与接回轮，不是观众付费或点名的
            # 强制回应情境，不按 forced_situation 措辞标注
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
            # 主动窗按当前实际装配说明触发来源与可选行动：没接委派游戏时不提派单与游戏工作日志，
            # 纯杂谈场不会对着空气找游戏工具
            has_game_activity = self._has_any_game() or bool(
                game_events or fresh_game_chat or private_game_chat or self._round_player_notes
            )
            sources = "定时、环节推进或游戏里有新动静" if has_game_activity else "定时或环节推进"
            delegate_actions = (
                "安排游戏里的活（framework_delegate 派单、framework_prompt 补要求、"
                "framework_task_settle 结单/搁下/撤单）、查游戏那边的工作日志、"
                if self._has_delegable_game()
                else ""
            )
            lines.append(
                f"【情境】本窗为主动触发（{sources}）——弹幕可能为空；"
                "这一轮不一定开口，不开口也有别的事可做：推进流程单（rundown_control 的 next / goto）、"
                + delegate_actions
                + "查自己记得的事（memory_recall）、"
                + ("更正简报里的旧记忆（streamer_memory_correct）、" if self._memory_correct_tool_ready() else "")
                + ("记一笔游戏里的社交事实（streamer_social_fact）、" if self._social_fact_tool_ready() else "")
                + "让形象动一下（avatar_act，不出声不占台词预算）；也可以什么都不做，等下一个事件。"
                "这些只是此刻能做的几类，不限于此——手上有什么活、想做什么，就做什么。"
            )
        if opening_wrapup:
            # 开播头两窗（或第一件委派受理前）注入确定性【上次收尾】，让主播开场知道上次停在哪、最后说了什么
            lines.append(opening_wrapup)
        current_ms = now_ms()
        activity = self._room_state.activity_line()
        if activity:
            # 房间活跃度事实一行（观众增量+弹幕量）：互动话术密度与发言策略的校准依据，
            # 空房间连续提问/求押注只会自问自答
            lines.append(f"【房间活跃度】{activity}")
        silence = render_silence(self._room_state.last_speech_ms, self._cold_silence_ms, current_ms)
        if not silence and proactive and not batch:
            # 解开 rundown_ready（15~30s）与【冷场】（45s）的死锁：
            # 杂谈档无弹幕时直接点亮找话提示；边玩边说档在游戏无新事件且上一轮主动窗已沉默过一次时同样点亮
            if self._round_speech_mode == "talk":
                silence = (
                    "【冷场】当前为杂谈节奏且本窗没有新弹幕：按本节提纲或话题卡推进话头，"
                    "也可从设定档案与冷场素材里挑本场没聊过的内容开口"
                )
            elif not game_events and self._was_proactive_silent():
                silence = (
                    "【冷场】游戏那边暂时没有新动静，且上一轮主动窗已沉默过一次："
                    "这一轮可以从冷场素材里挑本场没聊过的内容开口，不必等满冷场阈值"
                )
        if silence:
            lines.append(silence)
        if talk_workbench_text:
            lines.append(talk_workbench_text)
        if talk_cards_text:
            lines.append(talk_cards_text)
        if private_game_chat:
            # 私密游戏聊天：只发给我一个人的话，不进对话历史（表达侧看不到），只在本窗参考段出现。
            # 决策据此安排回应，但原话与内容不能被当众念出或转述
            lines.append("【私密游戏聊天】（只发给我，不在直播里念出或转述）\n" + "\n".join(private_game_chat))
        speakers = render_game_speakers(self._round_player_notes)
        if speakers:
            # 本窗有玩家在游戏公屏跟她说话：点出是谁在等她回话，回就在游戏里回（他听不到直播）
            lines.append(speakers)
        state_facts = self._round_facts_text
        if state_facts:
            # 状态跃迁事实块：只收本轮新入账批次里的死亡/重生/任务开始结束/交付类条目，
            # 供客观核对——叙事讲经历，这里只列可核对的状态
            lines.append(f"【当前状态（客观事实）】\n{state_facts}")
        social_facts_text = render_social_facts(self._round_social_facts, current_ms, age_text)
        if social_facts_text:
            # 游戏社交事实：环境里人与人怎么相处的现行事实（登记下一轮可见）；
            # 游戏里接话、安排给谁做什么时照这里的现状说
            lines.append(social_facts_text)
        # 现读任务账本看板并同步手头在做视图：已送达的进【我手头在游戏里做的事】，其余在【待办】
        boards = self.workbench.sync_from_ledger()
        now_reading = self._read_game_now()
        doing_map = actual_doing_map(self.workbench.delegations, now_reading, current_ms)
        work = render_delegations(
            self.workbench.delegations,
            self.workbench.delegation_status_provider,
            current_ms,
            actual_doing=doing_map,
        )
        if work:
            # 我手头在游戏里做的事：短编号、原话、已做时长、进展，加游戏那边此刻实际在做什么
            lines.append(f"【我手头在游戏里做的事】\n{work}")
        backlog = render_delegation_backlog(boards, current_ms)
        if backlog:
            # 待办：现读账本看板，没送达过的写"还没开工"，先搁下的写"搁下了，做到：……"
            lines.append(f"【待办】\n{backlog}")
        relays = render_recent_relays(self.workbench.recent_relays, current_ms)
        if relays:
            # 补充过的要求紧跟在手头的事后面：同一件事已经交代过什么一眼可见，没有新要求就不再补
            lines.append(f"【我最近给手上的事补充过的要求】\n{relays}")
        intents = render_recent_intents(
            self.workbench.recent_intents,
            self.workbench.delegations,
            current_ms,
            # 滑出最近几轮的话题名也列上：同一个段子隔半小时别再当新话题讲
            earlier_topics=self.workbench.earlier_topics,
        )
        if intents:
            # 已聊话题清单 + 硬规则：复盘发现 46% 的决策窗里模型自己写"别复述"却做不到——
            # 把"重叠必须有新东西"写成显式注入规则，而不是靠模型自觉。新东西只认事实层的
            # 新：换态度、换说法再讲同一件事是换皮复读，不算新；线上回放里 30 句有 27 句
            # 在复述同一个打算，所以讲过的结论和打算单独点名不再复述；拿不出新东西先换
            # 话题，再考虑沉默。
            # 这段话与提示词模板 amaidesu_planner_react.md 的"已聊话题必须有新角度"一条讲的是同一件事，
            # 措辞保持逐字一致（模板里那条是常驻版本，这里是每窗按实况注入的版本）
            talk_tail = (
                "杂谈环节围绕当前提纲要点或话题卡顺着讲出新的具体细节、来龙去脉算推进话头；"
                "把讲过的同一层意思换词重讲才算重复。"
                if self._round_speech_mode == "talk"
                else ""
            )
            lines.append(
                "【已聊话题】（由旧到新，同一话题合并成一行：话题名（轮数，时间跨度）：最近一轮她当时说的那句。"
                "都是当时的说法，不代表游戏里现在的样子）\n"
                f"{intents}\n"
                "硬规则：本轮想说的话题与清单重叠时，必须有事实上的新东西才开口"
                "（新事件、新进展、新的互动对象、新信息）；只是换个态度、换个说法不算；"
                "血量、数量、倒计时这类数值变化不算新东西；清单里讲过的结论和打算不再复述。"
                + talk_tail
                + "拿不出就换话题，或者干脆不开口。"
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
        # 游戏那边此刻（含本轮决策前自动读取的现场速览）整块放在参考段最后：新读数离决策最近，
        # 不被前面已聊话题里的旧说法盖住；每窗重建，不进对话历史
        game_now_text = render_game_now(
            self.workbench.delegations,
            self.workbench.delegation_status_provider,
            now_reading,
            overview,
            current_ms,
        )

        if not self._context_enabled:
            # 裸消息路径：跳过环境快照与记忆召回，仅保留情境标注、私密聊天与游戏那边此刻等本窗素材
            return "\n\n".join(block for block in (situation_text, game_now_text) if block), None

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
        parts = [block for block in (situation_text, assembled_text, cue_text, game_now_text) if block]
        return "\n\n".join(parts), None

    def _read_game_now(self) -> GameNow:
        """读取游戏那边此刻的结构化快照；没接环境或读取出错时退回空快照，不挡决策。"""
        delegations = self.workbench.delegations
        if self._game_now_provider is None:
            return GameNow()
        try:
            return self._game_now_provider(list(delegations))
        except Exception as exc:  # noqa: BLE001 - 读数缺一块不挡决策
            self.logger.warning(f"读游戏那边此刻失败，这一块只给委派与现场速览: {exc}", exc=True)
            return GameNow()

    def _render_game_now(self, overview: str, current_ms: int) -> str:
        """【游戏那边此刻】一块：手上的事各自的读数、近期目标成败、她近几分钟的公屏话，最后是现场速览。"""
        now = self._read_game_now()
        return render_game_now(
            self.workbench.delegations, self.workbench.delegation_status_provider, now, overview, current_ms
        )

    def _build_source_dialogue(self, batch: List[Any], fresh_game_chat: Sequence[str]) -> List[Dict[str, Any]]:
        """拼随委派与补充要求附带的来源原话：本窗观众弹幕 + 近期与本窗游戏公屏原话（路点、坐标、物品名原样保留）。"""
        entries = [message for message in self._build_dialogue_messages(batch, []) if message.get("role") == "user"]
        seen_bodies: Set[str] = set()
        for note in self._recent_player_notes:
            if not (note.said and note.line):
                continue
            body = note.line.split("] ", 1)[-1].strip()
            if body and body not in seen_bodies:
                seen_bodies.add(body)
                entries.append({"role": "game_chat", "content": note.line})
        for raw_line in fresh_game_chat:
            line = str(raw_line or "").strip()
            if not line:
                continue
            body = line.split("] ", 1)[-1].strip()
            if body and body not in seen_bodies:
                seen_bodies.add(body)
                entries.append({"role": "game_chat", "content": line})
        return entries

    def _build_dialogue_messages(
        self,
        batch: List[Any],
        history: Optional[List[Any]],
        *,
        mark_paid: bool = False,
    ) -> List[Dict[str, Any]]:
        """历史 + 本批 → 原生消息序列（user=观众 / assistant=主播）。

        均经 canonical 单一映射（批与历史同形）；历史尾部与本批同源的消息
        剔除——弹幕先落库再进入决策，窗口尾部会与本批重复（按消息 ID 精确
        匹配、原始文本兜底；仅剔尾部连续命中段，更早的同文历史保留为有效
        上下文）。去重在 canonical 化之前做：昵称渲染差异不影响判定。

        ``mark_paid=True`` 时给付过费的观众行加「（付费观众）」标注——只在
        给决策侧的请求里标（表达侧与委派上下文不需要这个事实）。
        """
        batch_messages = [
            canonical.batch_item_to_message(
                msg, paid=mark_paid and paid_viewers.identity_key(msg) in self._round_paid_identities
            )
            for msg in batch
        ]
        if not history:
            return batch_messages
        # 历史已由时间线控制在预算内（超预算的旧段换成了前情提要），这里原样映射
        history_messages = [canonical.turn_to_message(turn) for turn in canonical.trim_batch_echo(history, batch)]
        return history_messages + batch_messages

    async def _load_social_facts(self) -> List[Any]:
        """现行游戏社交事实（每批从库现读）；没接仓储或读失败时为空列表。"""
        if self._social_facts_provider is None:
            return []
        try:
            return list(await self._social_facts_provider())
        except Exception as exc:  # noqa: BLE001 - 读不到只少这一段参考，不挡决策
            self.logger.warning(f"读游戏社交事实失败（本批省略该段）: {exc}", exc=True)
            return []

    async def _collect_paid_identities(self, batch: List[Any]) -> Set[str]:
        """查本批付过费的观众身份键集合（查不到或没装依赖时为空集）。"""
        if not batch or self._paid_identities_provider is None:
            return set()
        try:
            return set(await self._paid_identities_provider(batch))
        except Exception:
            self.logger.warning("查本批观众付费记录失败，本批不加付费标注", exc=True)
            return set()

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
        弹幕批/游戏聊天/状态事实块/她刚说过的台词）打包进 ``ReplyRoundAttachment``、
        随 ``ToolInvocation.attachment`` 传入——材料生命周期 = 调用生命周期，
        调用即弃，无跨轮残留。
        新颖自检（``is_novel`` 申报）在 reply 工具内部执行：打回以工具错误
        形态回到 ReAct 循环。
        """
        if self._tool_registry is None:
            return (
                json.dumps({"ok": False, "error": "reply 工具不可用（tool_registry 未注入）"}, ensure_ascii=False),
                replace(outcome, reply_failures=outcome.reply_failures + 1),
                False,
            )
        invoke_started_ms = now_ms()
        # 这句的回应目标对得上本窗一条弹幕（编号或原文）：对象是那位观众。即使本窗只有一位玩家在公屏开口，
        # 也不记成在回应他——否则答应观众的事会被整理记到玩家头上。对不上的（拿公屏原话当目标）照旧在回玩家
        answers_danmaku = _answers_danmaku(args.get("target"), self._round_danmaku)
        # 回的是本场更早一窗的观众弹幕（上一窗问的、这一窗才回）同样是在回观众，不是在回游戏里的玩家
        answers_viewer = answers_danmaku or _viewer_message_in(args.get("target"), self._round_history)
        answering_player = "" if answers_viewer else self._reply_player_name(args)
        # 本场已经提过的旧事又被点名、这句又不是在回人：先退回一次，让她换个话题。第 58 场 14 条旧事里
        # 8 条各提了两次，第二次全是冷场时主动点名的。每窗只退一次：退过还点名就照常说出去，免得一窗里
        # 接连被退、整窗哑掉；点名留着，这句照样记成提过、计进唠叨指标
        repeated = self._repeated_archive_id(args)
        if repeated is not None and not self._answers_someone(repeated, answers_viewer, answering_player):
            number = render_archive_number(repeated)
            if not self._round_memory_bounced:
                self._round_memory_bounced = True
                self.logger.info(f"{number} 本场已提过、没人问起，这句先退回")
                return (
                    json.dumps({"ok": False, "error": _repeated_memory_error(number)}, ensure_ascii=False),
                    replace(outcome, memory_repeat_bounces=outcome.memory_repeat_bounces + 1),
                    False,
                )
            self.logger.info(f"{number} 本窗已退回过一次，这句照常说（点名留着，照样记成提过）")
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
                # 她本窗开始前说过的台词：表达侧据此看见自己刚说过什么（None = 本窗无历史可查）
                speech_history=speech_history,
                # 最近几分钟玩家找她的条目：谁在公屏说了什么、谁冲她扔了什么，台词照这个归到人头上
                game_people=list(self._recent_player_notes),
                # 回应对象：Planner 在 to_player 点名的玩家；没点名时，本窗唯一在公屏开口、话题摘要里又提到的那位
                reply_to=answering_player,
                speech_mode=self._round_speech_mode,
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
            return (
                json.dumps({"ok": False, "error": result.error_message or "reply 失败"}, ensure_ascii=False),
                replace(outcome, reply_failures=outcome.reply_failures + 1),
                False,
            )

        payload = dict(result.structured_content) if isinstance(result.structured_content, dict) else {}
        raw_speech = str(payload.get("speech") or "").strip()
        checked_ms = self._clock()
        if raw_speech and self._mouth_reconciler is not None:
            # 出声前第一道关口：对照本轮开窗后嘴说出口的话及本场已欢迎名单，拦住重复欢迎、重复反应与相邻矛盾
            verdict = await self._mouth_reconciler(raw_speech, self._round_started_ms)
            if verdict.action == "duplicate":
                self.logger.info(f"Planner 台词已被嘴覆盖或属重复欢迎，本轮不再出声: {raw_speech!r}")
                return (
                    json.dumps({"ok": True, "speech_delivered": False, "covered_by_mouth": True}, ensure_ascii=False),
                    replace(outcome, replied=False, silent_reason="mouth_covered", mouth_checked_ms=checked_ms),
                    True,
                )
            if verdict.action == "contradicted":
                if not self._round_mouth_bounced and outcome.steps < self.max_steps:
                    self._round_mouth_bounced = True
                    fresh_ctx = await self._load_fresh_context(self._round_started_ms)
                    self._round_started_ms = checked_ms
                    self.logger.info(
                        f"Planner 台词与本窗嘴/新事件矛盾（{verdict.reason}），退回按新现场重想一步: {raw_speech!r}"
                    )
                    return (
                        json.dumps(
                            {
                                "ok": False,
                                "error": _mouth_contradicted_error(verdict.reason),
                                "superseded_speech": raw_speech,
                                **fresh_ctx,
                            },
                            ensure_ascii=False,
                        ),
                        outcome,
                        False,
                    )
                self.logger.info(f"Planner 台词与最新现场矛盾且本窗不再重想，静默作废: {raw_speech!r}")
                return (
                    json.dumps({"ok": False, "error": verdict.reason or "台词已过时"}, ensure_ascii=False),
                    replace(outcome, replied=False, silent_reason="mouth_superseded", mouth_checked_ms=checked_ms),
                    True,
                )
            if verdict.action == "trim":
                payload = {**payload, "speech": verdict.speech}

        emotion = payload.get("emotion") if isinstance(payload.get("emotion"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        reply_to = args.get("target")
        raw_card_id = metadata.get("use_card_id", args.get("use_card_id"))
        parsed_card_id: Optional[int] = None
        if raw_card_id is not None and not isinstance(raw_card_id, bool):
            try:
                parsed_card_id = int(raw_card_id)
            except (TypeError, ValueError):
                parsed_card_id = None
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
            # 落库的"回应哪位玩家"和交给表达侧的回应对象同一口径：点了名的按他记，没点名时是本窗唯一开口的那位
            reply_player=self._reply_player_identity(str(args.get("to_player") or ""), answering_player),
            mouth_checked_ms=checked_ms,
            beat_done=bool(metadata.get("beat_done") or args.get("beat_done") is True),
            use_card_id=parsed_card_id,
        )
        # 话说出口了、回应的是游戏里的某位玩家：他到此为止找她的条目标"已接过"，
        # 后面几窗换话题时表达侧不再把同一份礼谢一遍、同一句话接一遍
        if answering_player:
            self._recent_player_notes = mark_answered(self._recent_player_notes, answering_player)
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
