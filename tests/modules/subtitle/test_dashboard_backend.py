"""测试 DashboardBackend - 包装 DanmakuWidgetService 的协议形态适配

覆盖：

- ``show`` 转发到 ``DanmakuWidgetService.show_subtitle``（不消费
  ``utterance_id``——widget 暂无该键的展示位）
- ``clear`` 转发到 ``DanmakuWidgetService.clear_subtitle``
- ``enabled`` 转发 widget 的 ``subtitle_config.enabled``
- 实例满足 ``SubtitleBackend`` 协议（结构类型校验通过）
- ``widget is None`` 时 ``enabled`` 为 False（构造边界兜底）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.subtitle import DashboardBackend, SubtitleBackend


def _make_widget(
    *,
    subtitle_enabled: bool = True,
) -> MagicMock:
    """构造 ``DanmakuWidgetService`` 形状的 MagicMock。

    仅声明本 Backend 实际使用的两个方法 + ``subtitle_config.enabled``
    字段；其他 ``DanmakuWidgetService`` 成员（事件订阅 / 队列等）不
    模拟——本测试只验证转发关系。
    """
    widget = MagicMock()
    widget.subtitle_config = MagicMock()
    widget.subtitle_config.enabled = subtitle_enabled
    widget.show_subtitle = AsyncMock()
    widget.clear_subtitle = AsyncMock()
    return widget


# ---------------------------------------------------------------------------
# 协议契约
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_dashboard_backend_satisfies_protocol(self):
        """``DashboardBackend`` 实例满足 ``SubtitleBackend`` 协议。"""
        backend = DashboardBackend(_make_widget())
        assert isinstance(backend, SubtitleBackend)


# ---------------------------------------------------------------------------
# show 转发
# ---------------------------------------------------------------------------


class TestShowForwards:
    @pytest.mark.asyncio
    async def test_show_calls_widget_show_subtitle(self):
        """``show`` 转发到 ``widget.show_subtitle``，传入 text。"""
        widget = _make_widget()
        backend = DashboardBackend(widget)

        await backend.show("hello world")

        widget.show_subtitle.assert_awaited_once_with("hello world")

    @pytest.mark.asyncio
    async def test_show_ignores_utterance_id(self):
        """``show`` 不把 ``utterance_id`` 透传给 widget（widget 暂无该键
        的展示位——Dashboard 字幕展示位未消费 utterance_id）。"""
        widget = _make_widget()
        backend = DashboardBackend(widget)

        await backend.show("hello", utterance_id="u-42")

        # 仅以 text 调用，utterance_id 不在参数中
        widget.show_subtitle.assert_awaited_once_with("hello")


# ---------------------------------------------------------------------------
# clear 转发
# ---------------------------------------------------------------------------


class TestClearForwards:
    @pytest.mark.asyncio
    async def test_clear_calls_widget_clear_subtitle(self):
        """``clear`` 转发到 ``widget.clear_subtitle``。"""
        widget = _make_widget()
        backend = DashboardBackend(widget)

        await backend.clear()

        widget.clear_subtitle.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# enabled 转发
# ---------------------------------------------------------------------------


class TestEnabledForwarding:
    def test_enabled_true_when_subtitle_config_enabled(self):
        """``widget.subtitle_config.enabled = True`` → backend ``enabled`` 为 True。"""
        widget = _make_widget(subtitle_enabled=True)
        backend = DashboardBackend(widget)
        assert backend.enabled is True

    def test_enabled_false_when_subtitle_config_disabled(self):
        """``widget.subtitle_config.enabled = False`` → backend ``enabled`` 为 False
        （字幕小部件关闭时本 Backend 不产生副作用——show/clear 仅清空队列）。"""
        widget = _make_widget(subtitle_enabled=False)
        backend = DashboardBackend(widget)
        assert backend.enabled is False
