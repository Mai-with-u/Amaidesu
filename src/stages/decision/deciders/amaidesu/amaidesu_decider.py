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
from src.modules.time_utils import now_ms

from .message_buffer import MessageBuffer
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
        self._proactive_trigger = ProactiveTrigger(
            config={
                "enabled": self.typed_config.proactive_enabled,
                "cold_timeout_ms": self.typed_config.proactive_cold_timeout_ms,
                "min_interval_ms": self.typed_config.proactive_min_interval_ms,
                "schedule_interval_ms": self.typed_config.proactive_schedule_interval_ms,
                "schedule_only_cold": self.typed_config.proactive_schedule_only_cold,
                "max_per_hour": self.typed_config.proactive_max_per_hour,
                "topic_required": self.typed_config.proactive_topic_required,
            }
        )
        # 外部 API 触发的"一次性"待消费标志（DeciderManager.trigger_proactive → 这里）
        self._external_proactive_pending: bool = False

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
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=self._external_proactive_pending,
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
        try:
            plan = await self._planner.plan(batch, forced=forced, proactive=proactive, history=history)
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
            return

        # ③ Replyer：基于 plan + 人设 + 弹幕生成 Intent
        persona = self._get_persona_config()
        try:
            intent = await self._replyer.generate(plan, batch, persona, history=history)
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
