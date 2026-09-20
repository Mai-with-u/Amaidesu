"""WarudoProvider 动作目录与工具面测试（无需 Warudo 外部环境）"""

import pytest

from src.modules.avatar.warudo.warudo_provider import WarudoProvider


@pytest.fixture
def mock_event_bus():
    from unittest.mock import MagicMock

    return MagicMock()


def test_action_catalog_empty_by_default(mock_event_bus):
    provider = WarudoProvider({"ws_host": "localhost"}, event_bus=mock_event_bus)

    assert provider.action_catalog == {}
    # 目录仍含内置条目（抛鱼）
    names = [entry["name"] for entry in provider._preset_action_catalog()]
    assert names == ["throw_fish"]


def test_action_catalog_from_config(mock_event_bus):
    provider = WarudoProvider(
        {"ws_host": "localhost", "action_catalog": {"wave": "招手", "sit": "坐下"}},
        event_bus=mock_event_bus,
    )

    catalog = provider._preset_action_catalog()
    entries = {entry["name"]: entry["description"] for entry in catalog}
    assert entries["wave"] == "招手"
    assert entries["sit"] == "坐下"
    assert entries["throw_fish"]


def test_action_catalog_rejects_non_dict_config(mock_event_bus):
    """action_catalog 必须为 Dict[str, str]；非 dict 严格报错（schema 严格化）。"""
    with pytest.raises(Exception):
        WarudoProvider(
            {"ws_host": "localhost", "action_catalog": "invalid"},
            event_bus=mock_event_bus,
        )


def test_list_tools_final_face(mock_event_bus):
    """Warudo 工具面 = 约定四件（§7 定案）；描述不承载目录。"""
    provider = WarudoProvider(
        {"ws_host": "localhost", "action_catalog": {"wave": "招手"}},
        event_bus=mock_event_bus,
    )

    names = {s.full_name for s in provider.list_tools()}
    assert names == {
        "warudo_set_expression",
        "warudo_list_preset_actions",
        "warudo_trigger_preset_action",
        "warudo_set_sight",
    }
    expression = next(s for s in provider.list_tools() if s.full_name == "warudo_set_expression")
    assert "招手" not in expression.description


def test_list_tools_provider_identifier(mock_event_bus):
    provider = WarudoProvider({"ws_host": "localhost"}, event_bus=mock_event_bus)

    for spec in provider.list_tools():
        assert spec.provider == "warudo"
