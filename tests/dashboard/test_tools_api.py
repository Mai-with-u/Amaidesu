"""Tools API：工具清单 / 分类目录与开关 / 工具级停用 / 调试调用 / Provider 重连"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from src.modules.config.core_schemas import DashboardConfig
from src.modules.config.multi_file_loader import update_config_values
from src.modules.config.service import ConfigService
from src.modules.dashboard.api.router import create_app
from src.modules.dashboard.dependencies import get_dashboard_server, set_dashboard_server
from src.modules.dashboard.server import DashboardServer
from src.modules.utils.time_utils import now_ms
from src.modules.tools.models import ResultBlock, ToolExecutionResult, ToolSpec


def _load_toml(config_dir: Path, name: str) -> dict:
    # 配置管线落盘带 BOM
    return tomllib.loads((config_dir / name).read_text(encoding="utf-8-sig"))


def _spec(name: str, description: str, schema=None, provider: str = "framework", **kwargs: Any) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters_schema=schema, provider=provider, **kwargs)


class _FakeToolRegistry:
    """最小 registry 替身；停用集与 invoke 键按全名索引，对齐真实 registry 口径。"""

    def __init__(self, specs, categories: dict[str, str] | None = None, providers: list[dict] | None = None) -> None:
        self._specs = list(specs)
        self._categories = dict(categories or {})
        self._disabled: set[str] = set()
        self.tripped: set[str] = set()
        self.scoped_owner: dict[str, str] = {}
        self.supports_reconnect: dict[str, bool] = {}
        self._providers_view = list(providers or [])
        self.invocations: list[Any] = []
        self.invoke_results: dict[str, Callable[[Any], ToolExecutionResult]] = {}

    def list_providers(self) -> list[dict]:
        return list(self._providers_view)

    def list_tools(self, provider=None, *, include_disabled=False, include_tripped=False, include_scoped=False):
        specs = [s for s in self._specs if include_disabled or s.full_name not in self._disabled]
        specs = [s for s in specs if include_tripped or s.name not in self.tripped]
        if provider is not None:
            return [s for s in specs if s.provider == provider]
        return [s for s in specs if include_scoped or s.name not in self.scoped_owner]

    def category_of(self, name: str) -> str:
        spec = next((s for s in self._specs if s.full_name == name), None)
        return self._categories.get(spec.provider, "") if spec else ""

    def has(self, name: str) -> bool:
        return any(s.full_name == name for s in self._specs)

    async def invoke(self, invocation) -> Any:
        self.invocations.append(invocation)
        handler = self.invoke_results.get(invocation.tool_name)
        if handler is not None:
            return handler(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="ok", timestamp_ms=now_ms())

    def apply_disabled(self, names) -> int:
        self._disabled = {n for n in names if self.has(n)}
        return len(self._disabled)

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled

    def scoped_owner_of(self, name: str) -> str:
        return self.scoped_owner.get(name, "")

    def provider_supports_reconnect(self, name: str) -> bool:
        return self.supports_reconnect.get(name, False)


class _NoReconnectQueryRegistry(_FakeToolRegistry):
    provider_supports_reconnect = None  # type: ignore[assignment]


class _HealthRegistry(_FakeToolRegistry):
    def __init__(self, snapshot: dict[str, dict[str, Any]]) -> None:
        super().__init__(_default_specs(), _default_categories())
        self._snapshot = snapshot

    def tool_health_snapshot(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._snapshot.items()}


class _ReconnectRegistry(_FakeToolRegistry):
    def __init__(self, result: dict | None) -> None:
        super().__init__(_default_specs(), _default_categories())
        self._result = result
        self.reconnect_calls: list[str] = []

    async def reconnect_provider(self, provider_id: str) -> dict:
        self.reconnect_calls.append(provider_id)
        return dict(self._result) if self._result else {"ok": False, "error": f"未注册 Provider: {provider_id}"}


def _default_specs() -> list[ToolSpec]:
    hotkey_schema = {
        "type": "object",
        "properties": {
            "hotkey": {"type": "string", "description": "热键名"},
            "speed": {"type": "number", "default": 1.0, "minimum": 0.5, "maximum": 2.0},
        },
        "required": ["hotkey"],
    }
    reply_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    return [
        _spec("vts_trigger_hotkey", "触发 VTS 热键", hotkey_schema, provider="vts"),
        _spec(
            "reply_to_user",
            "回复用户",
            reply_schema,
            provider="streamer",
            kind="async",
            result_event="tool.result.reply_to_user",
        ),
        _spec("framework_delegate", "委派工作"),
    ]


def _default_categories() -> dict[str, str]:
    # 键 = provider 名（分类按 provider 归账）
    return {"vts": "avatar", "streamer": "streamer", "framework": "framework"}


def _prepare_config(config_dir: Path) -> None:
    """铺出覆盖全部成员形态的配置：avatar 名单 + studio 成员 + 分类级开关 + mcp server。"""
    ConfigService(base_dir=str(config_dir.parent)).initialize()
    update_config_values(
        config_dir,
        "tools.toml",
        {
            "tools.studio.obs": {"enabled": False},
            "tools.vision.enabled": True,
            "tools.mcp.enabled": False,
            "tools.mcp.config.servers.generic_mcp": {"enabled": True},
        },
    )
    update_config_values(config_dir, "avatar.toml", {"platform.enabled": ["vts"]})


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return cfg


@pytest.fixture
def make_client(config_dir: Path):
    def _make(registry: Any = "default", *, prepared: bool = False) -> TestClient:
        if registry == "default":
            registry = _FakeToolRegistry(_default_specs(), _default_categories())
        if prepared:
            _prepare_config(config_dir)
        svc = ConfigService(base_dir=str(config_dir.parent))
        svc.initialize()
        server = DashboardServer(
            event_bus=None,  # type: ignore[arg-type]
            config_service=svc,
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
            tool_registry=registry,  # type: ignore[arg-type]
        )
        set_dashboard_server(server)
        return TestClient(create_app())

    yield _make
    set_dashboard_server(None)  # type: ignore[arg-type]


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def tools_client(make_client) -> TestClient:
    return make_client(prepared=True)


def _registry() -> _FakeToolRegistry:
    return get_dashboard_server().tool_registry  # type: ignore[return-value]


def _tools_by_name(client: TestClient, **params: Any) -> dict[str, dict]:
    resp = client.get("/api/v1/tools", params=params)
    assert resp.status_code == 200
    return {a["name"]: a for a in resp.json()["tools"]}


def _categories(client: TestClient) -> dict[str, dict[str, dict]]:
    resp = client.get("/api/v1/tools/categories")
    assert resp.status_code == 200
    return {c["category"]: {p["key"]: p for p in c["providers"]} for c in resp.json()["categories"]}


# GET /tools


def test_tools_listing_exposes_spec_metadata_and_parameters(client: TestClient) -> None:
    by_name = _tools_by_name(client)

    hotkey = by_name["vts_trigger_hotkey"]
    assert hotkey["description"] == "触发 VTS 热键"
    assert (hotkey["provider"], hotkey["kind"], hotkey["category"]) == ("vts", "sync", "avatar")
    assert "result_event" not in hotkey
    # full_name = 注册表调用键（<provider>_<裸名>），前端调试调用/停用以它为标识
    assert hotkey["full_name"] == "vts_vts_trigger_hotkey"
    assert (hotkey["disabled"], hotkey["owner_agent"], hotkey["supports_reconnect"]) == (False, "", False)
    assert hotkey["health"] is None
    hk = hotkey["parameters"]["hotkey"]
    assert (hk["type"], hk["required"], hk["description"]) == ("string", True, "热键名")
    speed = hotkey["parameters"]["speed"]
    assert (speed["type"], speed["default"], speed["minimum"], speed["maximum"]) == ("number", 1.0, 0.5, 2.0)
    assert speed["required"] is False

    reply = by_name["reply_to_user"]
    assert (reply["kind"], reply["result_event"]) == ("async", "tool.result.reply_to_user")
    assert by_name["framework_delegate"]["full_name"] == "framework_framework_delegate"


def test_tools_non_scalar_params_map_to_json_type(make_client) -> None:
    """array/object/缺失 type 的参数 → type=json（MCP 复杂参数不静默丢弃）。"""
    schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "目标"},
            "blueprint": {"type": "object", "description": "蓝图对象"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "mystery": {"description": "无 type 声明"},
        },
        "required": ["goal"],
    }
    client = make_client(_FakeToolRegistry([_spec("complex_tool", "复杂参数工具", schema, provider="mcp")]))
    params = _tools_by_name(client)["complex_tool"]["parameters"]
    assert {k: v["type"] for k, v in params.items()} == {
        "goal": "string",
        "blueprint": "json",
        "tags": "json",
        "mystery": "json",
    }
    assert params["blueprint"]["description"] == "蓝图对象"


def test_tools_listing_reflects_disabled_scoped_and_reconnect_state(make_client) -> None:
    registry = _FakeToolRegistry(_default_specs(), _default_categories())
    registry.apply_disabled(["vts_vts_trigger_hotkey"])
    registry.scoped_owner["vts_trigger_hotkey"] = "minecraft"
    registry.supports_reconnect["vts_trigger_hotkey"] = True
    by_name = _tools_by_name(make_client(registry))

    assert by_name["vts_trigger_hotkey"]["disabled"] is True
    assert by_name["vts_trigger_hotkey"]["owner_agent"] == "minecraft"
    assert by_name["vts_trigger_hotkey"]["supports_reconnect"] is True
    assert (by_name["reply_to_user"]["disabled"], by_name["reply_to_user"]["owner_agent"]) == (False, "")


def test_tools_reconnect_flag_false_when_registry_lacks_query(make_client) -> None:
    client = make_client(_NoReconnectQueryRegistry(_default_specs(), _default_categories()))
    assert all(entry["supports_reconnect"] is False for entry in _tools_by_name(client).values())


def test_tools_provider_query_filter(client: TestClient) -> None:
    assert set(_tools_by_name(client, provider="vts")) == {"vts_trigger_hotkey"}


def test_tools_without_registry_is_503_and_empty_registry_is_empty(make_client) -> None:
    assert make_client(None).get("/api/v1/tools").status_code == 503
    assert make_client(_FakeToolRegistry([])).get("/api/v1/tools").json() == {"tools": []}


def test_tools_listing_includes_tripped_tools_with_health(make_client) -> None:
    health = {
        "provider": "vts",
        "state": "tripped",
        "failure_count": 5,
        "last_error": "ConnectionError: VTS 不可达",
        "tripped_at_ms": 1700000000000,
    }
    registry = _HealthRegistry({"vts_trigger_hotkey": health})
    registry.tripped.add("vts_trigger_hotkey")
    by_name = _tools_by_name(make_client(registry))

    # 熔断摘除的工具仍在清单里，带 health 对象；未熔断工具为 None
    assert by_name["vts_trigger_hotkey"]["health"] == {k: v for k, v in health.items() if k != "provider"}
    assert by_name["reply_to_user"]["health"] is None


# GET /tools/categories


def test_categories_lists_static_members_with_config_and_runtime_state(tools_client: TestClient) -> None:
    resp = tools_client.get("/api/v1/tools/categories")
    order = [c["category"] for c in resp.json()["categories"]]
    assert order == ["avatar", "studio", "vision", "memory", "web", "mcp", "game", "framework"]
    cats = _categories(tools_client)

    vts = cats["avatar"]["vts"]
    assert set(cats["avatar"]) == {"vts", "vrchat", "warudo", "expression_sheet"}
    assert (vts["enabled"], vts["in_config"], vts["switchable"]) == (True, True, True)
    assert (vts["tool_count"], vts["disabled_count"], vts["provider_name"]) == (1, 0, "vts")
    # 运行时计数按 (分类, provider) 聚合；配置未声明的已知成员仍列出供面板开启
    assert cats["avatar"]["warudo"]["tool_count"] == 0
    assert (cats["avatar"]["vrchat"]["in_config"], cats["avatar"]["vrchat"]["enabled"]) == (False, False)
    assert (cats["studio"]["obs"]["enabled"], cats["studio"]["obs"]["tool_count"]) == (False, 0)
    assert (cats["vision"]["vision"]["enabled"], cats["vision"]["vision"]["tool_count"]) == (True, 0)
    # game / framework 由注册表动态发现；替身无记录时为空，但分类仍输出以保持侧边栏稳定
    assert cats["game"] == {} and cats["framework"] == {}


def test_categories_lists_mcp_servers_dynamically(tools_client: TestClient) -> None:
    assert _categories(tools_client)["mcp"] == {
        "generic_mcp": {
            "key": "generic_mcp",
            "provider_name": "generic_mcp",
            "description": "MCP server",
            "enabled": True,
            "in_config": True,
            "switchable": True,
            "tool_count": 0,
            "disabled_count": 0,
            "registered": False,
            "degraded": False,
            "supports_reconnect": False,
            "last_error": "",
            "notice": "",
        }
    }


def _registry_with_degraded_mcp() -> _FakeToolRegistry:
    """带降级 MCP Provider 记录的 registry（Agent 私有 maicraft 形态）。"""
    record = {
        "name": "maicraft",
        "category": "mcp",
        "tool_count": 0,
        "disabled_count": 0,
        "supports_reconnect": True,
        "last_error": "RuntimeError: Client failed to connect",
        "switch": {"file": "agents.toml", "key": "agents.minecraft.mcp.enabled"},
    }
    return _FakeToolRegistry([], providers=[record])


def test_categories_registry_provider_card_degraded(make_client) -> None:
    card = _categories(make_client(_registry_with_degraded_mcp()))["mcp"]["maicraft"]
    assert card["registered"] is True and card["degraded"] is True
    assert card["supports_reconnect"] is True
    assert "Client failed to connect" in card["last_error"]
    assert "身体事件采集器" in card["notice"]
    # 开关状态读自 agents.minecraft.mcp.enabled（基线默认启用）
    assert card["enabled"] is True


def test_control_agent_private_mcp_routes_to_agents_toml(make_client, config_dir: Path) -> None:
    client = make_client(_registry_with_degraded_mcp(), prepared=True)
    resp = client.post("/api/v1/tools/categories/mcp/maicraft/control", json={"action": "disable"})
    assert resp.status_code == 200 and "agents.toml" in resp.json()["message"]
    assert _load_toml(config_dir, "agents.toml")["agents"]["minecraft"]["mcp"]["enabled"] is False


# POST /tools/categories/{category}/{key}/control


def _tools_section(config_dir: Path) -> dict:
    return _load_toml(config_dir, "tools.toml")["tools"]


@pytest.mark.parametrize(
    ("member", "action", "check"),
    [
        # avatar 平台开关 = 启用名单增删
        ("avatar/vts", "disable", lambda cfg: "vts" not in _load_toml(cfg, "avatar.toml")["platform"]["enabled"]),
        ("studio/obs", "enable", lambda cfg: _tools_section(cfg)["studio"]["obs"]["enabled"] is True),
        # 分类级开关写 [tools.<分类>]，不落嵌套段
        ("vision/vision", "disable", lambda cfg: _tools_section(cfg)["vision"].get("vision") is None),
        ("vision/vision", "disable", lambda cfg: _tools_section(cfg)["vision"]["enabled"] is False),
        (
            "mcp/generic_mcp",
            "disable",
            lambda cfg: not _tools_section(cfg)["mcp"]["config"]["servers"]["generic_mcp"]["enabled"],
        ),
    ],
)
def test_control_member_writes_config(tools_client: TestClient, config_dir: Path, member, action, check) -> None:
    resp = tools_client.post(f"/api/v1/tools/categories/{member}/control", json={"action": action})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["enabled"] is (action == "enable")
    assert "重启后生效" in body["message"]
    assert check(config_dir)


@pytest.mark.parametrize("member", ["game/game", "bogus/key"], ids=["agent_category", "unknown"])
def test_control_rejects_non_switchable_member(tools_client: TestClient, member: str) -> None:
    resp = tools_client.post(f"/api/v1/tools/categories/{member}/control", json={"action": "enable"})
    assert resp.status_code == 400


# POST /tools/{full_name}/control（工具级）


def test_tool_control_disable_then_enable_updates_disabled_list(tools_client: TestClient, config_dir: Path) -> None:
    url = "/api/v1/tools/vts_vts_trigger_hotkey/control"
    resp = tools_client.post(url, json={"action": "disable"})
    body = resp.json()
    assert resp.status_code == 200 and body["success"] is True and body["enabled"] is False
    assert "重启后生效" in body["message"]
    assert _load_toml(config_dir, "tools.toml")["tools"]["disabled_tools"] == ["vts_vts_trigger_hotkey"]

    assert tools_client.post(url, json={"action": "enable"}).json()["enabled"] is True
    assert _load_toml(config_dir, "tools.toml")["tools"]["disabled_tools"] == []


@pytest.mark.parametrize(
    ("name", "action", "status"),
    [
        ("ghost_tool", "disable", 404),  # 防拼写错误静默写入无效条目
        ("vts_trigger_hotkey", "disable", 404),  # 裸名不是注册表键
        ("ghost_tool", "enable", 200),  # 启用本就不在停用列表的名字：幂等成功
    ],
)
def test_tool_control_name_validation(tools_client: TestClient, name: str, action: str, status: int) -> None:
    assert tools_client.post(f"/api/v1/tools/{name}/control", json={"action": action}).status_code == status


# POST /tools/{full_name}/invoke（调试调用）


def test_invoke_success_returns_result_and_tags_invocation(client: TestClient) -> None:
    resp = client.post("/api/v1/tools/vts_vts_trigger_hotkey/invoke", json={"arguments": {"hotkey": "hi"}})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["success"], body["content"], body["blocks"], body["error_message"]) == (True, "ok", [], "")
    assert body["structured_content"] is None
    assert isinstance(body["duration_ms"], int) and isinstance(body["timestamp_ms"], int)

    (inv,) = _registry().invocations
    assert (inv.tool_name, inv.source, inv.arguments) == ("vts_vts_trigger_hotkey", "dashboard-debug", {"hotkey": "hi"})
    assert inv.call_id != ""

    assert client.post("/api/v1/tools/framework_framework_delegate/invoke", json={}).status_code == 200
    assert _registry().invocations[-1].arguments == {}


@pytest.mark.parametrize(
    ("name", "arguments", "status", "detail"),
    [
        ("ghost_tool", {}, 404, "ghost_tool"),
        # 超过 64KB 拒收，防大文本进 tool.result 事件流/落库
        ("vts_vts_trigger_hotkey", {"hotkey": "x" * (64 * 1024 + 1)}, 400, "超限"),
    ],
)
def test_invoke_rejects_bad_requests(client: TestClient, name, arguments, status, detail) -> None:
    resp = client.post(f"/api/v1/tools/{name}/invoke", json={"arguments": arguments})
    assert resp.status_code == status and detail in resp.json()["detail"]
    assert _registry().invocations == []


def test_invoke_tool_failure_is_200_with_success_false(client: TestClient) -> None:
    _registry().invoke_results["vts_vts_trigger_hotkey"] = lambda inv: ToolExecutionResult(
        tool_name=inv.tool_name, success=False, error_message="工具已停用", timestamp_ms=now_ms()
    )
    resp = client.post("/api/v1/tools/vts_vts_trigger_hotkey/invoke", json={"arguments": {}})
    assert resp.status_code == 200
    assert (resp.json()["success"], resp.json()["error_message"]) == (False, "工具已停用")


def test_invoke_serializes_structured_content_and_blocks(client: TestClient) -> None:
    sentinel = object()  # 不可 JSON 序列化 → 兜底 str()
    _registry().invoke_results["vts_vts_trigger_hotkey"] = lambda inv: ToolExecutionResult(
        tool_name=inv.tool_name,
        success=True,
        content="done",
        blocks=[ResultBlock(kind="text", text="块文本")],
        structured_content={"count": 2, "weird": sentinel},
        duration_ms=12,
        timestamp_ms=now_ms(),
    )
    body = client.post("/api/v1/tools/vts_vts_trigger_hotkey/invoke", json={"arguments": {}}).json()
    assert body["structured_content"] == {"count": 2, "weird": str(sentinel)}
    assert body["blocks"] == [{"kind": "text", "text": "块文本", "data": "", "mime_type": ""}]
    assert body["duration_ms"] == 12


# POST /tools/providers/{provider_id}/reconnect


@pytest.mark.parametrize(
    ("result", "status", "detail"),
    [
        (None, 404, "未注册 Provider: vts"),
        ({"ok": False, "error": "Provider 'vts' 不支持手动重连"}, 409, "不支持手动重连"),
        ({"ok": False, "error": "重连失败: 通道超时"}, 500, "通道超时"),
    ],
    ids=["unknown", "unsupported", "failed"],
)
def test_reconnect_provider_error_mapping(make_client, result, status: int, detail: str) -> None:
    resp = make_client(_ReconnectRegistry(result)).post("/api/v1/tools/providers/vts/reconnect")
    assert resp.status_code == status and detail in resp.json()["detail"]


def test_reconnect_provider_success_returns_report(make_client) -> None:
    report = {"ok": True, "provider_id": "vts", "recovered": ["vts_trigger_hotkey"], "still_tripped": []}
    registry = _ReconnectRegistry(report)
    resp = make_client(registry).post("/api/v1/tools/providers/vts/reconnect")
    assert resp.status_code == 200 and resp.json() == report
    assert registry.reconnect_calls == ["vts"]


def test_reconnect_provider_registry_without_capability_is_503(client: TestClient) -> None:
    assert client.post("/api/v1/tools/providers/vts/reconnect").status_code == 503
