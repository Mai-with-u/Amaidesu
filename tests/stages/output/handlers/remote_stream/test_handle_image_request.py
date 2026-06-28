"""
RemoteStreamHandler _handle_image_request 签名回归测试。

P0-1 修复回归:验证 handler 签名与 EventBus typed_wrapper 契约一致。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.output import OBSCommandPayload
from src.stages.output.handlers.remote_stream import (
    remote_stream_handler as rs_mod,
)


def _make_remote_handler(
    monkeypatch: pytest.MonkeyPatch,
    server_mode: bool = False,
) -> rs_mod.RemoteStreamHandler:
    """构造一个最小可用的 RemoteStreamHandler。"""
    fake_websockets = MagicMock()
    monkeypatch.setattr(rs_mod, "websockets", fake_websockets)

    handler = rs_mod.RemoteStreamHandler(
        config={
            "type": "remote_stream",
            "server_mode": server_mode,
            "host": "127.0.0.1",
            "port": 8765,
            "audio_sample_rate": 16000,
            "audio_channels": 1,
            "audio_format": "s16le",
            "audio_chunk_size": 4096,
            "image_width": 640,
            "image_height": 480,
            "image_format": "jpeg",
            "image_quality": 80,
            "max_reconnect_attempts": 3,
            "reconnect_delay": 1.0,
        },
        event_bus=MagicMock(spec=EventBus),
    )
    return handler


@pytest.mark.asyncio
async def test_handle_image_request_accepts_three_args(monkeypatch):
    """回归: handler 签名必须接 3 参数,触发时不抛 TypeError。"""
    handler = _make_remote_handler(monkeypatch)
    handler.request_image = AsyncMock()

    payload = OBSCommandPayload(action="request_image", timestamp_ms=1)
    await handler._handle_image_request(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=payload,
        source="dashboard.api",
    )

    handler.request_image.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_image_request_ignores_other_actions(monkeypatch):
    """非 request_image 的 action 应直接忽略。"""
    handler = _make_remote_handler(monkeypatch)
    handler.request_image = AsyncMock()

    payload = OBSCommandPayload(action="send_text", text="x", timestamp_ms=1)
    await handler._handle_image_request(
        event_name=CoreEvents.OUTPUT_OBS_COMMAND,
        payload=payload,
        source="test",
    )

    handler.request_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_image_request_via_event_bus(monkeypatch):
    """端到端: 通过真实 EventBus 触发。"""
    handler = _make_remote_handler(monkeypatch)
    handler.request_image = AsyncMock()

    bus = EventBus()
    bus.on(
        CoreEvents.OUTPUT_OBS_COMMAND,
        handler._handle_image_request,
        model_class=OBSCommandPayload,
    )

    payload = OBSCommandPayload(action="request_image", timestamp_ms=100)
    await bus.emit(
        CoreEvents.OUTPUT_OBS_COMMAND,
        payload,
        source="integration_test",
        wait=True,
    )

    handler.request_image.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_off_signature_no_third_arg(monkeypatch):
    """回归: cleanup() 中 off() 调用不再误传第 3 个参数(OBSCommandPayload)。"""
    handler = _make_remote_handler(monkeypatch)

    bus_mock = MagicMock(spec=EventBus)
    handler.event_bus = bus_mock
    handler.server = None
    handler.active_connection = None

    await handler.cleanup()

    bus_mock.off.assert_called_once_with(
        CoreEvents.OUTPUT_OBS_COMMAND,
        handler._handle_image_request,
    )