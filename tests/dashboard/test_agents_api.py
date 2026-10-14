"""Agent 控制面 API（/api/v1/agents）：列表与状态、控制动作（高风险需 confirm）、递话、取消、直派

AgentManager 用真实实例 + monkeypatch 工厂产出轻量替身 Agent，与 tests/modules/agents/test_supervisor.py 同一基建。
"""

from __future__ import annotations

from typing import AsyncGenerator, Iterable, Optional

import pytest

from src.modules.agents import AgentManager, AgentState, BaseAgent
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.tools.models import ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


class _SampleAgent(BaseAgent):
    name = "sample_agent"
    description = "sample agent for agents api tests"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.accept_prompt = self.accept_cancel = self.accept_delegate = True
        self.received_prompts: list[tuple[str, str]] = []
        self.cancelled_tasks: list[str] = []
        self.delegated: list[tuple[str, str]] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        if self.accept_prompt:
            self.received_prompts.append((content, source))
        return self.accept_prompt

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        if not self.accept_delegate:
            return "忙不过来"
        self.delegated.append((task_id, instruction))
        return None

    def cancel_task(self, task_id: str, source: str = "") -> bool:
        if self.accept_cancel:
            self.cancelled_tasks.append(task_id)
        return self.accept_cancel

    async def _on_start(self) -> None:
        return None

    async def _on_stop(self) -> None:
        return None


@pytest.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AgentManager, None]:
    """真实 AgentManager；工厂按名产出 _SampleAgent（绕过生产三类重依赖）。"""

    def _fake_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        # 心跳关闭：TestClient 与 pytest-asyncio 分属两个事件循环，心跳任务跨循环取消会抛 RuntimeError
        return _SampleAgent(heartbeat_interval_ms=0) if name == "sample_agent" else None

    monkeypatch.setattr("src.modules.agents.factory.instantiate_agent", _fake_instantiate)
    mgr = AgentManager(supervisor_config=AgentSupervisorConfig(check_interval_ms=0))
    assert await mgr.enable_agent("sample_agent") is True
    yield mgr
    await mgr.stop_supervisor()
    for name in mgr.list_agents():
        agent = mgr.get_agent_by_name(name)
        if agent is not None and agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
            await agent.stop()


@pytest.fixture
def ledger() -> TaskLedger:
    return TaskLedger(event_bus=None)


@pytest.fixture
def client(mount, initialized_config, manager: AgentManager, ledger: TaskLedger):
    tracker = TaskTracker(ToolRegistry(), ledger)
    return mount(config_service=initialized_config, agent_manager=manager, task_tracker=tracker)


def _agent(manager: AgentManager) -> _SampleAgent:
    return manager.get_agent_by_name("sample_agent")  # type: ignore[return-value]


def test_list_and_state_expose_agent_fields(client) -> None:
    entry = next(a for a in client.get("/api/v1/agents").json()["agents"] if a["name"] == "sample_agent")
    assert (entry["description"], entry["state"]) == ("sample agent for agents api tests", "running")
    assert isinstance(entry["heartbeat_ms"], int) and entry["heartbeat_ms"] > 0
    assert (entry["is_alive"], entry["restart_count"]) == (True, 0)
    # agents.toml 的 enabled 名单是封闭 Literal（生产三 Agent），替身名不在其中
    assert entry["enabled"] is False

    state = client.get("/api/v1/agents/sample_agent/state").json()
    assert (state["name"], state["state"], state["is_alive"], state["restart_count"]) == (
        "sample_agent",
        "running",
        True,
        0,
    )
    ghost = client.get("/api/v1/agents/ghost/state")
    assert ghost.status_code == 404 and "ghost" in ghost.json()["detail"]


def test_pause_and_resume_need_no_confirm(client) -> None:
    body = client.post("/api/v1/agents/sample_agent/control", json={"action": "pause"}).json()
    assert (body["success"], body["state"]) == (True, "paused")
    assert client.get("/api/v1/agents/sample_agent/state").json()["state"] == "paused"
    assert client.post("/api/v1/agents/sample_agent/control", json={"action": "resume"}).json()["state"] == "running"


@pytest.mark.parametrize("action", ["shutdown", "restart"])
def test_high_risk_actions_require_confirm(client, action: str) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": action})
    assert resp.status_code == 400 and "confirm" in resp.json()["detail"] and "高风险" in resp.json()["detail"]


def test_confirmed_shutdown_stops_and_restart_rebuilds_with_count(client, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "restart", "confirm": True})
    assert (resp.status_code, resp.json()["state"]) == (202, "running")
    assert client.get("/api/v1/agents/sample_agent/state").json()["restart_count"] == 1
    assert (_agent(manager).restart_count, _agent(manager).state) == (1, AgentState.RUNNING)

    resp = client.post("/api/v1/agents/sample_agent/control", json={"action": "shutdown", "confirm": True})
    assert (resp.status_code, resp.json()["success"]) == (202, True)
    assert _agent(manager).state == AgentState.STOPPED


@pytest.mark.parametrize(
    ("path", "payload", "status", "detail"),
    [
        ("/api/v1/agents/sample_agent/control", {"action": "explode"}, 400, "未知控制动作"),
        ("/api/v1/agents/ghost/control", {"action": "pause"}, 404, "ghost"),
        ("/api/v1/agents/ghost/prompt", {"content": "在吗"}, 404, "ghost"),
        ("/api/v1/agents/ghost/tasks/deleg_1/cancel", None, 404, "ghost"),
        ("/api/v1/agents/ghost/delegate", {"instruction": "去挖矿"}, 404, ""),
    ],
)
def test_unknown_action_or_agent(client, path: str, payload, status: int, detail: str) -> None:
    resp = client.post(path, json=payload) if payload is not None else client.post(path)
    assert resp.status_code == status and detail in resp.json()["detail"]


def test_prompt_delivers_with_fixed_operator_source_and_refusal_is_409(client, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/prompt", json={"content": "注意东侧的苦力怕"})
    assert resp.json() == {"delivered": True}
    assert _agent(manager).received_prompts == [("注意东侧的苦力怕", "operator")]

    _agent(manager).accept_prompt = False
    refused = client.post("/api/v1/agents/sample_agent/prompt", json={"content": "在吗"})
    assert refused.status_code == 409 and "拒收" in refused.json()["detail"]


def test_cancel_task(client, manager: AgentManager) -> None:
    resp = client.post("/api/v1/agents/sample_agent/tasks/deleg_1/cancel")
    assert (resp.status_code, resp.json()) == (200, {"cancelled": True})
    assert _agent(manager).cancelled_tasks == ["deleg_1"]

    _agent(manager).accept_cancel = False  # 任务不在追踪（未知号/已终态移除）
    gone = client.post("/api/v1/agents/sample_agent/tasks/deleg_gone/cancel")
    assert gone.status_code == 404 and "deleg_gone" in gone.json()["detail"]


def test_delegate_registers_ledger_and_refusal_is_not_recorded(
    client, manager: AgentManager, ledger: TaskLedger
) -> None:
    body = client.post("/api/v1/agents/sample_agent/delegate", json={"instruction": "去挖矿"}).json()
    assert (body["accepted"], body["executor"]) == (True, "sample_agent")
    task_id = body["task_id"]
    assert _agent(manager).delegated == [(task_id, "去挖矿")]
    record = ledger.get(task_id)
    assert (record.initiator, record.executor, record.snapshot["instruction"]) == ("operator", "sample_agent", "去挖矿")

    _agent(manager).accept_delegate = False
    refused = client.post("/api/v1/agents/sample_agent/delegate", json={"instruction": "再去挖矿"})
    assert refused.status_code == 409 and "拒收" in refused.json()["detail"]
    assert ledger.active_task_ids() == [task_id], "拒收不登记账本"


def test_endpoints_return_503_without_agent_manager(mount, initialized_config) -> None:
    client = mount(config_service=initialized_config)
    assert client.get("/api/v1/agents").status_code == 503
    assert client.post("/api/v1/agents/x/control", json={"action": "pause"}).status_code == 503
