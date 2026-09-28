"""SimulatorService 集成测试

测试目标：验证 SimulatorService 作为"开发基础设施"的真实行为契约：

① 构造 + setup 读配置（enabled=false 不启动，零 LLM 调用）
② enabled=true + mock EventBus 下启动，emit room.message.danmaku 且 payload.simulated=True
③ stop/cleanup 幂等（重复调用不抛异常，资源正确清理）
④ CancelledError 传播语义（stop() 期间外层 cancel 必须透传，不被吞成正常返回）

LLM 注入：service.py:_find_llm_service 用 duck-type 检测 services_by_type
（需 generate / setup 等属性），测试用假服务对象模拟。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Generator, List, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.simulator import SimulatorService
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="sim-int-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def sim_store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# 测试夹具：fake LLMManager（duck-type 注入 services_by_type）
# ---------------------------------------------------------------------------


class _FakeLLMService:
    """满足 SimulatorService._find_llm_service duck-type 检测的假 LLM 服务。"""

    def __init__(self, reply: str = "模拟观众弹幕", tokens: int = 10) -> None:
        self._reply = reply
        self._tokens = tokens
        self.chat_calls: int = 0

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.chat_calls += 1
        from src.modules.llm.payload import Response, Usage

        return Response(
            success=True,
            content=self._reply,
            usage=Usage(total_tokens=self._tokens),
        )

    async def setup(self, config: Any) -> None:
        pass

    async def cleanup(self) -> None:
        pass


class _FakeConfigService:
    """Fake ConfigService：仅暴露 main_config 字段。"""

    def __init__(self, simulator_cfg: Dict[str, Any]) -> None:
        self.main_config = {"simulator": simulator_cfg}


def _enabled_config(**overrides: Any) -> Dict[str, Any]:
    """生成 enabled=True 的 simulator 段配置（其余走 schema 默认）。"""
    cfg: Dict[str, Any] = {"enabled": True}
    cfg.update(overrides)
    return cfg


def _disabled_config() -> Dict[str, Any]:
    """enabled=False（生产默认）。"""
    return {"enabled": False}


# ---------------------------------------------------------------------------
# ① 构造 + setup 读配置（enabled=false 不启动）
# ---------------------------------------------------------------------------


class TestSetupAndReadConfig:
    """SimulatorService 构造 + setup 行为契约。"""

    def test_constructor_does_not_start(self) -> None:
        """SimulatorService 构造不启动主循环（仅初始化内存状态）。"""
        event_bus = EventBus()
        service = SimulatorService(event_bus=event_bus)
        assert service.is_running is False

    @pytest.mark.asyncio
    async def test_setup_disabled_does_not_start(self) -> None:
        """enabled=false 时 setup() 不启动主循环，也不需要 LLMManager。"""
        event_bus = EventBus()
        service = SimulatorService(
            event_bus=event_bus,
            services_by_type={},  # 无 LLM 注入
        )
        await service.setup(_FakeConfigService(_disabled_config()))
        assert service.is_running is False

    @pytest.mark.asyncio
    async def test_setup_with_auto_start_false_skips_run(self) -> None:
        """auto_start=False 时 setup() 不启动主循环（即使 enabled=true）。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(
            _FakeConfigService(_enabled_config()),
            auto_start=False,
        )
        assert service.is_running is False
        assert fake_llm.chat_calls == 0  # 没产生 LLM 调用


# ---------------------------------------------------------------------------
# ② enabled=true 启动并 emit room.message.danmaku（simulated=True）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_emits_danmaku_with_simulated_flag(sim_store: SQLiteDatabase) -> None:
    """enabled=true + mock EventBus 下启动 → emit room.message.danmaku 且 payload.simulated=True。"""
    event_bus = EventBus()
    received: List[RoomMessagePayload] = []

    async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
        if isinstance(payload, RoomMessagePayload):
            received.append(payload)

    event_bus.on(
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        _capture,
        model_class=RoomMessagePayload,
    )

    # 用紧凑节奏配置加快首条消息产出（fixed_interval_s 约束 ≥1.0）
    fake_llm = _FakeLLMService(reply="测试弹幕", tokens=5)
    service = SimulatorService(
        event_bus=event_bus,
        sim_repo=sim_store.sim,
        chat_repo=sim_store.chat,
        services_by_type={type(fake_llm): fake_llm},
    )
    await service.setup(
        _FakeConfigService(
            _enabled_config(
                cadence_mode="fixed",
                fixed_interval_s=1.0,  # 节奏字段约束 ge=1.0
                gift_probability=0.0,  # 关闭礼物分支，只走弹幕
                warmup_duration_s=0.0,
            )
        )
    )

    # 等待最多 3s 让主循环产出至少一条弹幕（fixed 模式 1s 间隔）
    deadline = asyncio.get_event_loop().time() + 3.0
    while not received and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)

    try:
        assert service.is_running is True, "SimulatorService 应已自动启动"
        assert len(received) >= 1, "至少应 emit 一条 room.message.danmaku"
        payload = received[0]
        assert payload.simulated is True, "数据溯源标记 simulated 必须为 True"
        assert payload.message_type == "danmaku"
        assert payload.content  # 文本非空（来自 mock LLM 回复）
    finally:
        # 收尾：必须 cleanup 否则 task 残留
        await service.cleanup()


# ---------------------------------------------------------------------------
# ③ stop/cleanup 幂等
# ---------------------------------------------------------------------------


class TestStopCleanupIdempotent:
    """stop/cleanup 重复调用不抛异常，资源正确清理。"""

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self) -> None:
        """未启动时 stop() 直接返回，不抛异常。"""
        event_bus = EventBus()
        service = SimulatorService(event_bus=event_bus)
        await service.stop()  # 不应抛
        assert service.is_running is False

    @pytest.mark.asyncio
    async def test_cleanup_without_start_is_noop(self) -> None:
        """未启动时 cleanup() 直接返回，不抛异常。"""
        event_bus = EventBus()
        service = SimulatorService(event_bus=event_bus)
        await service.cleanup()  # 不应抛

    @pytest.mark.asyncio
    async def test_double_cleanup_idempotent(self) -> None:
        """重复 cleanup() 不抛异常。"""
        event_bus = EventBus()
        service = SimulatorService(event_bus=event_bus)
        await service.cleanup()
        await service.cleanup()  # 重复调用不抛

    @pytest.mark.asyncio
    async def test_full_lifecycle_cleanup(self, sim_store: SQLiteDatabase) -> None:
        """start → stop → cleanup → 再次 cleanup 全流程幂等。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)))
        assert service.is_running is True
        await service.stop()
        assert service.is_running is False
        await service.cleanup()
        await service.cleanup()  # 幂等
        # cleanup 后子系统已清空
        assert service._llm_wrapper is None
        assert service._persona_pool is None


# ---------------------------------------------------------------------------
# ④ CancelledError 传播语义（服务停止时取消必须透传）
# ---------------------------------------------------------------------------


class TestCancelledErrorPropagation:
    """stop() / cleanup() 期间外层 CancelledError 必须透传，不被吞成正常返回。"""

    @pytest.mark.asyncio
    async def test_stop_propagates_cancellation(self, sim_store: SQLiteDatabase) -> None:
        """在 stop() 内部模拟外层取消：CancelledError 必须传播到调用方。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)))
        assert service.is_running is True

        # 模拟 stop() 协程本身被外层 cancel 的场景：
        # 直接给当前 task 标记取消，再调 stop()，观察 CancelledError 是否透传
        current_task = asyncio.current_task()
        assert current_task is not None
        current_task.cancel()
        # stop() 必须透传 CancelledError（不是默默吞掉变正常返回）
        with pytest.raises(asyncio.CancelledError):
            await service.stop()

    @pytest.mark.asyncio
    async def test_run_loop_re_raises_cancelled(self, sim_store: SQLiteDatabase) -> None:
        """主循环 _run() 捕获 CancelledError 后必须重新 raise，保持任务取消语义。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)))
        # 主动 cancel task
        assert service._task is not None
        service._task.cancel()
        # 等待 task 终止，CancelledError 必须传播
        with pytest.raises(asyncio.CancelledError):
            await service._task
        await service.cleanup()


# ---------------------------------------------------------------------------
# ⑤ 回放自然完成收场（队列耗尽 → 落场复位，可再次启动）
# ---------------------------------------------------------------------------


class TestReplayNaturalFinish:
    """回放队列耗尽后服务必须自动落场：is_running 复位、task 清空、可立即再启动。"""

    @pytest.mark.asyncio
    async def test_natural_finish_resets_running_and_restartable(self, sim_store: SQLiteDatabase) -> None:
        event_bus = EventBus()
        received: List[RoomMessagePayload] = []

        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            if isinstance(payload, RoomMessagePayload):
                received.append(payload)

        event_bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU, _capture, model_class=RoomMessagePayload)

        # 种入同一本地日期的两条弹幕（相邻 1s；replay_speed=100 近似全速回放）
        date_str = "2026-09-01"
        base_ms = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        for i, (name, content) in enumerate([("观众甲", "第一条"), ("观众乙", "第二条")]):
            await sim_store.chat.insert_live_chat(
                live_session_id=1,
                timestamp_ms=base_ms + i * 1000,
                sender_role="viewer",
                sender_id=f"uid_{name}",
                sender_name=name,
                content=content,
                message_type="danmaku",
                simulated=False,
            )

        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(
            _FakeConfigService(_enabled_config(mode="replay", replay_date=date_str, replay_speed=100.0))
        )
        try:
            assert service.is_running is True, "replay 模式 + 默认日期就绪，setup 应自动启动回放"

            # 队列仅 2 条且全速回放：等服务自然落场（5s 上限兜底）
            deadline = asyncio.get_event_loop().time() + 5.0
            while service.is_running and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)

            assert len(received) == 2, f"两条录制弹幕都应被回放（实际 {len(received)} 条）"
            assert service.is_running is False, "回放自然完成后 is_running 必须复位，不得永久挂 true"
            assert service._active_mode == "off"
            assert service._task is None

            # 落场后应可立即再次启动（重建回放队列）
            await service.start()
            assert service.is_running is True
        finally:
            await service.cleanup()


# ---------------------------------------------------------------------------
# ⑥ start 顺序与语义（场次先于回放循环 / 拒绝原因 / 启动即完成 / Fatal 收场）
# ---------------------------------------------------------------------------


class _FakeSessionManager:
    """duck-type 场次管理器：记录调用顺序，供 start 顺序断言。"""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.opened_pk: Optional[int] = None

    async def open_session(self, *, title: str, source: str) -> int:
        self.calls.append(f"open:{source}")
        self.opened_pk = 42
        return 42

    async def close_session(self, *, reason: str) -> None:
        self.calls.append("close")

    async def resolve_pk(self) -> int:
        return 42


class _FatalLLMService:
    """恒返 FatalError 的假 LLM 服务（duck-type 注入）。"""

    def __init__(self) -> None:
        self.chat_calls: int = 0

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.chat_calls += 1
        from src.modules.llm.payload import Response, Usage

        return Response(
            success=False,
            content="",
            error="全部模型失败 ['deepseek-flash']: FatalError: 请求被服务端拒绝（HTTP 402）",
            usage=Usage(total_tokens=0),
        )

    async def setup(self, config: Any) -> None:
        pass

    async def cleanup(self) -> None:
        pass


async def _insert_danmaku(store: SQLiteDatabase, date_str: str, rows: int) -> None:
    """种入同一本地日期的连续弹幕（相邻 1s）。"""
    base_ms = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
    for i in range(rows):
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base_ms + i * 1000,
            sender_role="viewer",
            sender_id=f"uid_{i}",
            sender_name=f"观众{i}",
            content=f"第{i}条",
            message_type="danmaku",
            simulated=False,
        )


class TestReplayStartOrdering:
    """start() 内部顺序契约：场次必须先于回放循环开启（小队列回放竞态防护）。"""

    @pytest.mark.asyncio
    async def test_session_opens_before_first_replay_message(self, sim_store: SQLiteDatabase) -> None:
        event_bus = EventBus()
        session_mgr = _FakeSessionManager()
        opened_flags: List[bool] = []

        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            if isinstance(payload, RoomMessagePayload):
                opened_flags.append(session_mgr.opened_pk is not None)

        event_bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU, _capture, model_class=RoomMessagePayload)

        date_str = "2026-09-02"
        await _insert_danmaku(sim_store, date_str, rows=1)

        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
            session_manager=session_mgr,
        )
        # 不配 replay_date → setup 自动启动被拒，显式 start 走本测试的顺序断言
        await service.setup(_FakeConfigService(_enabled_config(mode="replay", replay_speed=100.0)))
        try:
            assert service.is_running is False
            rejected = await service.start(replay_date=date_str)
            assert rejected is None, "日期与队列就绪时 start 不应拒绝"

            deadline = asyncio.get_event_loop().time() + 5.0
            while service.is_running and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)

            assert opened_flags == [True], "首条回放消息 emit 时场次必须已开启（否则消息丢场次）"
            assert "close" in session_mgr.calls, "自然收场必须关闭回放场次"
        finally:
            await service.cleanup()


class TestStartRejectionAndInstantFinish:
    """start() 拒绝原因返回 + 小队列"启动即完成"语义。"""

    @pytest.mark.asyncio
    async def test_start_without_date_returns_reason(self, sim_store: SQLiteDatabase) -> None:
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=EventBus(),
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(mode="replay")))
        try:
            rejected = await service.start()
            assert rejected is not None and "日期" in rejected
            assert service.is_running is False
        finally:
            await service.cleanup()

    @pytest.mark.asyncio
    async def test_start_empty_date_returns_reason(self, sim_store: SQLiteDatabase) -> None:
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=EventBus(),
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(mode="replay")))
        try:
            rejected = await service.start(replay_date="1999-01-01")
            assert rejected is not None and "无可回放" in rejected
            assert service.is_running is False
        finally:
            await service.cleanup()

    @pytest.mark.asyncio
    async def test_single_message_replay_finishes_immediately(self, sim_store: SQLiteDatabase) -> None:
        """1 条全速回放启动即完成：start 不拒绝、随后 is_running 复位（API 据此报"已完成"）。"""
        event_bus = EventBus()
        date_str = "2026-09-03"
        await _insert_danmaku(sim_store, date_str, rows=1)

        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(_FakeConfigService(_enabled_config(mode="replay")))
        try:
            rejected = await service.start(replay_date=date_str)
            assert rejected is None
            assert service.replay_engine is not None
            assert service.replay_engine.total == 1

            deadline = asyncio.get_event_loop().time() + 5.0
            while service.is_running and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)

            assert service.is_running is False, "启动即完成后不得挂运行态（否则 API 误报启动失败）"
            assert service.mode == "off"
        finally:
            await service.cleanup()


class TestGenerateFatalErrorShutdown:
    """generate 循环遇 LLM FatalError 的收场契约。"""

    @pytest.mark.asyncio
    async def test_fatal_llm_error_stops_loop_and_sets_last_error(self, sim_store: SQLiteDatabase) -> None:
        fatal_llm = _FatalLLMService()
        service = SimulatorService(
            event_bus=EventBus(),
            sim_repo=sim_store.sim,
            chat_repo=sim_store.chat,
            services_by_type={type(fatal_llm): fatal_llm},
        )
        await service.setup(
            _FakeConfigService(
                _enabled_config(
                    cadence_mode="fixed",
                    fixed_interval_s=1.0,
                    gift_probability=0.0,
                    warmup_duration_s=0.0,
                )
            )
        )
        try:
            deadline = asyncio.get_event_loop().time() + 8.0
            while service.is_running and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)

            assert service.is_running is False, "FatalError 后循环必须收场，不得无限重试"
            assert service.mode == "off"
            assert service.last_error is not None and "FatalError" in service.last_error
            assert fatal_llm.chat_calls == 1, "不可恢复错误只允许调用一次"
        finally:
            await service.cleanup()
