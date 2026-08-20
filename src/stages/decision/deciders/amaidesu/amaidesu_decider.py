"""AmaidesuDecider - 直播专用决策器（双阶段编排：Planner + Replyer）

设计沿革（Task 10 + Task 11）：
- Stage 1 节奏门控（TimingGate + MessageBuffer）：
  - MessageBuffer 聚合突发弹幕，按时间窗口/条数上限/空窗补偿/force 决定何时取出一批。
  - TimingGate（Task 11 精简）：仅保留 is_forced 强制触发判定；sampling/backoff 已移除，
    "要不要发言"的裁决下沉到 Stage 2 的 Planner（should_reply）。
- Stage 2 两阶段内容决策（替代旧的单阶段 LLM 调用）：
  - Planner（战术决策者）：判断"要不要参与 + 如何参与"，产出 DecisionPlan。
    使用快速模型 planner_client（默认 llm_fast），零人设注入。
  - Replyer（回复生成器）：消费 plan + 人设 + 弹幕，生成实际 Intent。
    使用高质量模型 replyer_client（默认 llm），注入 bot_name/personality/style_constraints。
  - 两者解耦：Planner 失败 → silent；Planner should_reply=False → Replyer 不调用；
    Replyer 失败 → silent。失败计数分别记录到 planner_failures / replyer_failures。

约束：
- 只使用 Amaidesu 自己的 LLMManager / PromptManager / ContextService，不依赖 MaiBot。
- 不订阅 input/output 事件，由 DeciderManager 调用 decide()；只发布 decision.intent.generated。
- RoomState 提供热度信号给 Planner；RoomStateLoop 后台低频 LLM 摘要填充 topic_summary。
- 本期输出 speech + emotion（12 枚举），action 字段由 Replyer 经能力白名单校验。
"""

from typing import Any, Dict, List, Literal, Optional

import asyncio

from pydantic import Field

from src.stages.decision.registry import decider
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.service import ConfigService
from src.modules.context import ContextService, MessageRole
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.types import Intent
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.capabilities import CapabilitiesProvider
from src.modules.types.message_type import require_message_type
from src.modules.time_utils import format_duration_ms, now_ms

from .message_buffer import MessageBuffer
from .outline_loader import OutlineLoader
from .outline_scheduler import OutlineScheduler
from .outline_state import OutlineState
from .planner import Planner
from .proactive_trigger import ProactiveTrigger
from .replyer import Replyer
from .room_state import RoomState
from .room_state_loop import RoomStateLoop
from .timing_gate import TimingGate


@decider("amaidesu", label="Amaidesu 决策", description="直播专用决策器（Planner + Replyer 两阶段）")
class AmaidesuDecider:
    """直播专用决策器（双阶段编排：Planner + Replyer）。"""

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Decider 注册信息"""
        return {"layer": "decision", "name": "amaidesu", "class": cls, "source": "builtin:amaidesu"}

    class ConfigSchema(BaseConfig):
        """直播决策器配置 Schema。

        Task 11 已清理：participation_rate / no_action_backoff_base_ms /
        no_action_backoff_cap_ms（采样率与冷场退避已下沉到 Planner 的 should_reply 裁决）。
        保留 client / fallback_mode / use_llm_timing_gate 字段以维持向后兼容
        （决策器不再使用，但既有配置文件与 Dashboard UI 仍可能引用）。
        """

        # Stage 2 LLM（保留字段：向后兼容 stats / get_info，新代码使用 planner_client/replyer_client）
        client: Literal["llm", "llm_fast", "vlm"] = Field(
            default="llm_fast", description="（保留）单阶段 LLM 客户端，新代码用 planner_client/replyer_client"
        )
        fallback_mode: Literal["silent", "simple", "echo"] = Field(
            default="silent", description="（保留）LLM 失败降级模式；两阶段重构后固定 silent"
        )
        history_limit: int = Field(default=30, ge=0, description="构建 prompt 时引用的历史消息条数")

        # Stage 1 弹幕聚合（两阶段调优：调大窗口/上限，让 Planner 看到更多上下文）
        batch_window_ms: int = Field(default=3000, ge=0, description="弹幕聚合时间窗口（毫秒），原 1500")
        batch_max_size: int = Field(default=20, ge=1, description="单批最多聚合的弹幕条数，原 8")
        tick_interval_ms: int = Field(default=300, ge=50, description="后台聚合检查间隔（毫秒）")

        # Stage 1 节奏门控（仅保留强制触发判定；Task 11 移除采样/退避）
        # 计划要求：SC / guard / 礼物均强制响应（Final Wave 合规）
        force_data_types: List[str] = Field(
            default_factory=lambda: ["super_chat", "guard", "gift"], description="强制响应的数据类型"
        )
        force_importance: float = Field(default=0.8, ge=0.0, le=1.0, description="importance 达到该值则强制响应")

        # 可选 LLM 节奏门控（保留字段：两阶段重构后已删除代码路径，Planner 接管该职责）
        use_llm_timing_gate: bool = Field(default=False, description="（保留，已废弃）两阶段重构后 Planner 接管该职责")

        # 动作选择
        enable_action_selection: bool = Field(
            default=True, description="是否让 LLM 从 OutputHandler 能力中选择动作（需注入能力提供者）"
        )

        # 人设默认值（无 persona 配置时使用）
        bot_name: str = Field(default="爱德丝", description="VTuber 名称")

        # ===== 两阶段决策字段（Task 5）=====
        # 两阶段客户端：Planner 用快速模型生成动作意图，Replyer 用高质量模型生成回复
        planner_client: Literal["llm", "llm_fast", "vlm"] = Field(
            default="llm_fast", description="两阶段-Planner 使用的 LLM 客户端（快速模型）"
        )
        replyer_client: Literal["llm", "llm_fast", "vlm"] = Field(
            default="llm", description="两阶段-Replyer 使用的 LLM 客户端（高质量模型）"
        )
        # 空窗补偿：人少时避免冷场，默认开启
        enable_idle_compensation: bool = Field(default=True, description="空窗补偿开关：人少时不冷场（默认开启）")
        # 房间状态后台预处理：冷场 60s 自动降频/暂停控制成本
        room_state_enabled: bool = Field(default=True, description="是否启用房间状态后台预处理（默认开启）")
        room_state_cold_timeout_ms: int = Field(
            default=60000, ge=0, description="房间冷场判定阈值（毫秒），超过则视为冷场"
        )
        room_state_llm_summary_interval_ms: int = Field(
            default=60000, ge=0, description="低频 LLM 房间状态摘要间隔（毫秒）"
        )
        # 摘要专用 LLM profile：必须独立于 planner_client（默认 llm_fast），
        # 否则 LLMManager 按 profile 名复用同一 client 实例，违反"独立连接池"约束（Task 8）
        room_state_summary_client: str = Field(
            default="llm_summary",
            description="房间状态摘要专用 LLM profile（独立 client 实例，避免与 Planner 共享）",
        )

        # ===== 主动发言（Proactive Speech）字段（Task 6）=====
        # 触发器通过 sub-dict 接收这些字段（去掉 proactive_ 前缀映射到 unprefixed 键），
        # 默认全关以保持向后兼容：现有部署不受影响，需显式开启 proactive_enabled=True。
        proactive_enabled: bool = Field(default=False, description="主动发言总开关（默认关闭，需显式开启）")
        proactive_cold_timeout_ms: int = Field(
            default=45000, ge=0, description="冷场判定阈值（毫秒），超过该时长无弹幕视为冷场"
        )
        proactive_min_interval_ms: int = Field(default=120000, ge=0, description="两次主动发言最小间隔（毫秒，防接龙）")
        proactive_schedule_interval_ms: int = Field(
            default=300000, ge=0, description="定时话题触发间隔（毫秒），0 表示关闭定时触发"
        )
        proactive_schedule_only_cold: bool = Field(
            default=True, description="定时触发是否仅限冷场（True=仅冷场时触发，False=无视冷场）"
        )
        proactive_max_per_hour: int = Field(default=6, ge=1, description="每小时主动发言次数上限（频率限制）")
        proactive_topic_required: bool = Field(default=True, description="话题摘要缺失时是否跳过触发（防无话找话）")

        # ===== 直播大纲（Live Stream Outline）字段（Task 2）=====
        outline_enabled: bool = Field(default=False, description="直播大纲总开关（默认关闭，需显式开启）")
        outline_path: str = Field(default="", description="大纲 TOML 文件路径（相对项目根）；为空时不加载任何大纲")
        outline_expand_client: str = Field(
            default="llm_outline",
            description="AI 扩展用 LLM profile（独立连接池，仿 llm_summary 先例）",
        )
        outline_advance_eval_enabled: bool = Field(
            default=True,
            description="Planner 顺带评估开关：产出 may_advance / need_more_time / branch_id",
        )
        outline_scheduler_tick_ms: int = Field(
            default=1000, ge=1, description="大纲调度循环 tick 间隔（毫秒），默认 1s"
        )
        outline_auto_start: bool = Field(
            default=True,
            description="setup 时自动加载并启动大纲（需 outline_enabled=True 且 outline_path 非空）",
        )
        outline_speech_interval_ms: int = Field(
            default=3000,
            ge=1000,
            description="大纲环节内两次主动发言最小间隔（毫秒），仅 outline 触发源使用，不受 proactive_min_interval_ms 约束",
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        llm_service: LLMManager,
        prompt_service: PromptManager,
        config_service: Optional[ConfigService] = None,
        context_service: Optional[ContextService] = None,
        capabilities_provider: Optional[CapabilitiesProvider] = None,
        room_state: Optional[RoomState] = None,
    ):
        self.typed_config = self.ConfigSchema.from_dict(config)
        self.logger = get_logger("AmaidesuDecider")

        for t in self.typed_config.force_data_types:
            require_message_type(t)

        self._event_bus = event_bus
        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._config_service = config_service
        self._context_service = context_service
        self._capabilities_provider = capabilities_provider

        # 房间态势（可注入，默认新建）—— 提供热度信号给 Planner
        self._room_state = room_state or RoomState()

        # 两阶段子组件
        self._planner = Planner(
            config=self.typed_config,
            llm_service=llm_service,
            prompt_service=prompt_service,
            room_state=self._room_state,
            capabilities_provider=capabilities_provider,
        )
        self._replyer = Replyer(
            config=config,
            llm_service=llm_service,
            prompt_service=prompt_service,
            event_bus=event_bus,
            context_service=context_service,
            capabilities_provider=capabilities_provider,
        )
        self._room_state_loop = RoomStateLoop(
            config=self.typed_config,
            room_state=self._room_state,
            llm_service=llm_service,
            prompt_service=prompt_service,
            context_service=context_service,
        )

        # Task 9: 将 batch 参数和空窗补偿开关注入 MessageBuffer
        self._buffer = MessageBuffer(
            batch_window_ms=self.typed_config.batch_window_ms,
            batch_max_size=self.typed_config.batch_max_size,
            enable_idle_compensation=self.typed_config.enable_idle_compensation,
        )
        # TimingGate（Task 11 精简）：仅保留强制触发判定，采样/退避已移除
        self._timing_gate = TimingGate(
            force_data_types=self.typed_config.force_data_types,
            force_importance=self.typed_config.force_importance,
        )

        # 主动发言触发器（Task 6）：
        # ProactiveTrigger 内部用 unprefixed 键（enabled / cold_timeout_ms / ...），
        # 而 ConfigSchema 字段为 proactive_* 前缀；这里手工构造 sub-dict 去掉前缀映射。
        # proactive_enabled → enabled（其余 6 个字段按规律映射）。
        # outline_speech_interval_ms 是 outline 触发源独立字段，不走 proactive_ 前缀。
        self._proactive_trigger = ProactiveTrigger(
            config={
                "enabled": self.typed_config.proactive_enabled,
                "cold_timeout_ms": self.typed_config.proactive_cold_timeout_ms,
                "min_interval_ms": self.typed_config.proactive_min_interval_ms,
                "schedule_interval_ms": self.typed_config.proactive_schedule_interval_ms,
                "schedule_only_cold": self.typed_config.proactive_schedule_only_cold,
                "max_per_hour": self.typed_config.proactive_max_per_hour,
                "topic_required": self.typed_config.proactive_topic_required,
                "outline_speech_interval_ms": self.typed_config.outline_speech_interval_ms,
            }
        )
        # 外部 API 触发的"一次性"待消费标志（DeciderManager.trigger_proactive → 这里）
        self._external_proactive_pending: bool = False

        # ===== 直播大纲（Live Stream Outline）组件（Task 10）=====
        # 仅在 setup() 中按 ``outline_enabled`` 决定是否构造。失败时全部置 None（降级为
        # "无大纲"模式继续），不中断直播。所有 outline_* 鸭子类型方法在组件为 None 时
        # 返回 501 风格响应，由 DeciderManager 透传给 Dashboard API 层。
        self._outline_loader: Optional[OutlineLoader] = None
        self._outline_state: Optional[OutlineState] = None
        self._outline_scheduler: Optional[OutlineScheduler] = None
        # 当前已加载的大纲文件路径（供 outline_load 重载 + outline_segments 暴露给前端）
        self._outline_loader_path: Optional[str] = None
        # 大纲推进回调触发的一次性"待消费"标志——on_advance 同步方法仅置位，
        # 实际触发在 _maybe_flush 下一 tick 的 buffer 空分支内消费，行为对齐
        # ``_external_proactive_pending`` 模式
        self._outline_proactive_pending: bool = False

        # 旧字段保留（向后兼容 stats / get_info）
        self.client_type = self.typed_config.client
        self.fallback_mode = self.typed_config.fallback_mode

        self._running = False
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()

        # 统计信息：保留原有 + 新增 planner/replyer 失败计数
        self._total_messages = 0
        self._total_batches = 0
        self._total_replies = 0
        self._total_no_action = 0
        self._failed_requests = 0  # 旧字段保留（向后兼容 stats），新代码不再主动递增
        self._planner_failures = 0
        self._replyer_failures = 0
        # 主动发言触发次数（Task 6 新增）
        self._total_proactive = 0

    async def setup(self) -> None:
        """启动后台聚合循环 + 房间状态后台摘要循环。"""
        self.logger.info("初始化 AmaidesuDecider（双阶段：Planner + Replyer）...")
        if self._llm_service is None:
            raise RuntimeError("LLM Manager 未注入！请确保在 setup 中正确配置。")

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        # 启动房间状态后台摘要循环（默认启用，可通过 room_state_enabled=False 关闭）
        await self._room_state_loop.start()
        # 启动直播大纲组件（Task 10）：
        # 仅 outline_enabled=True 时构造；outline_path 为空 / 加载失败时降级为 None，
        # 直播继续正常运行（outline_* 鸭子类型方法返回 501）。
        if self.typed_config.outline_enabled:
            await self._start_outline_components()
        self.logger.info(
            f"AmaidesuDecider 初始化完成 "
            f"(Planner client={self.typed_config.planner_client}, "
            f"Replyer client={self.typed_config.replyer_client}, "
            f"聚合窗口={self.typed_config.batch_window_ms}ms)"
        )

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """接收单条消息：更新房间态势 + 入缓冲（快速返回，决策在后台循环完成）。"""
        self._total_messages += 1
        # 房间态势热度信号（Planner 通过 RoomState.get_snapshot 读取）
        self._room_state.update(normalized_message)
        forced = self._timing_gate.is_forced(normalized_message)
        self._buffer.add(normalized_message, arrival_ms=now_ms(), forced=forced)

    async def trigger_proactive(self, topic_hint: Optional[str] = None) -> None:
        """外部触发主动发言入口（由 DeciderManager.trigger_proactive 转发调用）。

        仅置 ``_external_proactive_pending`` 标志位，实际触发在下一 flush tick
        内的 ``_flush_lock`` 临界区中完成（先消费标志，再由 ProactiveTrigger
        的 should_trigger 判定是否满足前置条件）。topic_hint 仅做日志标注，
        当前实现不参与触发决策。

        Args:
            topic_hint: 可选话题提示（来自 Dashboard API），仅用于日志。
        """
        self._external_proactive_pending = True
        self.logger.info(f"收到外部主动发言触发: {topic_hint}")

    async def cleanup(self) -> None:
        """停止后台循环（聚合 + 房间状态摘要）并打印统计。"""
        self.logger.info("清理 AmaidesuDecider...")
        self._running = False

        # 停止房间状态后台摘要循环
        await self._room_state_loop.stop()

        # 停止直播大纲调度循环（Task 10，仿 room_state_loop.stop() 模式）
        if self._outline_scheduler is not None:
            try:
                await self._outline_scheduler.stop()
            except Exception as e:
                self.logger.error(f"停止 OutlineScheduler 失败: {e}", exc_info=True)
            self._outline_scheduler = None
        # 组件句柄清空，鸭子类型方法在 cleanup 后返回 501
        self._outline_loader = None
        self._outline_state = None
        self._outline_loader_path = None
        self._outline_proactive_pending = False

        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        self.logger.info(
            f"统计: 消息={self._total_messages}, 批次={self._total_batches}, "
            f"发言={self._total_replies}, no_action={self._total_no_action}, "
            f"主动发言={self._total_proactive}, "
            f"planner_failures={self._planner_failures}, "
            f"replyer_failures={self._replyer_failures}"
        )
        self.logger.info("AmaidesuDecider 已清理")

    # ==================== Stage 1: 聚合 + 节奏门控 ====================

    async def _flush_loop(self) -> None:
        """后台循环：周期性检查缓冲并触发批次决策。"""
        interval = self.typed_config.tick_interval_ms / 1000.0
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._maybe_flush()
                except Exception as e:
                    self.logger.error(f"批次决策异常: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _maybe_flush(self) -> None:
        """判断是否应该取出一批并做两阶段决策。

        流程（Task 6 重构：把 is_empty 提前 return 改为锁内分支判断）：
        1. 上一批仍在决策 → 跳过本 tick（避免 LLM 调用交叠）
        2. 获取 _flush_lock（覆盖分支1+分支2 完整临界区）：
           - 分支2（buffer 空时）：ProactiveTrigger 主动发言判定
           - 分支1（buffer 非空时）：弹幕聚合 should_flush → 两阶段决策
        """
        # 上一批仍在决策时跳过本 tick，避免发言交叠堆积
        if self._flush_lock.locked():
            return

        async with self._flush_lock:
            # ===== 分支2：主动发言（buffer 空时）=====
            # 必须放在锁内：保证"外部 trigger_proactive + 本 tick 主动触发"原子；
            # 锁外提前 return 会让 buffer 空时永远进不来此处。
            if self._buffer.is_empty:
                # 一次性消费 _outline_proactive_pending（on_advance 同步置位 → 本 tick 消费）
                # 与 _external_proactive_pending 同样在锁内消费，保证标志生命周期清晰
                outline_pending = self._outline_proactive_pending
                self._outline_proactive_pending = False
                # outline_pending（环节切换即时信号）单独传给 ProactiveTrigger：仅受总开关
                # 约束，立即触发；outline_ready（环节内持续发言信号）由 _is_outline_active
                # 判定，绕过通用 min_interval/max_per_hour/topic_required，按
                # outline_speech_interval_ms 节奏触发。
                # 优先级：external > outline > schedule > cold（ProactiveTrigger 内部处理）
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=self._external_proactive_pending,
                    outline_pending=outline_pending,
                    outline_ready=self._is_outline_active(),
                )
                # 一次性消费 external_pending（无论是否触发，都视为已处理）
                self._external_proactive_pending = False
                if reason is not None:
                    self._total_proactive += 1
                    self.logger.info(f"主动发言触发: {reason}")
                    await self._make_two_stage_decision(
                        [],
                        forced=False,
                        trigger_reason=f"proactive:{reason}",
                        proactive=True,
                    )
                return

            # ===== 分支1：弹幕决策（原逻辑，Task 6 仅位置调整）=====
            now = now_ms()
            avg_interval_ms = self._estimate_avg_interval_ms()
            flush_due, flush_reason = self._buffer.should_flush(now, avg_interval_ms=avg_interval_ms)
            if not flush_due:
                return

            forced = self._buffer.force
            batch = self._buffer.drain()
            if not batch:
                return

            self._total_batches += 1

            # TimingGate（Task 11 精简）：恒通过；forced 标识用于日志和 Planner 上下文
            act, reason = self._timing_gate.should_act(forced=forced)
            if not act:
                self.logger.info(f"节奏门控跳过本批 ({len(batch)} 条), 原因: {reason}")
                return

            await self._make_two_stage_decision(batch, forced=forced, trigger_reason=flush_reason)

    def _estimate_avg_interval_ms(self) -> Optional[float]:
        """根据当前缓冲内容估算平均消息间隔（毫秒），供空窗补偿折算使用。

        Returns:
            平均间隔毫秒数；缓冲不足或时间跨度为 0 时返回 None
        """
        buf = self._buffer
        if buf.size < 2:
            return None
        span = buf.last_arrival_ms - buf.first_arrival_ms
        if span <= 0:
            return None
        return span / (buf.size - 1)

    # ==================== Stage 2: 两阶段内容决策（Planner → Replyer） ====================

    async def _make_two_stage_decision(
        self,
        batch: List["NormalizedMessage"],
        *,
        forced: bool,
        trigger_reason: str,
        proactive: bool = False,
    ) -> None:
        """对一批弹幕做两阶段决策（Planner → Replyer），成功时发布 Intent。

        流程：
        1. Planner.plan(batch, forced=forced, proactive=proactive, history=history) → Optional[DecisionPlan]
           - None（异常/脏 JSON）→ silent 降级，planner_failures+1
        2. plan.should_reply=False → 不发布，no_action+1
        3. Replyer.generate(plan, batch, persona, history=history) → Optional[Intent]
           - None（异常/空 text）→ silent 降级，replyer_failures+1
        4. event_bus.emit(decision.intent.generated, IntentPayload)
        5. 保存上下文（ContextService） + 记录发言时刻（proactive 时同时 record_trigger）
        """
        # 空批次时 batch[-1] 不存在，下游取 source_message_id 时会抛 IndexError；
        # 主动发言场景无源消息，使用占位 ID。
        is_proactive_call = proactive or not batch
        session_id = next((m.session_id for m in batch if m.session_id), "live")

        # 读取会话历史（让 Planner/Replyer 看到自己最近说过的话，避免冷场复读）
        history = await self._read_history(session_id)

        # ① Planner：战术决策（产出 DecisionPlan 或 None）
        self.logger.info(
            f"Planner 决策中 ({len(batch)} 条, 触发: {trigger_reason}, forced={forced}, proactive={proactive})"
        )
        # 拼装 outline 上下文（Task 10）：从 OutlineState 取当前环节详情 + 整场进度信息
        # → 渲染为 outline_text 注入 Planner/Replyer 的 $outline 变量。
        # 大纲未激活时返回 None，Planner/Replyer 内部走"无大纲"占位。
        outline_text = self._build_outline_text()
        try:
            plan = await self._planner.plan(
                batch,
                forced=forced,
                proactive=proactive,
                history=history,
                outline_text=outline_text,
            )
        except Exception as e:
            # 防御性兜底：Planner 内部已捕获异常返回 None
            self.logger.error(f"Planner 调用异常: {e}", exc_info=True)
            plan = None

        if plan is None:
            self._planner_failures += 1
            self._timing_gate.record_result(replied=False)
            self._total_no_action += 1
            self.logger.warning(f"Planner 失败 (batch={len(batch)} 条), silent 降级")
            return

        # ② Plan 裁决：should_reply=False → Replyer 不调用
        if not plan.should_reply:
            self._timing_gate.record_result(replied=False)
            self._total_no_action += 1
            self.logger.info(f"Planner 决定本批不发言 (confidence={plan.confidence:.2f}, target={plan.target!r})")
            # 消费 Planner 顺带评估（即便不发言：AI 也可能在 should_reply=false 时给出评估意见）
            self._consume_plan_assessment(plan)
            return

        # ③ Replyer：基于 plan + 人设 + 弹幕生成 Intent
        persona = self._get_persona_config()
        try:
            intent = await self._replyer.generate(plan, batch, persona, history=history, outline=outline_text)
        except Exception as e:
            # 防御性兜底：Replyer 内部已捕获异常返回 None
            self.logger.error(f"Replyer 调用异常: {e}", exc_info=True)
            intent = None

        if intent is None:
            self._replyer_failures += 1
            self._timing_gate.record_result(replied=False)
            self._total_no_action += 1
            self.logger.warning(f"Replyer 失败 (plan.target={plan.target!r}), silent 降级")
            return

        # ④ 发布 Intent + 保存上下文
        # 主动发言时 batch 为空，使用占位 source_message_id（不破坏 metadata 必填字段）
        intent.metadata.source_message_id = batch[-1].message_id if batch else "proactive"
        # 填充可观察性字段（Task 调试大纲用）：trigger_reason 来自决策流程已知上下文，
        # outline_segment_id 来自当前 OutlineState（未启用时保持 None）。
        # 这两个字段是 IntentMetadata 的可选字段（Intent 默认 may be None），写不写都行
        # —— 但要写就一次写齐，避免下游 dashboard 误判"发言与大纲无关"。
        intent.metadata.trigger_reason = trigger_reason
        intent.metadata.outline_segment_id = (
            self._outline_state.current_segment_id
            if self._is_outline_active() and self._outline_state is not None
            else None
        )
        await self._publish_intent(intent)
        # intent.speech 类型为 Optional[str]，但 Replyer 返回成功时必然非空
        # 主动发言时 USER 行携带 Planner 的主题（topic_summary），让后续决策显式看到
        # "上次聊到哪了"，而非从发言文本反推主题（机制层连续性保障）
        if is_proactive_call:
            user_content = f"（主动发言，主题：{plan.topic_summary or '随聊'}）"
        else:
            user_content = MessageBuffer.render_batch_text(batch)
        await self._save_context(
            session_id,
            user_content,
            intent.speech or "",
        )

        self._timing_gate.record_result(replied=True)
        self._total_replies += 1
        self.logger.info(f"AmaidesuDecider 发言: {intent.speech}")

        # ⑤ 主动发言频率限制状态更新（Task 6）：
        # - 任何成功发布的发言都更新 last_speech_ms（防接龙，由 ProactiveTrigger 读）
        # - 主动触发时才 record_trigger（更新 _last_trigger_ms，供 schedule 间隔判定）
        now = now_ms()
        self._room_state.record_speech(now_ms=now)
        if is_proactive_call:
            # trigger_reason 形如 "proactive:cold" / "proactive:schedule" / "proactive:external"，
            # ProactiveTrigger 只需 reason 后缀做日志/统计，本组件不区分处理。
            reason = trigger_reason.removeprefix("proactive:") if trigger_reason else "unknown"
            self._proactive_trigger.record_trigger(reason, now)

        # ⑥ 消费 Planner 顺带评估（Task 10）：may_advance/need_more_time/branch_id
        # Scheduler 内部"全默认值 = 尊重沉默"——即便 LLM 没输出评估字段也安全。
        # 此处不阻塞当前发言：on_advance 触发的 proactive 在下一 tick 消费
        self._consume_plan_assessment(plan)

    # ==================== 直播大纲（Live Stream Outline）辅助方法（Task 10）====================

    async def _start_outline_components(self) -> None:
        """构造 + 启动大纲组件（Loader / State / Scheduler）。

        流程：
            1. 检查 ``outline_path`` 非空（为空时仅日志提示，不构造任何组件）
            2. 构造 :class:`OutlineLoader` 并 ``await load(path)``
            3. 构造 :class:`OutlineState` 并 ``start(outline)``（推进到首段、记锚点）
            4. 构造 :class:`OutlineScheduler` 并 ``start()``，注入 ``on_advance`` 回调
            5. 任意步骤异常 → log + 清理已构造组件 + 降级为 None（直播继续运行）

        配置：
            - ``outline_auto_start=False`` 时仅构造 Loader/State，不启动 Scheduler
              （用于延迟启动场景，Task 10 默认 True）
        """
        path = self.typed_config.outline_path
        if not path:
            self.logger.info("大纲未配置路径（outline_path 为空），跳过加载")
            return
        try:
            self._outline_loader = OutlineLoader(
                llm_manager=self._llm_service,
                prompt_manager=self._prompt_service,
                config=self.typed_config,
            )
            outline = await self._outline_loader.load(path)
            self._outline_state = OutlineState()
            self._outline_state.start(outline)
            self._outline_loader_path = path
            if self.typed_config.outline_auto_start:
                self._outline_scheduler = OutlineScheduler(
                    config=self.typed_config,
                    state=self._outline_state,
                    loader=self._outline_loader,
                    on_advance=self._on_outline_advance,
                )
                await self._outline_scheduler.start()
            self.logger.info(
                f"大纲已加载: path={path!r}, outline_id={outline.outline_id!r}, "
                f"segments={len(outline.segments)}, "
                f"auto_start={self.typed_config.outline_auto_start}"
            )
        except Exception as e:
            self.logger.error(
                f"大纲加载失败，降级为无大纲模式（直播继续）: {e}",
                exc_info=True,
            )
            # 失败时清理已部分构造的组件
            if self._outline_scheduler is not None:
                try:
                    await self._outline_scheduler.stop()
                except Exception:
                    pass
            self._outline_loader = None
            self._outline_state = None
            self._outline_scheduler = None
            self._outline_loader_path = None

    def _is_outline_active(self) -> bool:
        """当前是否处于"大纲激活且有当前环节"状态。

        用于 ``_maybe_flush`` 判定 ``outline_ready`` 参数（Task 8 ProactiveTrigger 新增）
        以及 ``_build_outline_text`` 是否返回非 None 内容。
        """
        if self._outline_state is None:
            return False
        if self._outline_state.status.value != "running":
            return False
        return self._outline_state.current_segment_id is not None

    def _find_outline_segment(self, segment_id: str) -> Any:
        """按 id 在当前大纲中查找环节对象（duck-typed）。

        Args:
            segment_id: 目标环节 id

        Returns:
            :class:`OutlineSegment` 或 None（未加载/未找到）
        """
        if self._outline_state is None or self._outline_state.outline is None:
            return None
        for seg in self._outline_state.outline.segments:
            if getattr(seg, "id", None) == segment_id:
                return seg
        return None

    def _build_outline_text(self) -> Optional[str]:
        """拼装供 Planner/Replyer ``$outline`` 变量注入的文本（Task 10）。

        格式（与 Task 7 模板约定一致）::

            当前环节：<title>（第 N/M 环节）
            任务：<task_description>
            话题引导：<topic_guidance from expanded_cache, 可选>
            关键节点：<key_points.join('、'), 可选>
            环节剩余：约 <format_duration_ms(remaining_ms)>
            整场进度：已进行 <elapsed> / 共 <total>（<pct>%）

        Returns:
            渲染后的多行文本；大纲未激活时返回 None（Planner/Replyer 走"无大纲"占位）。
        """
        if not self._is_outline_active():
            return None
        state = self._outline_state
        assert state is not None  # 仅供类型推断，_is_outline_active 已守
        seg_id = state.current_segment_id
        if seg_id is None:
            return None
        seg = self._find_outline_segment(seg_id)
        if seg is None:
            return None
        outline = state.outline
        if outline is None:
            return None
        seg_ids = [s.id for s in outline.segments]
        try:
            idx = seg_ids.index(seg_id)
        except ValueError:
            return None
        total = len(outline.segments)
        title = getattr(seg, "title", "") or ""
        task = getattr(seg, "task_description", "") or ""
        key_points = list(getattr(seg, "key_points", []) or [])
        # 话题引导：来自 OutlineLoader 异步扩展缓存（未扩展时退化为任务描述）
        expanded = state.get_expanded(seg_id)
        topic_guidance = (getattr(expanded, "topic_guidance", "") if expanded is not None else "") or task
        remaining_ms = state.get_current_segment_remaining_ms()
        elapsed_live = state.get_elapsed_live_ms()
        total_planned = state.get_total_planned_ms()
        progress_pct = state.get_progress_percent()

        lines: List[str] = []
        lines.append(f"当前环节：{title}（第 {idx + 1}/{total} 环节）")
        if task:
            lines.append(f"任务：{task}")
        if topic_guidance and topic_guidance != task:
            # 任务与引导内容一致时省略引导，避免冗余
            lines.append(f"话题引导：{topic_guidance}")
        if key_points:
            lines.append(f"关键节点：{'、'.join(key_points)}")
        lines.append(f"环节剩余：约 {format_duration_ms(remaining_ms)}")
        if elapsed_live is not None and total_planned is not None and progress_pct is not None:
            lines.append(
                f"整场进度：已进行 {format_duration_ms(elapsed_live)} / "
                f"共 {format_duration_ms(total_planned)}（{progress_pct:.0f}%）"
            )
        return "\n".join(lines)

    def _consume_plan_assessment(self, plan: Any) -> None:
        """消费 Planner 顺带产出的 AI 评估字段（Task 10 接线入口）。

        委托 :meth:`OutlineScheduler.note_plan_assessment`；scheduler 内部对
        "全默认值（may_advance=False / need_more_time=False / branch_id=None）"视为
        尊重沉默，不会触发任何动作；本方法仅在 ``outline_scheduler`` 已构造 +
        ``outline_advance_eval_enabled=True`` 时调用。

        失败隔离：任何异常被吞掉 + log，不影响当前发言已发布的结果。
        """
        if not self.typed_config.outline_advance_eval_enabled:
            return
        if self._outline_scheduler is None:
            return
        if plan is None:
            return
        try:
            self._outline_scheduler.note_plan_assessment(
                may_advance=getattr(plan, "may_advance", False),
                need_more_time=getattr(plan, "need_more_time", False),
                branch_id=getattr(plan, "branch_id", None),
            )
        except Exception as e:
            self.logger.error(f"消费 Planner 评估异常（不影响当前发言）: {e}", exc_info=True)

    def _on_outline_advance(self, new_segment_id: str, reason: Optional[str]) -> None:
        """``OutlineScheduler`` 推进回调（同步方法，签名由 Scheduler 定义）。

        行为：仅置 ``_outline_proactive_pending=True``，让下一次 ``_maybe_flush`` 的
        buffer 空分支以 ``outline`` 触发源立即触发一次 proactive 发言（按新环节
        任务描述生成开场内容）。reason 仅做日志标注，不参与触发决策。

        同步方法的原因：Scheduler 的 ``on_advance`` 签名为 ``Callable[[str, Optional[str]], None]``，
        而 proactive 决策链由 ``_flush_loop`` 异步驱动——回调只需"埋点"，不直接调 LLM。
        """
        self._outline_proactive_pending = True
        self.logger.info(
            f"大纲推进回调: new_segment={new_segment_id!r}, reason={reason!r}, "
            f"已置 outline_proactive_pending=True（下一 tick 消费）"
        )

    # ==================== 鸭子类型方法：Dashboard 控制接口（Task 10/11）====================

    async def outline_state(self) -> Dict[str, Any]:
        """返回当前大纲运行时快照（供 Dashboard ``GET /api/v1/outline/state``）。

        透传 :meth:`OutlineState.get_snapshot`，字段契约与 API 层 ``_build_state_response``
        期望一致（status / current_segment / next_segment / completed_count / total_count /
        is_paused / elapsed_live_ms / total_planned_ms / progress_percent）。

        Returns:
            :class:`OutlineState.get_snapshot` 风格 dict；组件未构造时返回 501 风格
            标记 dict（由 :class:`DeciderManager.outline_state` 透传给 Dashboard API 层
            转换为 HTTP 501）。
        """
        if self._outline_state is None:
            return {
                "error": "not_implemented",
                "status_code": 501,
                "method": "outline_state",
                "message": "大纲未激活（outline_enabled=False 或加载失败）",
            }
        try:
            return self._outline_state.get_snapshot()
        except Exception as e:
            self.logger.error(f"outline_state 异常: {e}", exc_info=True)
            return {"error": "unknown", "status_code": 500, "detail": str(e)}

    async def outline_transitions(self) -> Dict[str, Any]:
        """返回大纲推进历史（供 Dashboard ``GET /api/v1/outline/transitions``）。

        透传 :meth:`OutlineState.get_transitions`，返回 ``{"loaded": True,
        "transitions": [...]}``；组件未构造时返回 ``{"loaded": False, "transitions": []}``。
        失败隔离：异常被吞 + log，鸭子类型方法仍返回正常响应（避免 500 中断调试端点）。

        Returns:
            推进历史 dict（含 ``loaded`` 标志）；大纲未激活时 ``loaded=False`` + 空列表。
        """
        if self._outline_state is None:
            return {"loaded": False, "transitions": []}
        try:
            transitions = self._outline_state.get_transitions()
        except Exception as e:
            self.logger.error(f"outline_transitions 异常: {e}", exc_info=True)
            return {"loaded": True, "transitions": []}
        return {"loaded": True, "transitions": transitions}

    async def outline_load(self, path: str) -> Dict[str, Any]:
        """加载指定 TOML 大纲文件（供 Dashboard ``POST /api/v1/outline/load``）。

        流程：
            1. 检查组件已构造（无则惰性构造 Loader + State，便于用户在 outline_enabled=False
               时仍可通过 API 加载大纲）
            2. ``await loader.load(path)`` → :class:`StreamOutline`
            3. ``state.unload()`` + ``state.start(new_outline)`` 重置运行时
            4. 重启 Scheduler（先 stop 再 start，保持 on_advance 回调绑定）

        Returns:
            成功：``{"ok": True, "path": ..., "outline_id": ...}``；
            失败：``{"ok": False, "error": "not_found" | "parse_error" | ..., "detail": ...}``。
        """
        try:
            # 惰性构造（允许 outline_enabled=False 时通过 API 加载）
            if self._outline_loader is None or self._outline_state is None:
                self._outline_loader = OutlineLoader(
                    llm_manager=self._llm_service,
                    prompt_manager=self._prompt_service,
                    config=self.typed_config,
                )
                self._outline_state = OutlineState()
            loader = self._outline_loader
            state = self._outline_state
            assert loader is not None and state is not None  # 仅为类型推断
            try:
                outline = await loader.load(path)
            except FileNotFoundError:
                return {
                    "ok": False,
                    "error": "not_found",
                    "detail": f"大纲文件不存在: {path}",
                }
            except (ValueError, Exception) as e:
                # tomllib.TOMLDecodeError 与 pydantic.ValidationError 都属 Exception 子类
                err_name = type(e).__name__
                # 区分 TOML 语法错误与字段校验错误
                if "TOML" in err_name or "Validation" in err_name:
                    return {
                        "ok": False,
                        "error": "parse_error",
                        "detail": str(e),
                    }
                return {
                    "ok": False,
                    "error": "load_failed",
                    "detail": str(e),
                }
            # 重置 state + scheduler
            if self._outline_scheduler is not None:
                try:
                    await self._outline_scheduler.stop()
                except Exception as e:
                    self.logger.warning(f"旧 scheduler 停止失败（继续重载）: {e}")
            state.unload()
            state.start(outline)
            self._outline_loader_path = path
            self._outline_scheduler = OutlineScheduler(
                config=self.typed_config,
                state=state,
                loader=loader,
                on_advance=self._on_outline_advance,
            )
            await self._outline_scheduler.start()
            return {"ok": True, "path": path, "outline_id": outline.outline_id}
        except Exception as e:
            self.logger.error(f"outline_load 异常: {e}", exc_info=True)
            return {"ok": False, "error": "unknown", "detail": str(e)}

    async def outline_control(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """手动控制大纲推进（供 Dashboard ``POST /api/v1/outline/control``）。

        委托 :class:`OutlineState` 对应方法（skip / pause / resume / rewind / jump_to）。
        失败隔离：异常被吞 + log，鸭子类型方法仍返回 200 + 错误字段（由 API 层映射）。

        Args:
            action: 控制动作字符串（skip / pause / resume / rewind / jump）
            **kwargs: 透传参数（``segment_id`` 用于 jump）

        Returns:
            成功：``{"ok": True, "current_segment_id": ...}``；
            失败：``{"ok": False, "error": "no_active_outline" | "invalid_action" |
            "segment_not_found" | "unknown", "detail": ...}``。
        """
        if self._outline_state is None:
            return {
                "ok": False,
                "error": "no_active_outline",
                "detail": "大纲未加载（先调用 outline_load）",
            }
        state = self._outline_state
        try:
            if action == "skip":
                state.skip()
            elif action == "pause":
                state.pause()
            elif action == "resume":
                state.resume()
            elif action == "rewind":
                state.rewind()
            elif action == "jump":
                seg_id = kwargs.get("segment_id")
                if not seg_id:
                    return {
                        "ok": False,
                        "error": "invalid_action",
                        "detail": "jump 操作必须指定 segment_id",
                    }
                state.jump_to(seg_id)
            else:
                return {
                    "ok": False,
                    "error": "invalid_action",
                    "detail": f"未知 action: {action!r}",
                }
        except ValueError as e:
            return {"ok": False, "error": "segment_not_found", "detail": str(e)}
        except Exception as e:
            self.logger.error(f"outline_control 异常: {e}", exc_info=True)
            return {"ok": False, "error": "unknown", "detail": str(e)}
        return {"ok": True, "current_segment_id": state.current_segment_id}

    async def outline_save_file(self, path: str, content: str) -> Dict[str, Any]:
        """把编辑后的大纲 TOML 写回磁盘（供 Dashboard ``PUT /api/v1/outline/file``）。

        语义：保存到磁盘**不**主动触发热重载——下一段生效是既定契约（Task 11）。
        路径安全：拒绝含 ``..`` 段的路径，防越权写入。

        Args:
            path: 目标 TOML 文件路径（相对项目根或绝对路径）
            content: TOML 完整内容（覆盖写入）

        Returns:
            成功：``{"ok": True, "path": ..., "bytes_written": ...}``；
            失败：``{"ok": False, "error": "invalid_path" | "permission_denied" | ...}``。
        """
        try:
            from pathlib import Path as _Path

            p = _Path(path)
            # 防越权：拒绝路径中含 .. 段
            if ".." in p.parts:
                return {
                    "ok": False,
                    "error": "invalid_path",
                    "detail": f"路径含 .. 段: {path}",
                }
            p.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            p.write_bytes(data)
            return {"ok": True, "path": str(p), "bytes_written": len(data)}
        except PermissionError as e:
            return {"ok": False, "error": "permission_denied", "detail": str(e)}
        except Exception as e:
            self.logger.error(f"outline_save_file 异常: {e}", exc_info=True)
            return {"ok": False, "error": "write_failed", "detail": str(e)}

    async def outline_segments(self) -> Dict[str, Any]:
        """返回当前大纲完整环节列表（供 Dashboard ``GET /api/v1/outline/segments``）。

        字段契约（与 API 层 ``_build_segments_response`` 期望一致）：
            - ``loaded``: bool
            - ``outline_id`` / ``title`` / ``fallback_segment_id`` / ``path``
            - ``segments``: 环节对象列表（每个含 id / title / task_description /
              duration_ms / min_duration_ms / key_points / branches，API 层做序列化）
            - ``expanded_cache``: :class:`OutlineState.expanded_cache`（Duck-typed）；
              API 层据此为每段附加 ``expanded`` 详情，未缓存段为 ``None``

        Returns:
            大纲元数据 + segments 列表 + expanded_cache；未加载时返回
            ``{"loaded": False, "segments": [], "expanded_cache": {}}``。
        """
        if self._outline_state is None or self._outline_state.outline is None:
            return {"loaded": False, "segments": [], "expanded_cache": {}}
        outline = self._outline_state.outline
        return {
            "loaded": True,
            "outline_id": outline.outline_id,
            "title": outline.title,
            "fallback_segment_id": outline.fallback_segment_id,
            "path": self._outline_loader_path,
            "segments": list(outline.segments),
            "expanded_cache": self._outline_state.expanded_cache,
        }

    # ==================== 辅助方法 ====================

    async def _read_history(self, session_id: str) -> Optional[List]:
        """从 ContextService 读取最近会话历史（供 Planner/Replyer 反复读）。

        契约：
        - ``self._context_service`` 未注入时返回 None（不中断决策链路）。
        - 异常时 logger.warning 记录并返回 None（让两阶段调用按无历史降级，
          避免因历史服务故障导致整批弹幕静默）。

        Args:
            session_id: 会话 ID（与 ``_save_context`` 使用同一来源，默认 "live"）。

        Returns:
            会话历史列表（ConversationMessage 鸭子类型）；无服务或异常时返回 None。
        """
        if self._context_service is None:
            return None
        try:
            return await self._context_service.get_history(session_id, limit=self.typed_config.history_limit)
        except Exception as e:
            self.logger.warning(f"读取会话历史失败 (session_id={session_id}): {e}")
            return None

    async def _save_context(self, session_id: str, danmaku_batch: str, speech: str) -> None:
        """将本批弹幕与回复写回上下文（可选服务）。"""
        if not self._context_service:
            return
        try:
            await self._context_service.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=danmaku_batch,
            )
            await self._context_service.add_message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=speech,
            )
        except Exception as e:
            self.logger.warning(f"保存上下文失败: {e}")

    def _get_persona_config(self) -> Dict[str, Any]:
        """构建 Replyer 所需的 persona 字典。

        合并优先级（高 → 低）：
        1. config_service.get_section("persona", {}) 的字段（personality / style_constraints 等）
        2. ConfigSchema.bot_name（始终提供 bot_name 默认值）

        Returns:
            persona 字典，至少包含 bot_name。Replyer 对 personality / style_constraints
            缺失字段有自己的兜底默认值。
        """
        # 默认 persona：bot_name 来自 ConfigSchema
        persona: Dict[str, Any] = {"bot_name": self.typed_config.bot_name}

        if self._config_service is None:
            return persona

        try:
            section = self._config_service.get_section("persona", {})
        except Exception as e:
            self.logger.warning(f"读取 persona 配置失败: {e}")
            return persona

        if isinstance(section, dict):
            # 合并：section 覆盖默认；若 section 未提供 bot_name 则保留 ConfigSchema 默认
            persona.update(section)
            persona.setdefault("bot_name", self.typed_config.bot_name)
        return persona

    async def _publish_intent(self, intent: Intent) -> None:
        """通过 event_bus 发布 decision.intent.generated 事件。

        Guardrail：本类只允许发布此唯一事件，不得新增其他事件名。
        """
        if not self._event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return
        await self._event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, "amaidesu"),
            source="AmaidesuDecider",
        )

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息（结构向后兼容，新增 planner_failures / replyer_failures / total_proactive）。"""
        return {
            "total_messages": self._total_messages,
            "total_batches": self._total_batches,
            "total_replies": self._total_replies,
            "total_no_action": self._total_no_action,
            "total_proactive": self._total_proactive,
            "failed_requests": self._failed_requests,
            "planner_failures": self._planner_failures,
            "replyer_failures": self._replyer_failures,
            "client_type": self.client_type,
        }

    def get_info(self) -> Dict[str, Any]:
        """获取 Decider 配置信息。"""
        return {
            "name": "AmaidesuDecider",
            "version": "2.0.0",  # 两阶段编排版本
            "client_type": self.client_type,
            "planner_client": self.typed_config.planner_client,
            "replyer_client": self.typed_config.replyer_client,
            "template_name": "decision/amaidesu_planner",
            "fallback_mode": self.fallback_mode,
        }
