"""测试 DanmakuWidgetService 弹幕叠加与字幕公开方法

覆盖：

- ``show_subtitle`` 公开方法：构造 ``SubtitleWidgetMessage``、追加
  到 ``subtitle_messages``、通过 ``_subtitle_callback`` 广播
- ``show_subtitle`` 的 ``duration_ms`` 参数透传；``None`` 时回退到
  ``subtitle_config.auto_hide_after_ms``
- ``clear_subtitle`` 公开方法：清空 ``subtitle_messages``、广播清空
  消息（``text == ""`` 且 ``duration_ms == 0``）
- ``subtitle_config.enabled=False`` 时 ``show_subtitle`` /
  ``clear_subtitle`` 不广播（仅 ``clear_subtitle`` 内部清理 deque）
- ``start()`` 订阅面收敛在 ``room.message.*``（弹幕叠加是本服务唯一
  订阅职责，字幕显示由 ``SubtitleService`` 驱动）
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.modules.dashboard.widget.models import SubtitleWidgetConfig
from src.modules.dashboard.widget.service import DanmakuWidgetService
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents


def _make_service(
    subtitle_enabled: bool = True,
    auto_hide_after_ms: int = 5000,
) -> DanmakuWidgetService:
    bus = EventBus()
    return DanmakuWidgetService(
        event_bus=bus,
        subtitle_config=SubtitleWidgetConfig(
            enabled=subtitle_enabled,
            auto_hide_after_ms=auto_hide_after_ms,
        ),
    )


# ---------------------------------------------------------------------------
# show_subtitle：公开方法 + 队列追加 + callback 广播
# ---------------------------------------------------------------------------


class TestShowSubtitle:
    @pytest.mark.asyncio
    async def test_appends_to_subtitle_messages(self):
        """``show_subtitle`` 追加 ``SubtitleWidgetMessage`` 到内部队列。"""
        svc = _make_service()
        await svc.show_subtitle("hello")
        assert len(svc.subtitle_messages) == 1
        assert svc.subtitle_messages[0].text == "hello"

    @pytest.mark.asyncio
    async def test_default_duration_uses_config(self):
        """``duration_ms=None`` → 落 ``subtitle_config.auto_hide_after_ms``。"""
        svc = _make_service(auto_hide_after_ms=7000)
        await svc.show_subtitle("hi")
        assert svc.subtitle_messages[0].duration_ms == 7000

    @pytest.mark.asyncio
    async def test_explicit_duration_overrides_config(self):
        """``duration_ms`` 显式传入 → 覆盖 ``auto_hide_after_ms``。"""
        svc = _make_service(auto_hide_after_ms=7000)
        await svc.show_subtitle("hi", duration_ms=2500)
        assert svc.subtitle_messages[0].duration_ms == 2500

    @pytest.mark.asyncio
    async def test_subtitle_callback_receives_subtitle_payload(self):
        """``_subtitle_callback`` 收到 ``type='subtitle'`` data 字典。"""
        svc = _make_service()
        svc._subtitle_callback = AsyncMock()
        await svc.show_subtitle("hello")
        svc._subtitle_callback.assert_awaited_once()
        data = svc._subtitle_callback.await_args.args[0]
        assert data["type"] == "subtitle"
        assert data["text"] == "hello"
        assert data["duration_ms"] == 5000

    @pytest.mark.asyncio
    async def test_no_callbacks_does_not_raise(self):
        """未注册 callback 时 ``show_subtitle`` 不抛错（仅入队）。"""
        svc = _make_service()
        await svc.show_subtitle("hello")
        assert len(svc.subtitle_messages) == 1


# ---------------------------------------------------------------------------
# show_subtitle：subtitle_config.enabled gate
# ---------------------------------------------------------------------------


class TestShowSubtitleDisabled:
    @pytest.mark.asyncio
    async def test_disabled_does_not_append_or_broadcast(self):
        """``subtitle_config.enabled=False`` 时 ``show_subtitle`` 早退
        ——不追加消息、不广播。"""
        svc = _make_service(subtitle_enabled=False)
        svc._subtitle_callback = AsyncMock()
        await svc.show_subtitle("hello")
        assert len(svc.subtitle_messages) == 0
        svc._subtitle_callback.assert_not_awaited()


# ---------------------------------------------------------------------------
# clear_subtitle：公开方法 + 清空 + 广播清空信号
# ---------------------------------------------------------------------------


class TestClearSubtitle:
    @pytest.mark.asyncio
    async def test_clears_subtitle_messages(self):
        """``clear_subtitle`` 清空 ``subtitle_messages`` deque。"""
        svc = _make_service()
        await svc.show_subtitle("first")
        await svc.show_subtitle("second")
        assert len(svc.subtitle_messages) == 2

        await svc.clear_subtitle()
        assert len(svc.subtitle_messages) == 0

    @pytest.mark.asyncio
    async def test_broadcasts_empty_text_message(self):
        """``clear_subtitle`` 广播 ``text=''`` 且 ``duration_ms=0`` 的清空消息。"""
        svc = _make_service()
        svc._subtitle_callback = AsyncMock()

        await svc.clear_subtitle()

        subtitle_data = svc._subtitle_callback.await_args.args[0]
        assert subtitle_data["type"] == "subtitle"
        assert subtitle_data["text"] == ""
        assert subtitle_data["duration_ms"] == 0


# ---------------------------------------------------------------------------
# start() / stop()：订阅面收敛在 room.message.*
# ---------------------------------------------------------------------------


class TestSubscriptions:
    @pytest.mark.asyncio
    async def test_start_does_not_subscribe_other_domains(self):
        """``start()`` 订阅面收敛在 ``room.message.*``：业务决策/流程单域
        事件不进本服务（字幕显示由 ``SubtitleService`` 驱动）。"""
        svc = _make_service()
        await svc.start()

        for outsider in ("planner.decision", "streamer.speech", "rundown.changed"):
            assert len(svc.event_bus._handlers.get(outsider, [])) == 0

        await svc.stop()

    @pytest.mark.asyncio
    async def test_start_subscribes_room_message_danmaku(self):
        """``start()`` 仍订阅 ``room.message.danmaku``（弹幕订阅主路径）。"""
        svc = _make_service()
        await svc.start()

        handlers = svc.event_bus._handlers.get(CoreEvents.ROOM_MESSAGE_DANMAKU, [])
        assert len(handlers) >= 1

        await svc.stop()
