"""组件清单构建（collectors / agents 两组）：采集器全集 = 顶层各段 ∪ enabled 名单（meta 等文件级键不算），
Agent 全集 = agents 段子键；未启用 is_enabled=False，运行中 is_started=True"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.modules.dashboard.schemas.component import ComponentListResponse
from src.modules.dashboard.utils.component_helper import get_v2_component_list


def _make_server(
    collector_running: set[str] | None = None,
    agent_running: set[str] | None = None,
    collector_descs: dict[str, str] | None = None,
    agent_descs: dict[str, str] | None = None,
):
    server = MagicMock()
    cm = MagicMock()
    cm.list_running = lambda: collector_running or set()
    cm.descriptions = dict(collector_descs or {})
    am = MagicMock()
    am.list_running = lambda: agent_running or set()
    am.descriptions = dict(agent_descs or {})
    server.collector_manager = cm
    server.agent_manager = am
    server.tool_registry = MagicMock()
    return server


def _make_config() -> dict:
    """组件配置视图（新契约：collectors scope = collectors.toml 顶层内容，
    enabled 名单与各采集器段同层；meta 为文件级键不算组件）。"""
    return {
        "agents": {
            "enabled": ["streamer"],
            "streamer": {"planner_llm": "llm_fast"},
            "minecraft": {},
        },
        "collectors": {
            "enabled": ["bili_danmaku", "stt"],
            "meta": {"version": "2.0.31"},
            "bili_danmaku": {"room_id": 1},
            "bili_danmaku_official": {},
        },
    }


def test_list_includes_disabled_collectors() -> None:
    config = _make_config()
    server = _make_server(collector_running={"bili_danmaku"})
    grouped = get_v2_component_list(config, server)

    # 新契约：组件全集 = 采集器段 ∪ enabled 名单（stt 仅在名单中，空配置占位）
    # ∪ agents.toml 里有段的 Agent 的附属采集器（启用跟所属 Agent 走，owner 标明归属）
    by_name = {c.name: c for c in grouped["collectors"]}
    assert set(by_name) == {"bili_danmaku", "bili_danmaku_official", "stt", "maicraft_attention", "maicraft_chat"}
    assert by_name["maicraft_chat"].owner == "minecraft"
    assert by_name["maicraft_chat"].is_enabled is False  # minecraft 有段但未启用
    assert by_name["bili_danmaku"].owner is None
    assert "meta" not in by_name  # 文件级键不算组件

    danmaku = by_name["bili_danmaku"]
    assert danmaku.group == "collectors"
    assert danmaku.is_enabled is True
    assert danmaku.is_started is True

    official = by_name["bili_danmaku_official"]
    assert official.is_enabled is False
    assert official.is_started is False

    stt = by_name["stt"]
    assert stt.is_enabled is True  # 仅在名单中、无配置段也算启用
    assert stt.is_started is False


def test_list_includes_disabled_agents_and_excludes_tools_group() -> None:
    grouped = get_v2_component_list(_make_config(), _make_server(agent_running={"streamer"}))
    by_name = {c.name: c for c in grouped["agents"]}
    assert set(by_name) == {"streamer", "minecraft"}
    assert (by_name["streamer"].is_enabled, by_name["streamer"].is_started) == (True, True)
    assert by_name["minecraft"].is_enabled is False
    # 工具不在组件清单（域开关单元归 tools API 的 categories 端点管）
    assert set(grouped) == {"collectors", "agents"}


def test_edge_configs_and_response_serialization() -> None:
    assert get_v2_component_list({}, _make_server()) == {"collectors": [], "agents": []}
    # agents 段没有 enabled 名单时子段照样出卡，全部未启用
    grouped = get_v2_component_list({"agents": {"streamer": {}, "minecraft": {"x": 1}}}, _make_server())
    assert {c.name for c in grouped["agents"]} == {"streamer", "minecraft"}
    assert all(c.is_enabled is False for c in grouped["agents"])

    grouped = get_v2_component_list(_make_config(), _make_server())
    data = ComponentListResponse(collectors=grouped["collectors"], agents=grouped["agents"]).model_dump()
    assert set(data["collectors"][0]) >= {"name", "group", "type", "is_started", "is_enabled"}


def test_description_comes_from_manager_and_defaults_to_empty() -> None:
    server = _make_server(
        collector_running={"bili_danmaku"},
        collector_descs={"bili_danmaku": "B站弹幕接收器"},
        agent_descs={"streamer": "主播 Agent"},
    )
    grouped = get_v2_component_list(_make_config(), server)
    collectors = {c.name: c.description for c in grouped["collectors"]}
    agents = {c.name: c.description for c in grouped["agents"]}
    assert (collectors["bili_danmaku"], collectors["bili_danmaku_official"]) == ("B站弹幕接收器", "")
    assert (agents["streamer"], agents["minecraft"]) == ("主播 Agent", "")
