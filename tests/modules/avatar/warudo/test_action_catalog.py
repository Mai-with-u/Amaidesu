"""WarudoProvider 动作目录与工具描述测试（无需 Warudo 外部环境）"""

import pytest

from src.modules.avatar.warudo.warudo_provider import WarudoProvider


@pytest.fixture
def mock_event_bus():
    from unittest.mock import MagicMock

    return MagicMock()


def test_action_catalog_empty_by_default(mock_event_bus):
    provider = WarudoProvider({"ws_host": "localhost"}, event_bus=mock_event_bus)

    assert provider.action_catalog == {}
    assert provider._action_catalog_summary() == ""


def test_action_catalog_from_config(mock_event_bus):
    provider = WarudoProvider(
        {"ws_host": "localhost", "action_catalog": {"wave": "招手", "sit": "坐下"}},
        event_bus=mock_event_bus,
    )

    summary = provider._action_catalog_summary()
    assert "wave（招手）" in summary
    assert "sit（坐下）" in summary


def test_action_catalog_ignores_non_dict_config(mock_event_bus):
    provider = WarudoProvider(
        {"ws_host": "localhost", "action_catalog": "invalid"},
        event_bus=mock_event_bus,
    )

    assert provider.action_catalog == {}


def test_list_tools_description_carries_catalog(mock_event_bus):
    provider = WarudoProvider(
        {"ws_host": "localhost", "action_catalog": {"wave": "招手"}},
        event_bus=mock_event_bus,
    )

    for name in ("warudo_trigger_hotkey", "warudo_trigger_body", "warudo_trigger_head", "warudo_trigger_action"):
        spec = next(s for s in provider.list_tools() if s.name == name)
        assert "wave（招手）" in spec.description


def test_list_tools_provider_identifier(mock_event_bus):
    provider = WarudoProvider({"ws_host": "localhost"}, event_bus=mock_event_bus)

    for spec in provider.list_tools():
        assert spec.provider == "warudo"
