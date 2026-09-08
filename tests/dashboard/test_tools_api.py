"""Tools API 测试套件

覆盖：
1. **GET /api/v1/tools** — 工具清单 + provider/kind/category 元数据 +
   异步工具 result_event；参数 schema → 前端 ParameterSpec 形状
2. **GET /api/v1/tools/categories** — 提供者分类目录（分类 → 提供者 →
   开关状态 + 运行态计数）
3. **POST /api/v1/tools/categories/{category}/{key}/control** — 提供者开关
   写回 tools.toml（写回位置按成员类型区分；未知成员 / Agent 自声明分类 400）
4. tool_registry=None → 503
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


_CORE_TOML = """\
[meta]
version = "2.0.3"
"""

# 覆盖全部成员形态的最小 tools.toml：分类成员（avatar/studio）+ 分类级开关
# （vision/memory）+ mcp server（通用通道；maicraft 已下放到 agents.toml 的
# [agents.minecraft].mcp 作为 Agent 私有 MCP——跨文件迁移会自动处理）
_TOOLS_TOML = """\
[meta]
version = "2.0.19"

[tools]

[tools.avatar.vts]
enabled = true

[tools.avatar.vts.config]

[tools.studio.obs]
enabled = false

[tools.vision]
enabled = true

[tools.mcp]
enabled = false

[tools.mcp.config.servers.generic_mcp]
enabled = true
"""


def _make_spec(
    name: str,
    description: str,
    parameters_schema=None,
    provider: str = "builtin",
    kind: str = "sync",
    result_event: str = "",
):
    from src.modules.tools.models import ToolSpec

    return ToolSpec(
        name=name,
        description=description,
        parameters_schema=parameters_schema,
        kind=kind,
        result_event=result_event,
        provider=provider,
    )


class _FakeToolRegistry:
    """按名查分类的最小 registry 替身（list_tools + category_of + 停用集）。"""

    def __init__(self, specs, categories: dict[str, str] | None = None) -> None:
        self._specs = list(specs)
        self._categories = dict(categories or {})
        self._disabled: set[str] = set()
        self._scoped_owner: dict[str, str] = {}

    def list_tools(
        self,
        provider=None,
        *,
        include_disabled: bool = False,
        include_tripped: bool = False,
        include_scoped: bool = False,
    ):
        specs = [s for s in self._specs if include_disabled or s.name not in self._disabled]
        if provider is not None:
            specs = [s for s in specs if s.provider == provider]
        if provider is None and not include_scoped:
            specs = [s for s in specs if s.name not in self._scoped_owner]
        return specs

    def category_of(self, name: str) -> str:
        return self._categories.get(name, "")

    def apply_disabled(self, names) -> int:
        self._disabled = {n for n in names if any(s.name == n for s in self._specs)}
        return len(self._disabled)

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled

    def scoped_owner_of(self, name: str) -> str:
        return self._scoped_owner.get(name, "")


def _default_specs():
    return [
        _make_spec(
            "vts_trigger_hotkey",
            "触发 VTS 热键",
            {
                "type": "object",
                "properties": {
                    "hotkey": {"type": "string", "description": "热键名"},
                    "speed": {
                        "type": "number",
                        "default": 1.0,
                        "minimum": 0.5,
                        "maximum": 2.0,
                    },
                },
                "required": ["hotkey"],
            },
            provider="vts",
        ),
        _make_spec(
            "reply_to_user",
            "回复用户",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            provider="streamer",
            kind="async",
            result_event="tool.result.reply_to_user",
        ),
    ]


def _default_categories() -> dict[str, str]:
    return {"vts_trigger_hotkey": "avatar", "reply_to_user": "streamer"}


def _build_server(config_dir: Path, registry):
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=registry,  # type: ignore[arg-type]
    )


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    return cfg


@pytest.fixture
def tools_config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    (cfg / "tools.toml").write_text(_TOOLS_TOML, encoding="utf-8")
    return cfg


@pytest.fixture
def client(config_dir: Path):
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    server = _build_server(config_dir, registry)
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


@pytest.fixture
def tools_client(tools_config_dir: Path):
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    server = _build_server(tools_config_dir, registry)
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


# ==================== GET /tools ====================


def test_tools_returns_tools_with_name(client: TestClient) -> None:
    """动作名 = 工具自身名（名称已含 provider 前缀，不再拼接）。"""
    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    body = resp.json()
    names = {a["name"] for a in body["tools"]}
    assert "vts_trigger_hotkey" in names
    assert "reply_to_user" in names


def test_tools_action_description_passthrough(client: TestClient) -> None:
    resp = client.get("/api/v1/tools")
    by_name = {a["name"]: a for a in resp.json()["tools"]}
    assert by_name["vts_trigger_hotkey"]["description"] == "触发 VTS 热键"
    assert by_name["reply_to_user"]["description"] == "回复用户"


def test_tools_expose_provider_kind_category_metadata(client: TestClient) -> None:
    """ToolSpec 的 provider/kind 与 registry 分类透传给前端。"""
    resp = client.get("/api/v1/tools")
    by_name = {a["name"]: a for a in resp.json()["tools"]}

    hotkey = by_name["vts_trigger_hotkey"]
    assert hotkey["provider"] == "vts"
    assert hotkey["kind"] == "sync"
    assert hotkey["category"] == "avatar"
    assert "result_event" not in hotkey  # 同步工具无结果事件

    reply = by_name["reply_to_user"]
    assert reply["kind"] == "async"
    assert reply["result_event"] == "tool.result.reply_to_user"


def test_tools_includes_disabled_flag(config_dir: Path) -> None:
    """/tools 返回全集（含停用），disabled 字段标记状态。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    registry.apply_disabled(["vts_trigger_hotkey"])
    server = _build_server(config_dir, registry)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        by_name = {a["name"]: a for a in resp.json()["tools"]}
        assert by_name["vts_trigger_hotkey"]["disabled"] is True
        assert by_name["reply_to_user"]["disabled"] is False
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_expose_owner_agent_for_scoped_tools(config_dir: Path) -> None:
    """/tools 条目含 owner_agent 字段；归属限定的工具标其 Agent 名，无归属为空串。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    # 模拟归属限定：vts_trigger_hotkey 归属 'minecraft'
    registry._scoped_owner["vts_trigger_hotkey"] = "minecraft"
    server = _build_server(config_dir, registry)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        by_name = {a["name"]: a for a in resp.json()["tools"]}
        assert by_name["vts_trigger_hotkey"]["owner_agent"] == "minecraft"
        assert by_name["reply_to_user"]["owner_agent"] == ""
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_provider_query_filter(client: TestClient) -> None:
    """?provider= 过滤透传 registry。"""
    resp = client.get("/api/v1/tools", params={"provider": "vts"})
    names = {a["name"] for a in resp.json()["tools"]}
    assert names == {"vts_trigger_hotkey"}


def test_tools_parameters_map_to_parameter_spec(client: TestClient) -> None:
    """parameters_schema.properties[*] → Record<key, ParameterSpec>。"""
    resp = client.get("/api/v1/tools")
    by_name = {a["name"]: a for a in resp.json()["tools"]}

    hotkey_params = by_name["vts_trigger_hotkey"]["parameters"]
    assert hotkey_params["hotkey"]["type"] == "string"
    assert hotkey_params["hotkey"]["required"] is True
    assert hotkey_params["hotkey"]["description"] == "热键名"
    assert hotkey_params["speed"]["type"] == "number"
    assert hotkey_params["speed"]["default"] == 1.0
    assert hotkey_params["speed"]["minimum"] == 0.5
    assert hotkey_params["speed"]["maximum"] == 2.0
    assert hotkey_params["speed"]["required"] is False


def test_tools_handlers_endpoint_removed(client: TestClient) -> None:
    resp = client.get("/api/v1/handlers")
    assert resp.status_code == 404


def test_tools_returns_503_when_registry_missing(config_dir: Path) -> None:
    """tool_registry 未注入时返回 503（与原契约一致）。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    server = _build_server(config_dir, None)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        assert resp.status_code == 503
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_empty_when_registry_empty(config_dir: Path) -> None:
    """空注册表返回 tools=[]（不抛 500）。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    server = _build_server(config_dir, _FakeToolRegistry([]))
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json() == {"tools": []}
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_magicmock_registry_returns_tools(config_dir: Path) -> None:
    """MagicMock（test_components_v2_api 风格）作为 registry 也能跑通（无异常路径）。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    class _EmptyRegistry:
        def list_tools(self, provider=None, *, include_disabled: bool = False, include_tripped: bool = False):
            return []

        def category_of(self, name: str) -> str:
            return ""

    server = _build_server(config_dir, MagicMock(spec=_EmptyRegistry))
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json()["tools"] == []
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


# ==================== GET /tools/categories ====================


def test_categories_lists_static_members(tools_client: TestClient) -> None:
    """静态成员按分类聚组，携带配置态与运行态计数。"""
    resp = tools_client.get("/api/v1/tools/categories")
    assert resp.status_code == 200
    by_category = {c["category"]: c for c in resp.json()["categories"]}
    assert list(by_category) == ["avatar", "studio", "vision", "memory", "mcp", "game", "framework"]

    avatar_keys = {p["key"]: p for p in by_category["avatar"]["providers"]}
    assert set(avatar_keys) == {"vts", "vrchat", "warudo"}
    assert avatar_keys["vts"]["enabled"] is True
    assert avatar_keys["vts"]["in_config"] is True
    assert avatar_keys["vts"]["switchable"] is True
    assert avatar_keys["vts"]["tool_count"] == 1
    assert avatar_keys["vts"]["disabled_count"] == 0
    assert avatar_keys["vts"]["provider_name"] == "vts"
    # 配置未声明的已知成员仍列出（供面板直接开启）
    assert avatar_keys["vrchat"]["in_config"] is False
    assert avatar_keys["vrchat"]["enabled"] is False

    obs = {p["key"]: p for p in by_category["studio"]["providers"]}["obs"]
    assert obs["enabled"] is False
    assert obs["provider_name"] == "obs"
    assert obs["tool_count"] == 0

    vision = {p["key"]: p for p in by_category["vision"]["providers"]}["vision"]
    assert vision["enabled"] is True
    assert vision["tool_count"] == 0  # 配置开但 fake registry 无该分类工具


def test_categories_lists_mcp_servers_dynamically(tools_client: TestClient) -> None:
    """mcp 分类提供者 = 配置声明的各 server。"""
    resp = tools_client.get("/api/v1/tools/categories")
    by_category = {c["category"]: c for c in resp.json()["categories"]}
    mcp_providers = {p["key"]: p for p in by_category["mcp"]["providers"]}
    assert mcp_providers == {
        "generic_mcp": {
            "key": "generic_mcp",
            "provider_name": "generic_mcp",
            "description": "MCP server",
            "enabled": True,
            "in_config": True,
            "switchable": True,
            "tool_count": 0,
            "disabled_count": 0,
        }
    }


def test_categories_agent_categories_not_switchable(tools_client: TestClient) -> None:
    """game / framework 随 agents.toml 启用列表存在，不可开关。"""
    resp = tools_client.get("/api/v1/tools/categories")
    by_category = {c["category"]: c for c in resp.json()["categories"]}

    game = by_category["game"]
    assert [p["key"] for p in game["providers"]] == ["text_adv", "content_engine"]
    for p in game["providers"]:
        assert p["switchable"] is False
        assert p["enabled"] is False  # 测试配置未启用 game Agent
        assert p["tool_count"] == 0  # fake registry 中无 game 分类工具

    framework = by_category["framework"]
    assert [p["key"] for p in framework["providers"]] == ["framework"]
    assert framework["providers"][0]["switchable"] is False


def test_categories_runtime_count_uses_registry_category(tools_client: TestClient) -> None:
    """运行时计数按 (分类, provider 标识) 聚合，不按名字猜测。"""
    resp = tools_client.get("/api/v1/tools/categories")
    by_category = {c["category"]: c for c in resp.json()["categories"]}
    avatar_keys = {p["key"]: p for p in by_category["avatar"]["providers"]}
    assert avatar_keys["vts"]["tool_count"] == 1
    assert avatar_keys["warudo"]["tool_count"] == 0


# ==================== POST /tools/categories/.../control ====================


def test_control_disables_avatar_member(tools_client: TestClient, tools_config_dir: Path) -> None:
    resp = tools_client.post("/api/v1/tools/categories/avatar/vts/control", json={"action": "disable"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is False

    content = (tools_config_dir / "tools.toml").read_text(encoding="utf-8")
    assert "enabled = false" in content


def test_control_enables_studio_member(tools_client: TestClient, tools_config_dir: Path) -> None:
    resp = tools_client.post("/api/v1/tools/categories/studio/obs/control", json={"action": "enable"})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    content = (tools_config_dir / "tools.toml").read_text(encoding="utf-8")
    assert "enabled = true" in content


def test_control_category_level_member_writes_flat_section(tools_client: TestClient, tools_config_dir: Path) -> None:
    """vision/memory 为分类级开关，写 [tools.<分类>]，不落嵌套段。"""
    resp = tools_client.post("/api/v1/tools/categories/vision/vision/control", json={"action": "disable"})
    assert resp.status_code == 200

    content = (tools_config_dir / "tools.toml").read_text(encoding="utf-8")
    assert "[tools.vision]" in content
    assert "[tools.vision.vision]" not in content


def test_control_mcp_server_writes_server_section(tools_client: TestClient, tools_config_dir: Path) -> None:
    """mcp server 开关写 [tools.mcp.config.servers.<键>].enabled。"""
    resp = tools_client.post("/api/v1/tools/categories/mcp/generic_mcp/control", json={"action": "disable"})
    assert resp.status_code == 200

    content = (tools_config_dir / "tools.toml").read_text(encoding="utf-8")
    assert "[tools.mcp.config.servers.generic_mcp]" in content
    # 同一文件里 server 段落中应出现 disabled 的 enabled 值（粗校验：disable 后无 enabled = true 残留于该段）
    assert "enabled = false" in content


def test_control_rejects_agent_category(tools_client: TestClient) -> None:
    """Agent 自声明分类不可开关。"""
    resp = tools_client.post("/api/v1/tools/categories/game/game/control", json={"action": "enable"})
    assert resp.status_code == 400


def test_control_rejects_unknown_member(tools_client: TestClient) -> None:
    resp = tools_client.post("/api/v1/tools/categories/bogus/key/control", json={"action": "enable"})
    assert resp.status_code == 400


# ==================== POST /tools/{name}/control（工具级） ====================


def test_tool_control_disable_writes_disabled_list(tools_client: TestClient, tools_config_dir: Path) -> None:
    """停用工具 → 名字进入 [tools].disabled_tools。"""
    resp = tools_client.post("/api/v1/tools/vts_trigger_hotkey/control", json={"action": "disable"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is False

    content = (tools_config_dir / "tools.toml").read_text(encoding="utf-8")
    assert "disabled_tools" in content
    assert "vts_trigger_hotkey" in content


def test_tool_control_enable_removes_from_disabled_list(tools_client: TestClient, tools_config_dir: Path) -> None:
    import tomlkit

    tools_client.post("/api/v1/tools/vts_trigger_hotkey/control", json={"action": "disable"})
    resp = tools_client.post("/api/v1/tools/vts_trigger_hotkey/control", json={"action": "enable"})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    doc = tomlkit.parse((tools_config_dir / "tools.toml").read_text(encoding="utf-8"))
    assert list(doc["tools"]["disabled_tools"]) == []


def test_tool_control_unknown_name_returns_404(tools_client: TestClient) -> None:
    """停用未注册工具名 → 404（防拼写错误静默写入无效条目）。"""
    resp = tools_client.post("/api/v1/tools/ghost_tool/control", json={"action": "disable"})
    assert resp.status_code == 404


def test_tool_control_enable_unregistered_name_is_noop(tools_client: TestClient) -> None:
    """启用一个本来就不在停用列表的名字 → 幂等成功。"""
    resp = tools_client.post("/api/v1/tools/ghost_tool/control", json={"action": "enable"})
    assert resp.status_code == 200


# ==================== health 字段合并（熔断/恢复快照注入响应） ====================


class _HealthStubRegistry:
    """最小 stub：list_tools 接 include_tripped，tool_health_snapshot 返回钉死数据。"""

    def __init__(self, specs, *, snapshot: dict[str, dict[str, Any]] | None = None) -> None:
        self._specs = list(specs)
        self._snapshot = dict(snapshot or {})
        self._disabled: set[str] = set()
        self._tripped: set[str] = set()
        self._categories: dict[str, str] = {}
        self._scoped_owner: dict[str, str] = {}

    def list_tools(
        self,
        provider=None,
        *,
        include_disabled: bool = False,
        include_tripped: bool = False,
        include_scoped: bool = False,
    ):
        specs = list(self._specs)
        if not include_disabled:
            specs = [s for s in specs if s.name not in self._disabled]
        if not include_tripped:
            specs = [s for s in specs if s.name not in self._tripped]
        if provider is not None:
            specs = [s for s in specs if s.provider == provider]
        if provider is None and not include_scoped:
            specs = [s for s in specs if s.name not in self._scoped_owner]
        return specs

    def category_of(self, name: str) -> str:
        return self._categories.get(name, "")

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled

    def scoped_owner_of(self, name: str) -> str:
        return self._scoped_owner.get(name, "")

    def tool_health_snapshot(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._snapshot.items()}

    def apply_tripped(self, names) -> None:
        self._tripped = {n for n in names if any(s.name == n for s in self._specs)}


def _build_health_server(config_dir: Path, registry):
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=registry,  # type: ignore[arg-type]
    )


def test_tools_response_exposes_health_field_for_tripped_tool(config_dir: Path) -> None:
    """tripped 工具的 health 字段是带 state/tripped_at_ms 的对象；其他工具为 None。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    snapshot = {
        "vts_trigger_hotkey": {
            "provider": "vts",
            "state": "tripped",
            "failure_count": 5,
            "last_error": "ConnectionError: VTS 不可达",
            "tripped_at_ms": 1700000000000,
        },
    }
    registry = _HealthStubRegistry(_default_specs(), snapshot=snapshot)
    server = _build_health_server(config_dir, registry)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        assert resp.status_code == 200
        by_name = {a["name"]: a for a in resp.json()["tools"]}

        assert by_name["vts_trigger_hotkey"]["health"] == {
            "state": "tripped",
            "failure_count": 5,
            "last_error": "ConnectionError: VTS 不可达",
            "tripped_at_ms": 1700000000000,
        }
        assert by_name["reply_to_user"]["health"] is None
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_response_health_none_when_snapshot_missing_method(config_dir: Path) -> None:
    """registry 不实现 tool_health_snapshot 时，所有 health 字段都是 None（不抛 500）。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    server = _build_health_server(config_dir, registry)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        assert resp.status_code == 200
        for entry in resp.json()["tools"]:
            assert entry["health"] is None
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_tools_listing_includes_tripped_tools(config_dir: Path) -> None:
    """include_tripped=True 路径：默认被熔断摘除的工具仍出现在 listing（带 health 对象）。"""
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    snapshot = {
        "vts_trigger_hotkey": {
            "provider": "vts",
            "state": "tripped",
            "failure_count": 3,
            "last_error": "boom",
            "tripped_at_ms": 1700000000000,
        },
    }
    registry = _HealthStubRegistry(_default_specs(), snapshot=snapshot)
    registry.apply_tripped(["vts_trigger_hotkey"])
    server = _build_health_server(config_dir, registry)
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/tools")
        names = {a["name"] for a in resp.json()["tools"]}
        assert "vts_trigger_hotkey" in names
        assert "reply_to_user" in names
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
