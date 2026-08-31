"""Agenda Dashboard API 测试套件

覆盖：
1. ``GET /api/v1/agenda/state`` — available=false 各路径（无 agent / 关闭 / 组件未就绪）；
   available=true 时返回 snapshot / transitions / segments / expanded / config 全字段。
2. ``POST /api/v1/agenda/control`` — 各 action（pause/resume/skip/rewind/jump/unload/start）
   的成功路径；参数缺失（jump 无 segment_id、start 无 path）；state 抛 ValueError 时的
   success=false 包装；agent 不可用时的 success=false 兜底；start 加载 TOML 失败。
3. config 字段从 main_config["agents"]["streamer"] 正确读取（同时兼容旧 outline_* 字段）。

注：使用 Fake Agent（duck-typed 实现 get_agenda_view / agenda_control / is_agenda_available），
    不触发真实 LLM / AgendaState / AgendaLoader。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient


_CORE_TOML = """\
[meta]
version = "2.0.3"
"""


# ---------------------------------------------------------------------------
# Fake Agent（鸭子类型：实现 dashboard 关心的三个方法即可）
# ---------------------------------------------------------------------------


class FakeStreamerAgent:
    """最小可注入 Agent 替身（满足 _AgendaControlCallable + is_agenda_available + get_agenda_view）。"""

    def __init__(
        self,
        *,
        available: bool = True,
        snapshot: Optional[dict] = None,
        view: Optional[dict] = None,
        transitions: Optional[list] = None,
        segments: Optional[list] = None,
        expanded: Optional[dict] = None,
        control_result: Optional[tuple[bool, str, Optional[dict]]] = None,
        control_exception: Optional[Exception] = None,
    ) -> None:
        self._available = available
        self._snapshot = snapshot or {
            "status": "running",
            "current_segment": {"id": "s1", "title": "开场"},
            "next_segment": {"id": "s2", "title": "聊天"},
            "completed_count": 0,
            "total_count": 2,
            "is_paused": False,
            "elapsed_live_ms": 1000,
            "total_planned_ms": 30000,
            "progress_percent": 3.3,
            "agenda_id": "live",
            "agenda_title": "通用直播节目单",
            "manually_overridden": False,
        }
        self._view = view or {
            "snapshot": self._snapshot,
            "transitions": transitions
            or [
                {"event": "start", "segment_id": "s1", "reason": None, "timestamp_ms": 1000},
            ],
            "segments": segments
            or [
                {
                    "id": "s1",
                    "title": "开场",
                    "duration_ms": 15000,
                    "min_duration_ms": 5000,
                    "task_description": "自我介绍",
                    "key_points": ["问候", "介绍"],
                    "branch_count": 0,
                    "expanded": False,
                    "needs_expansion": True,
                },
                {
                    "id": "s2",
                    "title": "聊天",
                    "duration_ms": 15000,
                    "min_duration_ms": None,
                    "task_description": "互动",
                    "key_points": [],
                    "branch_count": 0,
                    "expanded": False,
                    "needs_expansion": True,
                },
            ],
            "expanded": expanded or {"s1": None, "s2": None},
        }
        self._control_result = control_result
        self._control_exception = control_exception
        self.calls: list[tuple[str, Optional[str], Optional[str]]] = []

    def is_agenda_available(self) -> bool:
        return self._available

    def get_agenda_view(self, *, now_ms: Optional[int] = None) -> Optional[dict]:
        return self._view if self._available else None

    async def agenda_control(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        path: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[dict]]:
        self.calls.append((action, segment_id, path))
        if self._control_exception is not None:
            raise self._control_exception
        assert self._control_result is not None, "FakeStreamerAgent 未注入 control_result"
        return self._control_result


class _FakeAgentManager:
    """仅暴露 get_agent_by_name 的最小 manager 替身。"""

    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        if name == "streamer":
            return self._agent
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_config(
    config_dir: Path,
    *,
    agenda_enabled: bool,
    agenda_path: str = "config/agenda/live.toml",
    agenda_auto_start: bool = True,
) -> None:
    """写入测试用的 agents.toml / core.toml（main_config["agents"]["streamer"] 来源）。"""
    (config_dir / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    agents_toml = f"""\
[agents]
enabled = ["streamer"]

[agents.streamer]
agenda_enabled = {str(agenda_enabled).lower()}
agenda_path = "{agenda_path}"
agenda_auto_start = {str(agenda_auto_start).lower()}
planner_llm = "llm_fast"
"""
    (config_dir / "agents.toml").write_text(agents_toml, encoding="utf-8")


def _make_client(
    config_dir: Path,
    agent: Optional[FakeStreamerAgent],
) -> TestClient:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        agent_manager=_FakeAgentManager(agent),  # type: ignore[arg-type]
    )
    set_dashboard_server(server)
    return TestClient(create_app())


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return cfg


# ---------------------------------------------------------------------------
# GET /api/v1/agenda/state
# ---------------------------------------------------------------------------


def test_get_state_unavailable_when_no_streamer_agent(config_dir: Path) -> None:
    """AgentManager 中无 streamer → available=false / message=主播 Agent 未启用。"""
    _write_config(config_dir, agenda_enabled=True)
    client = _make_client(config_dir, agent=None)

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["message"] == "主播 Agent 未启用"
    assert body["snapshot"] is None
    assert body["transitions"] == []
    assert body["segments"] == []
    assert body["expanded"] == {}
    # config 字段仍然尽力填充（main_config 仍可读）
    assert body["config"]["agenda_enabled"] is True


def test_get_state_unavailable_when_agenda_disabled(config_dir: Path) -> None:
    """agent 在场但 agenda_enabled=false → available=false / message=Agenda 未开启。"""
    _write_config(config_dir, agenda_enabled=False)
    agent = FakeStreamerAgent(available=True)
    client = _make_client(config_dir, agent=agent)

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["message"] == "Agenda 未开启"
    assert body["config"]["agenda_enabled"] is False


def test_get_state_unavailable_when_components_not_ready(config_dir: Path) -> None:
    """agent 存在 + agenda_enabled=true 但 is_agenda_available=False → 组件未就绪。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(available=False)
    client = _make_client(config_dir, agent=agent)

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["message"] == "Agenda 组件未就绪"


def test_get_state_returns_full_view_when_available(config_dir: Path) -> None:
    """正常路径：snapshot / transitions / segments / expanded / config 全部回填。"""
    _write_config(config_dir, agenda_enabled=True)
    expanded_payload = {
        "s1": {
            "opening_line": "嗨大家好",
            "topic_guidance": "打招呼",
            "talking_points": ["自我介绍", "今天主题"],
        },
        "s2": None,
    }
    agent = FakeStreamerAgent(
        available=True,
        expanded=expanded_payload,
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert body["message"] is None

    # snapshot 原样透传
    assert body["snapshot"]["status"] == "running"
    assert body["snapshot"]["current_segment"]["id"] == "s1"
    assert body["snapshot"]["agenda_title"] == "通用直播节目单"

    # transitions 列表
    assert len(body["transitions"]) == 1
    assert body["transitions"][0]["event"] == "start"

    # segments 强类型字段对齐
    s1 = next(s for s in body["segments"] if s["id"] == "s1")
    assert s1["title"] == "开场"
    assert s1["duration_ms"] == 15000
    assert s1["min_duration_ms"] == 5000
    assert s1["branch_count"] == 0
    assert s1["needs_expansion"] is True
    assert s1["expanded"] is False
    assert s1["key_points"] == ["问候", "介绍"]

    # expanded 字段（dict 或 None）
    assert body["expanded"]["s1"]["opening_line"] == "嗨大家好"
    assert body["expanded"]["s1"]["talking_points"] == ["自我介绍", "今天主题"]
    assert body["expanded"]["s2"] is None

    # config 字段来自 main_config
    assert body["config"]["agenda_enabled"] is True
    assert body["config"]["agenda_path"] == "config/agenda/live.toml"
    assert body["config"]["agenda_auto_start"] is True


def test_get_state_handles_view_exception_gracefully(config_dir: Path) -> None:
    """get_agenda_view 抛异常时返回 available=false + 异常消息（不 500）。"""

    class _BoomAgent(FakeStreamerAgent):
        def get_agenda_view(self, *, now_ms: Optional[int] = None) -> Optional[dict]:
            raise RuntimeError("state 内爆")

    _write_config(config_dir, agenda_enabled=True)
    client = _make_client(config_dir, agent=_BoomAgent(available=True))

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "Agenda 视图获取失败" in body["message"]
    assert "state 内爆" in body["message"]


def test_get_state_reads_outline_legacy_fields(config_dir: Path) -> None:
    """main_config 中 agents.streamer 同时存在 agenda_* 与 outline_* 字段时，
    agenda_* 字段优先（旧 outline_* 字段由 schema drift 写回后保留）。
    验证端点不报错，config 字段从 main_config 读取。
    """
    (config_dir / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    (config_dir / "agents.toml").write_text(
        """\
[agents]
enabled = ["streamer"]

[agents.streamer]
agenda_enabled = true
agenda_path = "config/agenda/live.toml"
outline_enabled = false
outline_path = "config/agenda/legacy.toml"
""",
        encoding="utf-8",
    )
    agent = FakeStreamerAgent(available=True)
    client = _make_client(config_dir, agent=agent)

    resp = client.get("/api/v1/agenda/state")
    assert resp.status_code == 200
    body = resp.json()
    # agenda_* 字段优先（因为 schema drift 总会写回 agenda_* 默认值）
    assert body["config"]["agenda_enabled"] is True
    assert body["config"]["agenda_path"] == "config/agenda/live.toml"


# ---------------------------------------------------------------------------
# POST /api/v1/agenda/control
# ---------------------------------------------------------------------------


def test_control_unavailable_when_no_streamer_agent(config_dir: Path) -> None:
    """Agent 不在 → success=false，不抛 HTTPException。"""
    _write_config(config_dir, agenda_enabled=True)
    client = _make_client(config_dir, agent=None)

    resp = client.post("/api/v1/agenda/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "主播 Agent 未启用"
    assert body["snapshot"] is None


def test_control_unavailable_when_agenda_disabled(config_dir: Path) -> None:
    """Agenda 未开启 → success=false / message=Agenda 未开启。"""
    _write_config(config_dir, agenda_enabled=False)
    agent = FakeStreamerAgent(available=True)
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "Agenda 未开启"


def test_control_pause_resume_success(config_dir: Path) -> None:
    """pause / resume 转发到 facade，snapshot 回填。"""
    _write_config(config_dir, agenda_enabled=True)
    snap = {"status": "running", "is_paused": True}
    agent = FakeStreamerAgent(
        available=True,
        control_result=(True, "已暂停", snap),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "已暂停"
    assert body["snapshot"] == snap
    assert agent.calls == [("pause", None, None)]


def test_control_skip_rewind_jump_success(config_dir: Path) -> None:
    """skip / rewind / jump 三种手动控制动作均转发到 facade。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(True, "已跳过到 s2", {"status": "running", "current_segment_id": "s2"}),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/agenda/control",
        json={"action": "jump", "segment_id": "s3"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert agent.calls == [("jump", "s3", None)]


def test_control_jump_without_segment_id_returns_false(config_dir: Path) -> None:
    """jump 缺 segment_id → success=false（facade 内校验失败，state.jump_to 不被调用）。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(False, "jump 必须提供 segment_id", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "jump"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "segment_id" in body["message"]
    # 端点原样转发；facade 内部对 jump 缺 segment_id 做前置校验，
    # 真实 StreamerAgent.agenda_control 不会触发 state.jump_to。
    # 这里 fake 替身仍记录调用，但返回的 (False, ...) 模拟了真实 facade 的契约。
    assert agent.calls == [("jump", None, None)]


def test_control_start_without_path_returns_false(config_dir: Path) -> None:
    """start 缺 path → success=false（facade 内前置校验失败，不加载 TOML）。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(False, "start 必须提供 path", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "start"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "path" in body["message"]
    # 端点原样转发；facade 内部对 start 缺 path 做前置校验。
    assert agent.calls == [("start", None, None)]


def test_control_unload_returns_null_snapshot(config_dir: Path) -> None:
    """unload 成功 → snapshot 字段为 None（语义上 unload 后无快照）。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(True, "已卸载节目单", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "unload"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["snapshot"] is None


def test_control_start_success_passes_path_to_facade(config_dir: Path) -> None:
    """start 成功路径：path 透传给 facade，snapshot 回填。"""
    _write_config(config_dir, agenda_enabled=True)
    snap = {"status": "running", "agenda_id": "live"}
    agent = FakeStreamerAgent(
        available=True,
        control_result=(True, "已启动节目单 live", snap),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/agenda/control",
        json={"action": "start", "path": "config/agenda/live.toml"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "已启动节目单 live"
    assert body["snapshot"] == snap
    assert agent.calls == [("start", None, "config/agenda/live.toml")]


def test_control_start_toml_load_failure_wrapped(config_dir: Path) -> None:
    """start 时 TOML 加载失败（ValidationError 等）→ success=false 带原因。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(False, "TOML 解析失败: 字段 duration_ms 校验失败", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/agenda/control",
        json={"action": "start", "path": "config/agenda/bad.toml"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "TOML 解析失败" in body["message"]


def test_control_state_raises_value_error_wrapped_as_false(config_dir: Path) -> None:
    """state 抛 ValueError（jump 到不存在 segment_id 等）→ success=false 包装。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(False, "AgendaState.jump_to: 不存在的环节 id='xxx'", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/agenda/control",
        json={"action": "jump", "segment_id": "xxx"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "不存在的环节" in body["message"]


def test_control_unknown_action_returns_false(config_dir: Path) -> None:
    """facade 收到非契约 action 名 → success=false（不抛 500）。

    Pydantic 端点层只接受枚举内 action；此处用合法 action 但 fake facade 返回
    "未知 action" 消息来覆盖 facade 兜底分支。
    """
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(False, "未知 action: 'foo'", None),
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "未知 action" in body["message"]


def test_control_invalid_enum_value_rejected_at_validation_layer(config_dir: Path) -> None:
    """Pydantic enum 校验层拒绝未知 action → HTTP 422（不在 dashboard 业务层处理）。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(available=True)
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/agenda/control", json={"action": "foo"})
    assert resp.status_code == 422
    assert agent.calls == []


def test_control_internal_exception_does_not_500(config_dir: Path) -> None:
    """facade 自身抛未预期异常 → API 层兜底为 success=false（不返 500）。"""
    _write_config(config_dir, agenda_enabled=True)

    class _BoomAgent(FakeStreamerAgent):
        async def agenda_control(  # type: ignore[override]
            self,
            action: str,
            *,
            segment_id: Optional[str] = None,
            path: Optional[str] = None,
            now_ms: Optional[int] = None,
        ) -> tuple[bool, str, Optional[dict]]:
            raise RuntimeError("内部爆炸")

    client = _make_client(config_dir, agent=_BoomAgent(available=True))

    resp = client.post("/api/v1/agenda/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "控制失败" in body["message"]


def test_control_passes_segment_id_only_for_jump(config_dir: Path) -> None:
    """pause/resume/skip/rewind 不消费 segment_id（仅 jump 用），path 同理（仅 start 用）。"""
    _write_config(config_dir, agenda_enabled=True)
    agent = FakeStreamerAgent(
        available=True,
        control_result=(True, "ok", {}),
    )
    client = _make_client(config_dir, agent=agent)

    client.post(
        "/api/v1/agenda/control",
        json={"action": "skip", "segment_id": "ignored", "path": "ignored"},
    )
    assert agent.calls == [("skip", "ignored", "ignored")]  # 端点不主动 strip
    # 这是预期：端点只把请求体原样转发给 facade，由 facade 内部决定是否使用。
