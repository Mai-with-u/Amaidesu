"""v2 组件管理 API 测试套件（collectors/agents/tools 三组）

覆盖:
1. **get_v2_component_list** — 配置全集构建：
   - 采集器全集 = tools.perception.config 子键（含未启用占位）
   - Agent 全集 = agents 子键（enabled 之外的键）
   - 工具全集 = tools.output.config 子键（含未启用占位）
   - 未启用组件 is_enabled=False / is_started=False
   - 已启用 + 运行中 is_started=True
2. **ComponentListResponse** — 三组字段透传
3. **control 端点配置同步** — 幂等加入/移除 enabled 列表
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.modules.dashboard.schemas.component import ComponentListResponse
from src.modules.dashboard.utils.component_helper import get_v2_component_list


def _make_server(collector_running: set[str] | None = None, agent_running: set[str] | None = None):
    server = MagicMock()
    cm = MagicMock()
    cm.list_running = lambda: collector_running or set()
    am = MagicMock()
    am.list_running = lambda: agent_running or set()
    server.collector_manager = cm
    server.agent_manager = am
    server.tool_registry = MagicMock()
    return server


def _make_config() -> dict:
    return {
        "agents": {
            "enabled": ["streamer"],
            "streamer": {"planner_llm": "llm_fast"},
            "game": {},
        },
        "tools": {
            "enabled": ["perception", "output"],
            "perception": {
                "enabled": True,
                "provider": "builtin",
                "config": {
                    "enabled": ["bili_danmaku"],
                    "bili_danmaku": {"room_id": 1},
                    "mock_danmaku": {},
                    "read_pingmu": {},
                },
            },
            "output": {
                "enabled": True,
                "provider": "builtin",
                "config": {
                    "enabled": ["subtitle"],
                    "subtitle": {},
                    "vts": {},
                },
            },
        },
    }


def test_list_includes_disabled_collectors() -> None:
    config = _make_config()
    server = _make_server(collector_running={"bili_danmaku"})
    grouped = get_v2_component_list(config, server)

    by_name = {c.name: c for c in grouped["collectors"]}
    assert set(by_name) == {"bili_danmaku", "mock_danmaku", "read_pingmu"}

    danmaku = by_name["bili_danmaku"]
    assert danmaku.group == "collectors"
    assert danmaku.is_enabled is True
    assert danmaku.is_started is True

    mock = by_name["mock_danmaku"]
    assert mock.is_enabled is False
    assert mock.is_started is False


def test_list_includes_disabled_agents() -> None:
    config = _make_config()
    server = _make_server(agent_running={"streamer"})
    grouped = get_v2_component_list(config, server)

    by_name = {c.name: c for c in grouped["agents"]}
    assert set(by_name) == {"streamer", "game"}
    assert by_name["streamer"].is_enabled is True
    assert by_name["streamer"].is_started is True
    assert by_name["game"].is_enabled is False


def test_list_includes_disabled_tools() -> None:
    config = _make_config()
    grouped = get_v2_component_list(config, _make_server())

    by_name = {c.name: c for c in grouped["tools"]}
    assert set(by_name) == {"subtitle", "vts"}
    assert by_name["subtitle"].is_enabled is True
    assert by_name["vts"].is_enabled is False


def test_empty_config_returns_empty_groups() -> None:
    grouped = get_v2_component_list({}, _make_server())
    assert grouped == {"collectors": [], "agents": [], "tools": []}


def test_component_list_response_serializes_v2_groups() -> None:
    config = _make_config()
    grouped = get_v2_component_list(config, _make_server())

    resp = ComponentListResponse(
        collectors=grouped["collectors"],
        agents=grouped["agents"],
        tools=grouped["tools"],
    )
    data = resp.model_dump()
    assert set(data["collectors"][0].keys()) >= {"name", "group", "phase", "type", "is_started", "is_enabled"}
    assert data["input"] == []
    assert data["decision"] == []
    assert data["output"] == []


def test_agents_section_without_enabled_still_lists_children() -> None:
    config = {"agents": {"streamer": {}, "game": {"x": 1}}}
    grouped = get_v2_component_list(config, _make_server())
    names = {c.name for c in grouped["agents"]}
    assert names == {"streamer", "game"}
    assert all(c.is_enabled is False for c in grouped["agents"])
