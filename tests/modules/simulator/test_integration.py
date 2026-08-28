"""SimulatorService 集成测试（ADR-006）

测试目标：验证 SimulatorService 作为"开发基础设施"的真实行为契约：

① 构造 + setup 读配置（enabled=false 不启动，零 LLM 调用）
② enabled=true + mock EventBus 下启动，emit room.message.danmaku 且 payload.simulated=True
③ stop/cleanup 幂等（重复调用不抛异常，资源正确清理）
④ CancelledError 传播语义（stop() 期间外层 cancel 必须透传，不被吞成正常返回）

LLM 注入：service.py:_find_llm_service 用 duck-type 检测 services_by_type
（需 chat / chat_fast / setup 三属性），测试用 MagicMock 模拟。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.simulator import SimulatorService


# ---------------------------------------------------------------------------
# 测试夹具：fake LLMManager（duck-type 注入 services_by_type）
# ---------------------------------------------------------------------------


class _FakeLLMService:
    """满足 SimulatorService._find_llm_service duck-type 检测的假 LLM 服务。"""

    def __init__(self, reply: str = "模拟观众弹幕", tokens: int = 10) -> None:
        self._reply = reply
        self._tokens = tokens
        self.chat_calls: int = 0

    async def chat(self, prompt: str, **kwargs: Any) -> Any:
        self.chat_calls += 1
        from src.modules.llm.manager import LLMResponse

        return LLMResponse(
            success=True,
            content=self._reply,
            usage={"total_tokens": self._tokens},
            error=None,
        )

    async def chat_fast(self, prompt: str, **kwargs: Any) -> Any:
        return await self.chat(prompt, **kwargs)

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
async def test_enabled_emits_danmaku_with_simulated_flag() -> None:
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
    async def test_full_lifecycle_cleanup(self) -> None:
        """start → stop → cleanup → 再次 cleanup 全流程幂等。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(
            _FakeConfigService(
                _enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)
            )
        )
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
    async def test_stop_propagates_cancellation(self) -> None:
        """在 stop() 内部模拟外层取消：CancelledError 必须传播到调用方。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(
            _FakeConfigService(
                _enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)
            )
        )
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
    async def test_run_loop_re_raises_cancelled(self) -> None:
        """主循环 _run() 捕获 CancelledError 后必须重新 raise，保持任务取消语义。"""
        event_bus = EventBus()
        fake_llm = _FakeLLMService()
        service = SimulatorService(
            event_bus=event_bus,
            services_by_type={type(fake_llm): fake_llm},
        )
        await service.setup(
            _FakeConfigService(
                _enabled_config(cadence_mode="fixed", fixed_interval_s=1.0)
            )
        )
        # 主动 cancel task
        assert service._task is not None
        service._task.cancel()
        # 等待 task 终止，CancelledError 必须传播
        with pytest.raises(asyncio.CancelledError):
            await service._task
        await service.cleanup()