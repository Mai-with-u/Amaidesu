"""测试 TkGuiBackend - 包装 SubtitleGuiService 的协议形态适配

覆盖：

- ``show`` 转发到 ``SubtitleGuiService.push_subtitle``（不消费
  ``utterance_id``——GUI 服务暂未提供该键的展示位）
- ``clear`` 转发到 ``SubtitleGuiService._clear_content``（既有私有
  API 调用约定）
- ``enabled`` 转发 ``SubtitleGuiService.enabled``（CustomTkinter
  不可用时为 False）
- 实例满足 ``SubtitleBackend`` 协议（结构类型校验通过）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.modules.subtitle import SubtitleBackend, TkGuiBackend


@pytest.fixture
def mock_service() -> MagicMock:
    """构造一个 ``SubtitleGuiService`` 形状的 MagicMock。

    仅声明本 Backend 实际使用的三个成员（``enabled`` 属属性 /
    ``push_subtitle`` / ``_clear_content``）；其他 ``SubtitleGuiService``
    成员（Tk 线程、队列等）不模拟——本测试只验证转发关系。
    """
    svc = MagicMock()
    svc.enabled = True
    return svc


class TestProtocolConformance:
    def test_tk_gui_backend_satisfies_protocol(self, mock_service):
        """``TkGuiBackend`` 实例满足 ``SubtitleBackend`` 协议。"""
        backend = TkGuiBackend(mock_service)
        assert isinstance(backend, SubtitleBackend)


class TestShowForwards:
    async def test_show_calls_push_subtitle(self, mock_service):
        """``show`` 转发到 ``push_subtitle``，传入 text。"""
        backend = TkGuiBackend(mock_service)

        await backend.show("hello world")

        mock_service.push_subtitle.assert_called_once_with("hello world")

    async def test_show_ignores_utterance_id(self, mock_service):
        """``show`` 不把 ``utterance_id`` 透传给 GUI 服务（暂未消费）。"""
        backend = TkGuiBackend(mock_service)

        await backend.show("hello", utterance_id="u-42")

        # 仅以 text 调用，utterance_id 不在参数中
        mock_service.push_subtitle.assert_called_once_with("hello")


class TestClearForwards:
    async def test_clear_calls_private_clear_content(self, mock_service):
        """``clear`` 转发到 ``_clear_content``（既有私有 API）。"""
        backend = TkGuiBackend(mock_service)

        await backend.clear()

        mock_service._clear_content.assert_called_once_with()


class TestEnabledForwarding:
    def test_enabled_true_when_service_enabled(self, mock_service):
        """``service.enabled = True`` → backend ``enabled`` 为 True。"""
        mock_service.enabled = True
        backend = TkGuiBackend(mock_service)
        assert backend.enabled is True

    def test_enabled_false_when_service_disabled(self, mock_service):
        """``service.enabled = False`` → backend ``enabled`` 为 False
        （CustomTkinter 不可用时由 GUI 服务自动降级到此状态）。"""
        mock_service.enabled = False
        backend = TkGuiBackend(mock_service)
        assert backend.enabled is False
