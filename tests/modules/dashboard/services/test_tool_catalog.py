"""tool_catalog 服务层边界单测。

覆盖提供者分类目录的三路拼装（静态元数据 ∪ 注册表运行态 ∪ 配置声明态）
与 list_providers 兼容层兜底。
"""

from __future__ import annotations

from typing import Any

from src.modules.dashboard.services.tool_catalog import (
    build_tool_catalog,
    runtime_tool_counts,
    safe_list_providers,
)


class _FakeRegistry:
    """注册表桩：list_tools / list_providers 返回预置数据。"""

    def __init__(
        self,
        tools: list[Any] | None = None,
        providers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._tools = tools or []
        self._providers = providers or []

    def list_tools(self, include_disabled: bool = True, include_tripped: bool = True) -> list[Any]:
        return self._tools

    def list_providers(self) -> list[dict[str, Any]]:
        return self._providers

    def category_of(self, name: str) -> str:
        return "framework"

    def is_disabled(self, name: str) -> bool:
        return False


class _BrokenRegistry:
    """list_tools / list_providers 均抛异常的最小注册表。"""

    def list_tools(self, include_disabled: bool = True, include_tripped: bool = True) -> list[Any]:
        raise RuntimeError("boom")

    def list_providers(self) -> list[dict[str, Any]]:
        raise RuntimeError("boom")


_EMPTY_TOOLS_CFG: dict[str, Any] = {}


def _catalog(registry: Any, tools_cfg: dict[str, Any], agents_enabled: list[str] | None = None) -> dict[str, Any]:
    main_config: dict[str, Any] = {"agents": {"enabled": agents_enabled or []}}
    return build_tool_catalog(registry, tools_cfg, main_config)


def _providers_by_category(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["key"]: p for cat in catalog["categories"] for p in cat["providers"]}


# ---------------------------------------------------------------------------
# safe_list_providers / runtime_tool_counts 兜底
# ---------------------------------------------------------------------------


class TestSafeListProviders:
    """兼容层兜底：未实现 / 抛异常 / 非 dict 记录过滤。"""

    def test_未实现list_providers返回空列表(self) -> None:
        assert safe_list_providers(object()) == []

    def test_调用抛异常兜底返回空列表(self) -> None:
        assert safe_list_providers(_BrokenRegistry()) == []

    def test_正常记录透传且过滤非dict(self) -> None:
        registry = _FakeRegistry(providers=[{"name": "vts", "category": "avatar"}, "junk", 42])
        result = safe_list_providers(registry)
        assert result == [{"name": "vts", "category": "avatar"}]


class TestRuntimeToolCounts:
    """工具计数：异常兜底按 0 工具处理。"""

    def test_注册表异常时返回空计数(self) -> None:
        assert runtime_tool_counts(_BrokenRegistry()) == {}

    def test_空工具表返回空计数(self) -> None:
        assert runtime_tool_counts(_FakeRegistry()) == {}


# ---------------------------------------------------------------------------
# build_tool_catalog 三路拼装
# ---------------------------------------------------------------------------


class TestBuildToolCatalog:
    """注册表运行态、配置声明态、静态元数据表三路贡献与合并。"""

    def test_注册表记录建卡_运行态字段透传(self) -> None:
        registry = _FakeRegistry(
            providers=[
                {
                    "name": "vts",
                    "category": "avatar",
                    "tool_count": 3,
                    "disabled_count": 1,
                    "supports_reconnect": True,
                    "last_error": "",
                }
            ]
        )
        catalog = _catalog(registry, {"avatar": {"vts": {"enabled": True}}})
        card = _providers_by_category(catalog)["vts"]
        assert card["registered"] is True
        assert card["degraded"] is False
        assert card["tool_count"] == 3
        assert card["disabled_count"] == 1
        assert card["supports_reconnect"] is True
        # 配置态：成员段存在 → in_config + enabled 跟随配置
        assert card["in_config"] is True
        assert card["enabled"] is True
        assert card["switchable"] is True

    def test_零工具注册表提供者标记degraded(self) -> None:
        registry = _FakeRegistry(providers=[{"name": "vrchat", "category": "avatar", "tool_count": 0}])
        card = _providers_by_category(_catalog(registry, {}))["vrchat"]
        assert card["registered"] is True
        assert card["degraded"] is True

    def test_配置声明态补卡_enabled_false也展示(self) -> None:
        registry = _FakeRegistry()
        tools_cfg = {"avatar": {"warudo": {"enabled": False}}}
        card = _providers_by_category(_catalog(registry, tools_cfg))["warudo"]
        assert card["registered"] is False
        assert card["degraded"] is False
        assert card["enabled"] is False
        assert card["in_config"] is True
        # 静态元数据表补描述
        assert card["description"] == "Warudo 控制"

    def test_mcp配置server补卡且enabled跟随配置(self) -> None:
        registry = _FakeRegistry()
        tools_cfg = {"mcp": {"config": {"servers": {"my-mcp": {"enabled": False}}}}}
        card = _providers_by_category(_catalog(registry, tools_cfg))["my-mcp"]
        assert card["registered"] is False
        assert card["enabled"] is False
        assert card["in_config"] is True
        assert card["description"] == "MCP server"

    def test_静态元数据表为未声明提供者补卡(self) -> None:
        # 未在注册表、未在配置中声明的已知提供者保持可见可开启
        registry = _FakeRegistry()
        card = _providers_by_category(_catalog(registry, {}))["vts"]
        assert card["registered"] is False
        assert card["in_config"] is False
        assert card["enabled"] is False
        assert card["description"] == "VTubeStudio 控制"

    def test_game分类随非主播Agent启用(self) -> None:
        registry = _FakeRegistry(providers=[{"name": "text_adv", "category": "game", "tool_count": 2}])
        off = _providers_by_category(_catalog(registry, {}, agents_enabled=["streamer"]))["text_adv"]
        on = _providers_by_category(_catalog(registry, {}, agents_enabled=["streamer", "text_adv"]))["text_adv"]
        assert off["enabled"] is False
        assert on["enabled"] is True
        # 随 Agent 分类不可开关
        assert on["switchable"] is False
        assert on["in_config"] is True

    def test_分类级开关成员读取enabled段(self) -> None:
        registry = _FakeRegistry()
        tools_cfg = {"vision": {"enabled": True}}
        card = _providers_by_category(_catalog(registry, tools_cfg))["vision"]
        assert card["enabled"] is True
        assert card["in_config"] is True

    def test_固定词表空分类保持输出_词表外按名追加(self) -> None:
        registry = _FakeRegistry(providers=[{"name": "zz", "category": "custom", "tool_count": 1}])
        catalog = _catalog(registry, {})
        categories = [c["category"] for c in catalog["categories"]]
        # 固定词表全量在前（保序），词表外按名排序追加在后
        assert categories[:7] == ["avatar", "studio", "vision", "memory", "mcp", "game", "framework"]
        assert categories[7:] == sorted(categories[7:])
        assert "custom" in categories
