"""ToolCatalog 服务层：提供者开关地址解析与停用名单增删（HTTP 层之下的纯逻辑）"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.modules.dashboard.services.tool_catalog import (
    ProviderNotSwitchableError,
    SwitchAddressIncompleteError,
    UnknownProviderError,
    build_disabled_tools_change,
    build_provider_switch_change,
)


def _resolve(
    category: str, key: str, enable: bool, records: list[dict] | None = None, avatar_doc: Dict[str, Any] | None = None
) -> tuple[str, Any, str]:
    change = build_provider_switch_change(
        category,
        key,
        enable,
        records or [],
        lambda: avatar_doc if avatar_doc is not None else {"platform": {"enabled": ["vts"]}},
    )
    return change.api_key, change.value, change.subject


def test_switch_config_declaration_routes_to_declared_file_and_key() -> None:
    records = [{"name": "maicraft", "switch": {"file": "agents.toml", "key": "agents.minecraft.mcp.enabled"}}]
    api_key, value, subject = _resolve("mcp", "maicraft", False, records)
    assert (api_key, value) == ("agents.agents.minecraft.mcp.enabled", False)
    assert subject == "工具提供者 mcp.maicraft"


def test_incomplete_switch_declaration_raises() -> None:
    records = [{"name": "maicraft", "switch": {"file": "", "key": "agents.minecraft.mcp.enabled"}}]
    with pytest.raises(SwitchAddressIncompleteError):
        _resolve("mcp", "maicraft", True, records)


def test_agent_category_is_not_switchable() -> None:
    with pytest.raises(ProviderNotSwitchableError):
        _resolve("game", "text_adv", True)


def test_unknown_member_raises() -> None:
    with pytest.raises(UnknownProviderError):
        _resolve("studio", "ghost", True)


def test_mcp_server_writes_member_key() -> None:
    assert _resolve("mcp", "generic_mcp", False)[:2] == (
        "tools.tools.mcp.config.servers.generic_mcp.enabled",
        False,
    )


def test_avatar_platform_enable_appends_to_roster() -> None:
    api_key, value, subject = _resolve("avatar", "warudo", True, avatar_doc={"platform": {"enabled": ["vts"]}})
    assert (api_key, value, subject) == ("avatar.platform.enabled", ["vts", "warudo"], "虚拟形象平台 avatar.warudo")


def test_avatar_platform_disable_removes_from_roster() -> None:
    _, value, _ = _resolve("avatar", "vts", False, avatar_doc={"platform": {"enabled": ["vts", "warudo"]}})
    assert value == ["warudo"]


def test_category_level_and_member_level_keys() -> None:
    # 分类级开关（键与分类名相同）写分类级 enabled，不落嵌套成员段
    assert _resolve("vision", "vision", True)[:2] == ("tools.tools.vision.enabled", True)
    # 成员级写 <分类>.<键>.enabled
    assert _resolve("studio", "obs", False)[:2] == ("tools.tools.studio.obs.enabled", False)


def test_disabled_tools_list_add_remove_sorted_deduped() -> None:
    # 排除名单：enable=False 加入（停用），enable=True 移出（启用）
    doc: Dict[str, Any] = {"tools": {"disabled_tools": ["b_tool", "a_tool"]}}
    assert build_disabled_tools_change(doc, "c_tool", False) == ["a_tool", "b_tool", "c_tool"]
    assert build_disabled_tools_change(doc, "b_tool", True) == ["a_tool"]
    # 重复停用幂等；缺失段与非名单值按空名单起步
    assert build_disabled_tools_change(doc, "b_tool", False) == ["a_tool", "b_tool"]
    assert build_disabled_tools_change({}, "x_tool", False) == ["x_tool"]
    assert build_disabled_tools_change({"tools": {"disabled_tools": "bad"}}, "x_tool", False) == ["x_tool"]
