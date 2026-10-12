"""Agent 控制面 API 测试套件（/api/v1/agents）

覆盖：
1. **GET /api/v1/agents** — 列表非空且 ≥ enabled 数；条目字段齐全
2. **GET /api/v1/agents/{name}/state** — 状态字段齐全；未知名 404
3. **POST /api/v1/agents/{name}/control** — shutdown/restart 缺 confirm →
   400 + 中文风险说明；pause/resume 无需 confirm 即生效；restart 走
   rebuild（restart_count +1 跨实例继承）；未知动作 400
4. agent_control 未注入 → 503

AgentManager 用真实实例 + monkeypatch 工厂产出轻量替身 Agent（与
tests/modules/agents/test_supervisor.py 同一测试基建）。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator, Generator, Iterable, Optional, TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from src.modules.agents import AgentManager, AgentState, BaseAgent
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.tools.models import ToolSpec

if TYPE_CHECKING:
    from src.modules.tools.tasks import TaskLedger


class _SampleAgent(BaseAgent):
    """最小可工作子类（无外部依赖）。"""

    name = "sample_agent"
    description = "sample agent for agents api tests"

    def __init__(self, accept_prompt: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self._accept_prompt = accept_prompt
        self.received_prompts: list[tuple[str, str]] = []
        self._accept_cancel = True
        self.cancelled_tasks: list[str] = []
        self._accept_delegate = True
        self.delegated: list[tuple[str, str]] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        if not self._accept_prompt:
            return False
        self.received_prompts.append((content, source))
        return True

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        if not self._accept_delegate:
            return "忙不过来"
        self.delegated.append((task_id, instruction))
        return None

    def cancel_task(self, task_id: str, source: str = "") -> bool:
        if not self._accept_cancel:
            return False
        self.cancelled_tasks.append(task_id)
        return True

    async def _on_start(self) -> None:
        return None

    async def _on_stop(self) -> None:
        return None


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return cfg


@pytest.fixture
async def manager() -> AsyncGenerator[AgentManager, None]:
    """真实 AgentManager；工厂按名产出 _SampleAgent（绕过生产三类重依赖）。"""
    from src.modules.agents import factory

    def _fake_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        if name != "sample_agent":
            return None
        # 心跳关闭（heartbeat_interval_ms=0）：TestClient 与 pytest-asyncio
        # 分属两个事件循环，心跳后台任务跨循环取消会抛 RuntimeError
        return _SampleAgent(heartbeat_interval_ms=0)

    original = factory.instantiate_agent
    factory.instantiate_agent = _fake_instantiate
    mgr = AgentManager(supervisor_config=AgentSupervisorConfig(check_interval_ms=0))
    try:
        assert await mgr.enable_agent("sample_agent") is True
        yield mgr
    finally:
        factory.instantiate_agent = original
        await mgr.stop_supervisor()
        for name in mgr.list_agents():
            agent = mgr.get_agent_by_name(name)
            if agent is not None and agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
                await agent.stop()


@pytest.fixture
def client(config_dir: Path, manager: AgentManager) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer
    from src.modules.tools.registry import ToolRegistry
    from src.modules.tools.tasks import TaskLedger, TaskTracker

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    ledger = TaskLedger(event_bus=None)
    tracker = TaskTracker(ToolRegistry(), ledger)
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
        agent_manager=manager,
        task_tracker=tracker,
    )
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


def _current_task_ledger() -> "TaskLedger":
    """从当前注入的 DashboardServer 取任务账本（直派断言用）。"""
    import src.modules.dashboard.dependencies as deps

    server = deps._dashboard_server
    assert server is not None and server.task_tracker is not None
    return server.task_tracker.ledger


# ==================== GET /api/v1/agents ====================


def test_agents_list_non_empty_and_covers_enabled(client: TestClient) -> None:
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    assert len(agents) >= 1
    enabled = [a for a in agents if a["enabled"]]
    assert len(agents) >= len(enabled)
    names = {a["name"] for a in agents}
    assert "sample_agent" in names


def test_agents_list_entry_fields_complete(client: TestClient) -> None:
    resp = client.get("/api/v1/agents")
    entry = next(a for a in resp.json()["agents"] if a["name"] == "sample_agent")
    assert entry["description"] == "sample agent for agents api tests"
    assert entry["state"] == "running"
    assert isinstance(entry["heartbeat_ms"], int) and entry["heartbeat_ms"] > 0
    assert entry["is_alive"] is True
    assert entry["restart_count"] == 0
    # agents.toml 的 enabled 名单是封闭 Literal（生产三 Agent），替身名不在其中
    assert entry["enabled"] is False


# ==================== GET /api/v1/agents/{name}/state ====================


def test_agent_state_fields_complete(client: TestClient) -> None:
    resp = client.get("/api/v1/agents/sample_agent/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "sample_agent"
    assert body["state"] == "running"
    assert isinstance(body["heartbeat_ms"], int)
    assert body["is_alive"] is True
    assert body["restart_count"] == 0


def test_agent_state_unknown_name_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/agents/ghost/state")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


# ==================== POST /api/v1/agents/{name}/control ====================


def test_pause_and_resume_need_no_confirm(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "pause"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["state"] == "paused"

    resp = client.get("/api/v1/agents/sample_agent/state")
    assert resp.json()["state"] == "paused"

    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "resume"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "running"


def test_shutdown_without_confirm_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "shutdown"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "confirm" in detail
    assert "高风险" in detail


def test_shutdown_with_confirm_succeeds(client: TestClient, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "shutdown", "confirm": True})
    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    assert agent.state == AgentState.STOPPED


def test_restart_without_confirm_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "restart"})
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"]


def test_restart_with_confirm_increments_restart_count(client: TestClient, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "restart", "confirm": True})
    assert resp.status_code == 202
    assert resp.json()["state"] == "running"

    state = client.get("/api/v1/agents/sample_agent/state").json()
    assert state["restart_count"] == 1
    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    assert agent.restart_count == 1
    assert agent.state == AgentState.RUNNING


def test_control_unknown_action_returns_400(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "explode"})
    assert resp.status_code == 400
    assert "未知控制动作" in resp.json()["detail"]


def test_control_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/ghost/control", json={"action": "pause"})
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


# ==================== POST /api/v1/agents/{name}/prompt ====================


def test_prompt_delivers_with_operator_source(client: TestClient, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/prompt", json={"content": "注意东侧的苦力怕"})
    assert resp.status_code == 200
    assert resp.json() == {"delivered": True}

    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    assert agent.received_prompts == [("注意东侧的苦力怕", "operator")], "REST 通道来源固定记 operator"


def test_prompt_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/ghost/prompt", json={"content": "在吗"})
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_prompt_refusal_returns_409(client: TestClient, manager: AgentManager) -> None:
    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    agent._accept_prompt = False  # 模拟拒收路径（默认拒收 / 队列满）
    resp = client.post("/api/v1/agents/sample_agent/prompt", json={"content": "在吗"})
    assert resp.status_code == 409
    assert "拒收" in resp.json()["detail"]


# ==================== agent_control 未注入 ====================


def test_agents_endpoints_return_503_without_control(config_dir: Path) -> None:
    """agent_manager 未注入 → 列表端点 503（与 tools API 契约同形）。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
    )
    set_dashboard_server(server)
    try:
        tc = TestClient(create_app())
        assert tc.get("/api/v1/agents").status_code == 503
        assert tc.post("/api/v1/agents/x/control", json={"action": "pause"}).status_code == 503
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


# ==================== POST /api/v1/agents/{name}/tasks/{task_id}/cancel ====================


def test_cancel_task_succeeds(client: TestClient, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/tasks/deleg_1/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True}

    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    assert agent.cancelled_tasks == ["deleg_1"]


def test_cancel_task_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/ghost/tasks/deleg_1/cancel")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_cancel_task_unknown_or_terminal_returns_404(client: TestClient, manager: AgentManager) -> None:
    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    agent._accept_cancel = False  # 模拟任务不在追踪（未知号/已终态移除）
    resp = client.post("/api/v1/agents/sample_agent/tasks/deleg_gone/cancel")
    assert resp.status_code == 404
    assert "deleg_gone" in resp.json()["detail"]


# ==================== POST /api/v1/agents/{name}/delegate ====================


def test_delegate_registers_ledger_and_delivers(client: TestClient, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/delegate", json={"instruction": "去挖矿"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["executor"] == "sample_agent"
    task_id = body["task_id"]

    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    assert agent.delegated == [(task_id, "去挖矿")]

    ledger = _current_task_ledger()
    record = ledger.get(task_id)
    assert record is not None
    assert record.initiator == "operator" and record.executor == "sample_agent"
    assert record.snapshot["instruction"] == "去挖矿"


def test_delegate_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.post("/api/v1/agents/ghost/delegate", json={"instruction": "去挖矿"})
    assert resp.status_code == 404


def test_delegate_refusal_returns_409(client: TestClient, manager: AgentManager) -> None:
    agent = manager.get_agent_by_name("sample_agent")
    assert agent is not None
    agent._accept_delegate = False
    resp = client.post("/api/v1/agents/sample_agent/delegate", json={"instruction": "去挖矿"})
    assert resp.status_code == 409
    assert "拒收" in resp.json()["detail"]
    assert _current_task_ledger().active_task_ids() == [], "拒收不登记账本"
