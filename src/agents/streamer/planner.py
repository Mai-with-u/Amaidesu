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
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.agents.streamer import canonical
from src.agents.streamer.delegation_workbench import DelegationWorkbench, observation_data
from src.agents.streamer.plan import ReplyRoundAttachment, assistant_speech_lines
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler, age_text
from src.agents.streamer.planner_render import (
    Intent,
    forced_situation,
    render_delegations,
    render_observation,
    render_recent_intents,
    render_recent_relays,
    render_silence,
    reply_last,
    tool_call_parts,
)
from src.agents.streamer.game_ledger import GameLedger
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

__all__ = ["Planner", "RoundOutcome"]


#: Planner 绑定的 LLM profile（代码显式声明，配置不承载绑定，无静默兜底）。
PLANNER_PROFILE: str = "planner"


#: ReAct 循环默认步数上限（配置 planner_max_steps 可覆盖）。
_DEFAULT_MAX_STEPS: int = 8

#: 画面帧龄告警的最小间隔（毫秒）：断流告警限频，避免长冷场时逐窗刷日志
_STALE_WARN_INTERVAL_MS: int = 5 * 60 * 1000


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


# 没有观众、运营或游戏侧新输入的主动触发原因；这些窗口不给游戏活补充要求。
# signoff（收播道别）同样没有任何新输入：场次已结账，不给游戏活递补充要求。
_NO_INPUT_PROACTIVE_REASONS = frozenset({"rundown", "schedule", "cold", "signoff"})


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


@dataclass(frozen=True)
class PlannerEnv:
    """Planner 运行环境：构造期一次性注入的迟绑定依赖集合。

    Provider 类字段全部可选，``None`` 即对应能力降级（参考段缺段等降级
    语义与原先逐项 bind 缺席时一致）。``reply_provider_factory`` 持工厂
    而非实例——ReplyToolProvider 依赖 Agent start 期才构造的实例，工厂
    化解生命周期错位，Planner 在首次取用时才解析。
    """

    reply_provider_factory: Optional[Callable[[], Any]] = None
    elapsed_live_provider: Optional[Callable[[], Optional[int]]] = None
    frame_provider: Optional[Callable[[], Optional[Any]]] = None
    frame_stale_warn_ms: int = 0
    notebook_text_provider: Optional[Callable[[], str]] = None
    action_recorder: Optional[Callable[[str, str], None]] = None
    delegation_status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None


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
        env: Optional[PlannerEnv] = None,
    ) -> None:
        """初始化 Planner。

        Args:
            config: 配置字典或已解析对象（planner_max_steps）。
            llm_service: LLM 管理器，需提供
                ``async generate(messages, *, tools=..., profile=...) -> payload.Response``
                与 ``is_profile_visual(profile) -> bool``。
            prompt_service: 提示词管理器，需提供 ``render(name, **vars) -> str``。
            room_state: 直播间态势规则层实例。
            tool_registry: 全局 ToolRegistry——ReAct 工具列表来源（信息收集/动作类工具）。
            profile_repo: 观众画像查询（``ViewerProfileRepo`` 结构契约，只用
                ``get_profile_text``）；None 时无画像注入。
            viewer_repo: ``ViewerRepo``（观众仓储）——画像注入时把消息里的外部 ID
                换算成观众、实时取昵称与曾用名（昵称会漂移不进画像文本）；None 时无画像注入。
            context_enabled: 组装器路径开关；False 时以直播流窗口文本为 context_block。
            behavior_style: 人设行为准则（决策侧）。
            bot_name: 主播名字（她是谁）。
            personality: 主播性格描述——替她挑话题、定态度的依据。
            env: 运行环境（迟绑定依赖集合，见 :class:`PlannerEnv`）；缺省
                ``PlannerEnv()`` 即全部可选依赖降级缺席。
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
        # llm_service 另需 async generate（决策推理用）；视觉查询契约在装配期校验，
        # 注入了缺方法的对象时启动即报错，而不是运行期静默按非视觉处理
        validate_llm_capability(llm_service, ProfileVisualCapabilityLookup, parameter="llm_service")
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
        # 本窗是否豁免发言闸的台词重合度检查（收播道别窗：道别话题常与收尾环节发言字面重合）
        self._round_bypass_topic_gate: bool = False
        # 本场备忘录全文查询（经环境注入；空串 = 还没写过，段整段省略）
        self._notebook_text_provider = env.notebook_text_provider if env else None

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
        note = (
            f"【直播画面】观众此刻看到的画面（{age_text(age_ms)}截取）。描述观感、氛围时可以参考；"
            "游戏里的数值和进展以游戏记录和查询结果为准，画面里看不清的不要猜。"
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
        thinking: Optional[ThinkingStreamContext] = None,
        round_id: str = "",
        trigger_reason: str = "",
        session_recap: str = "",
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
            thinking: 思考流上下文（可选；提供时每次 LLM 调用的 reasoning
                增量经旁路通道外发）。
            round_id: 决策轮次 ID（工具调用经 ToolInvocation.round_id 透传到
                tool.result 事件，供观察器归属；空串表示无轮次关联）。
            trigger_reason: 本窗触发原因码（如 ``proactive:rundown``）；用于判断这一窗
                有没有新的外部输入，没有时不给手上的游戏活补充要求。
            session_recap: 上场回顾文本（开播后固定，整场不变；空串 = 没有上一场或未启用）。

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
        self.workbench.begin_round()
        # 游戏聊天原话（非私聊）随本轮说话交给表达侧；私聊行只进本窗参考段
        fresh_game_chat = list(resolved_batch.fresh_chat)
        private_game_chat = list(resolved_batch.private_chat)
        # 收播道别窗整窗豁免台词重合度检查（见 _round_bypass_topic_gate 声明处）
        self._round_bypass_topic_gate = trigger_reason == "proactive:signoff"
        # 发言闸打回计数是本窗口径：由 Provider 检测 round_id 变化清零（每窗
        # 首次 reply 调用前生效，时序与本入口等价）

        outcome = RoundOutcome()

        system_prompt, prompt_failure = self._render_system_prompt()
        if system_prompt is None:
            return replace(outcome, error=prompt_failure, silent_reason="prompt_render_failed")

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
            game_events=self._round_game_events,
            fresh_game_chat=fresh_game_chat,
            private_game_chat=private_game_chat,
            overview=overview,
            session_recap=session_recap,
            trigger_reason=trigger_reason,
        )
        if reference_text is None:
            return replace(outcome, error=reference_failure, silent_reason="assembler_failed")

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

        # 发言闸语料：本窗历史里的已说台词，窗起点算一次随单携带（reply 工具
        # 不再为查重单独读历史——读是纯读，无决策窗出账副作用）
        speech_history = assistant_speech_lines(history)

        # 直播画面只挂在本窗第一次请求的尾部：后续 ReAct 步骤靠模型第一步的判断继续
        frame_tail = self._live_frame_message()
        steps = 0
        while steps < self.max_steps:
            steps += 1
            outcome = replace(outcome, steps=steps)

            # 上一步工具返回的截图只挂在这一次请求尾部、不写回消息序列：下一步请求时它已消失，
            # 前缀与这一次逐字一致；画面内容由模型这一步的思考与决定带下去
            tail = self._transient_images.drain()
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
                return replace(outcome, error=f"llm_error: {e}", silent_reason="llm_error")

            if not getattr(response, "success", False):
                error = getattr(response, "error", None) or "unknown"
                return replace(outcome, error=f"llm_failed: {error}", silent_reason="llm_failed")

            outcome = replace(
                outcome,
                raw_content=getattr(response, "content", None) or "",
                llm_request_id=getattr(response, "request_id", None) or None,
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
                return replace(outcome, silent_reason="natural")

            replied = False
            # 同一步里并列的委派/环节切换先执行、说话最后收尾：reply 一成功本轮即结束，
            # 排在它后面的调用若不先做就会被丢掉（例如说完话却没切到下一个环节）
            for call in reply_last(tool_calls):
                if not isinstance(call, dict):
                    continue
                name, args = tool_call_parts(call)
                if not name:
                    continue
                outcome = replace(outcome, tool_trace=[*outcome.tool_trace, name])

                if name == "streamer_reply":
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
        return replace(outcome, silent_reason="max_steps")

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
                    game_rules=self._render_game_rules(),
                ),
                None,
            )
        except Exception as e:
            self.logger.exception(f"渲染 Planner 系统提示词失败: {e}")
            return None, f"prompt_render_failed: {e}"

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
    ) -> Tuple[Optional[str], Optional[str]]:
        """构造参考段，返回（参考段文本, 失败原因）。

        内容 = 情境标注（强制/主动，按触发原因取条件子句）+ 私密游戏聊天 + 运营提醒 +
        组装器元数据段（上场回顾 / 环节描述 / 直播间快照 / 人物画像）。对话内容与
        游戏事件不在此处——历史（含 game 条目）与本批走 canonical 映射的原生消息通道。
        组装失败时文本为 None，失败原因随 outcome.error 写进决策事件。
        """
        signoff_window = trigger_reason == "proactive:signoff"
        lines: List[str] = []
        if forced and not signoff_window:
            # signoff 的 forced 只是闸门豁免语义（绕过台词重合度检查保住道别），不是
            # 付费/点名的强制回应情境，不按 forced_situation 措辞标注
            lines.append(forced_situation(batch))
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
            return None, f"assembler_failed: {e}"

        parts = [block for block in (situation_text, assembled_text) if block]
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
            attachment = ReplyRoundAttachment(
                # 思考流回调只在调用单上活一次，不经 LLM、模型不可伪造
                thinking_callback=thinking.callback_for("replyer", 1) if thinking else None,
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
            reply_to=reply_to if isinstance(reply_to, str) and reply_to else None,
        )
        # 话说出口了才记下这轮的打算；表达失败或被净化丢弃的不算，观众没听到
        self.workbench.note_intent(
            Intent(
                at_ms=now_ms(),
                topic_summary=outcome.topic_summary,
                reply_guidance=outcome.reply_guidance,
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

    # ==================== 人物画像注入 ====================

    async def _collect_person_profiles(
        self,
        batch: List[Any],
    ) -> str:
        """按本批发言时间倒序注入全部已有印象（观众画像），完整保留人物经历与偏好。

        消息只带外部 ID：先换算成观众（同一个人换了数据源也是同一份画像），按观众去重；
        还没建档的（记账器尚未落库的新观众）没有画像，跳过。
        """
        if self._profile_repo is None or self._viewer_repo is None or not batch:
            return ""

        # 候选收集：发言时间倒序去重（最新发言优先；batch 本身按时间正序）
        candidates: List[tuple[str, str]] = []  # (platform, 外部 ID)
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

        lines: List[str] = []
        injected: set[int] = set()
        for platform, user_id in candidates:
            try:
                viewer_id = await self._viewer_repo.find_viewer_id(platform=platform, external_id=user_id)
                if viewer_id is None or viewer_id in injected:
                    continue
                injected.add(viewer_id)
                profile_text = await self._profile_repo.get_profile_text(viewer_id=viewer_id)
            except Exception as exc:
                self.logger.warning(f"画像查询失败（{platform}/{user_id}，跳过该人）: {exc}")
                continue
            if not profile_text:
                continue  # 无画像不注入、不占位
            text = profile_text.replace("\n", " ").strip()
            display = await self._viewer_display(viewer_id) or f"{platform}/{user_id}"
            lines.append(f"- {display}: {text}")

        if not lines:
            return ""
        header = "（内部参考，帮助识别老观众；不要逐字复述，与当前对话冲突时以当前对话为准）"
        return "\n".join([header, *lines])

    async def _viewer_display(self, viewer_id: int) -> str:
        """观众的展示名：当前昵称，改过名的带上曾用名（"乙（以前叫甲）"）。

        场次回顾按当时的名字写，主播要知道"乙就是以前的甲"才对得上号；查询失败回退空串。
        """
        try:
            row = await self._viewer_repo.get_viewer(viewer_id=viewer_id)
            if row is None:
                return ""
            name = str(row["user_name"])
            former = await self._viewer_repo.list_former_names(viewer_id=viewer_id, current_name=name)
        except Exception as exc:
            self.logger.warning(f"观众昵称查询失败（viewer {viewer_id}）: {exc}")
            return ""
        return f"{name}（以前叫{'、'.join(former)}）" if former else name
