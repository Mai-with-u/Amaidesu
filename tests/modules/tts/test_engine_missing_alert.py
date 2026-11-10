"""TTS 引擎缺席告警：配置说开、引擎没造出来时发 core.error；有意关闭不报；同类告警 5 分钟窗内只发一条。"""

from __future__ import annotations

from src.modules.events.event_bus import EventBus
from src.modules.tts import build_tts_infrastructure
from src.modules.tts.backends.edge.provider import EdgeTTSProvider
from src.modules.tts.engine_missing_alert import ERROR_ALERT_THROTTLE_MS, TtsEngineMissingAlert
from tests.helpers import FakeClock, record_core_error


async def test_missing_engine_publishes_core_error(event_bus: EventBus) -> None:
    """配置开启、引擎没造出来：发一条 core.error，文案说清"配置说开但引擎没起来"。"""
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus)

    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="gptsovits")
    await event_bus.wait_until_drained()

    assert len(alerts) == 1
    message = alerts[0].message or ""
    assert "配置开启了语音" in message
    assert "引擎没有构造成功" in message
    assert "不会出声" in message


async def test_construction_failure_reaches_the_same_publish_point(event_bus: EventBus) -> None:
    """构造异常路径：装配函数吞掉异常返回 None，矛盾态判定据此发出告警。

    全模态引擎缺 LLM 服务时装配失败（返回 None），这是真实的构造异常路径；
    装配层把它降级成"没有引擎"，与"配置关闭"在返回值上无法区分，
    矛盾态判定必须靠配置开关单独判断。
    """
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus)

    config = {"enabled": True, "provider": "omni_tts", "omni_tts": {"voice": "Chelsie"}}
    engine = build_tts_infrastructure(config)

    assert engine is None
    await publisher.report_missing_engine(config_enabled=True, engine=engine, provider="omni_tts")
    await event_bus.wait_until_drained()

    assert len(alerts) == 1
    assert "引擎没有构造成功" in (alerts[0].message or "")


async def test_disabled_tts_publishes_nothing(event_bus: EventBus) -> None:
    """有意关闭语音（配置 enabled=false）：配置与实际一致，不是矛盾态，不发告警。"""
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus)

    await publisher.report_missing_engine(config_enabled=False, engine=None, provider="gptsovits")
    await event_bus.wait_until_drained()

    assert alerts == []


async def test_built_engine_publishes_nothing(event_bus: EventBus) -> None:
    """引擎构造成功：配置与实际一致，不发告警。"""
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus)

    engine = EdgeTTSProvider(config={})
    await publisher.report_missing_engine(config_enabled=True, engine=engine, provider="edge_tts")
    await event_bus.wait_until_drained()

    assert alerts == []


async def test_repeat_alert_throttled_within_window(event_bus: EventBus, fake_clock: FakeClock) -> None:
    """同类告警 5 分钟窗内只发第一条；拨过窗口后才发第二条。"""
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus, clock=fake_clock.now_ms)

    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="gptsovits")
    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="gptsovits")
    await event_bus.wait_until_drained()

    assert len(alerts) == 1

    fake_clock.tick(ERROR_ALERT_THROTTLE_MS)
    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="gptsovits")
    await event_bus.wait_until_drained()

    assert len(alerts) == 2


async def test_report_without_event_bus_does_not_raise() -> None:
    """没接事件总线时静默跳过（装配期旁路告警，不阻断启动）。"""
    publisher = TtsEngineMissingAlert(event_bus=None)

    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="edge_tts")


async def test_alert_payload_carries_provider(event_bus: EventBus) -> None:
    """告警载荷带上引擎名：定位是哪一路没起来。"""
    alerts = record_core_error(event_bus)
    publisher = TtsEngineMissingAlert(event_bus=event_bus)

    await publisher.report_missing_engine(config_enabled=True, engine=None, provider="minimax_tts")
    await event_bus.wait_until_drained()

    assert len(alerts) == 1
    assert alerts[0].data == {"source": "tts", "provider": "minimax_tts"}
