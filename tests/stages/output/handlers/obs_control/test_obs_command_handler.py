"""
ObsControlHandler OBS 命令事件 handler 签名回归测试。

P0-1 修复回归:验证 _handle_obs_command_event 签名与 EventBus typed_wrapper 契约一致。
触发 OUTPUT_OBS_COMMAND 事件时 handler 应被以 (event_name, payload, source) 三参数调用,
而非只收 payload(否则会 TypeError,业务逻辑永不执行)。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.output import OBSCommandPayload
from src.stages.output.handlers.obs_control import obs_control_handler as obs_mod


def _make_obs_handler(monkeypatch: pytest.MonkeyPatch) -> obs_mod.ObsControlHandler:
    """构造一个最小可用的 ObsControlHandler,绕过 obsws-python 依赖。"""
    monkeypatch.setattr(obs_mod, "obs", MagicMock())
    return obs_mod.ObsControlHandler(
        config={
            "type": "obs_control",
            "host": "127.0.0.1",
            "port": 4455,
            "password": None,
            "text_source_name": "text",
            "typewriter_enabled": False,
            "typewriter_speed": 0.1,
            "typewriter_delay": 0.5,
            "test_on_connect": False,
        },
        event_bus=MagicMock(spec=EventBus),
    )


@pytest.mark.asyncio
async def test_handle_obs_command_event_accepts_three_args(monkeypatch):
    """回归: handler 签名必须接 3 参数,触发时不抛 TypeError。"""
    handler = _make_obs_handler(monkeypatch)

    handler._send_text_to_obs = AsyncMock()
    handler.switch_scene = AsyncMock()
    handler.set_source_visibility = AsyncMock()

    payload = OBSCommandPayload(action="send_text", text="hello", timestamp_ms=1)

    # 关键断言:用三参数直接调用,这是 EventBus typed_wrapper 的契约
    await handler._handle_obs_command_event(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=payload,
        source="dashboard.api",
    )

    handler._send_text_to_obs.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_obs_command_event_routes_actions(monkeypatch):
    """验证 action 字段正确路由到对应处理函数。"""
    handler = _make_obs_handler(monkeypatch)
    handler._send_text_to_obs = AsyncMock()
    handler.switch_scene = AsyncMock()
    handler.set_source_visibility = AsyncMock()

    await handler._handle_obs_command_event(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=OBSCommandPayload(action="send_text", text="hi", timestamp_ms=1),
        source="test",
    )
    handler._send_text_to_obs.assert_awaited_once_with("hi", None)

    await handler._handle_obs_command_event(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=OBSCommandPayload(action="switch_scene", scene_name="main", timestamp_ms=2),
        source="test",
    )
    handler.switch_scene.assert_awaited_once_with("main")

    await handler._handle_obs_command_event(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=OBSCommandPayload(
            action="set_source_visibility",
            source_name="cam",
            visibility=True,
            timestamp_ms=3,
        ),
        source="test",
    )
    handler.set_source_visibility.assert_awaited_once_with("cam", True)


@pytest.mark.asyncio
async def test_handle_obs_command_event_via_event_bus(monkeypatch):
    """端到端: 通过真实 EventBus 触发,验证 typed_wrapper 链路工作。"""
    monkeypatch.setattr(obs_mod, "obs", MagicMock())

    handler = obs_mod.ObsControlHandler(
        config={"type": "obs_control"},
        event_bus=MagicMock(spec=EventBus),
    )
    handler._send_text_to_obs = AsyncMock()

    bus = EventBus()
    bus.on(
        CoreEvents.OUTPUT_OBS_COMMAND,
        handler._handle_obs_command_event,
        model_class=OBSCommandPayload,
    )

    payload = OBSCommandPayload(action="send_text", text="via_bus", timestamp_ms=100)
    await bus.emit(
        CoreEvents.OUTPUT_OBS_COMMAND,
        payload,
        source="integration_test",
        wait=True,
    )

    handler._send_text_to_obs.assert_awaited_once_with("via_bus", None)


@pytest.mark.asyncio
async def test_cleanup_off_signature(monkeypatch):
    """验证 cleanup() 中 event_bus.off() 调用不再误传第 3 个参数。"""
    monkeypatch.setattr(obs_mod, "obs", MagicMock())

    bus_mock = MagicMock(spec=EventBus)
    handler = obs_mod.ObsControlHandler(
        config={"type": "obs_control"},
        event_bus=bus_mock,
    )

    await handler.cleanup()

    bus_mock.off.assert_called_once_with(
        CoreEvents.OUTPUT_OBS_COMMAND,
        handler._handle_obs_command_event,
    )