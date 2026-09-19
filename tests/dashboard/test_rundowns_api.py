"""流程单库 Dashboard API 测试套件

覆盖 ``/api/v1/rundowns*`` 六端点：
1. ``GET  /rundowns`` — repo 缺席降级 / 空库 / 列表含完整定义与 current_id。
2. ``GET  /rundowns/template`` — 返回内置默认流程单预填模板。
3. ``POST /rundowns`` — 新建落库 / 非法定义（环节 id 重复）拒绝 / 运行中写穿。
4. ``DELETE /rundowns/{id}`` — 删除 / 不存在 / 配置仍指向时的回退提示。
5. ``POST /rundowns/{id}/duplicate`` — 副本落库、新 id 可追溯源单。
6. ``POST /rundowns/{id}/activate`` — 配置落盘 / 不存在的 id 拒绝。

注：使用 Fake RundownRepo（内存字典）与 Fake Agent（鸭子类型），不触真实 SQLite。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake 仓储与 Agent
# ---------------------------------------------------------------------------


class FakeRundownRepo:
    """RundownRepo 的内存替身（upsert / get / list / delete 同语义）。"""

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}

    async def upsert_rundown(self, rundown: Any) -> None:
        self._store[rundown.rundown_id] = rundown

    async def get_rundown(self, rundown_id: str) -> Optional[Any]:
        return self._store.get(rundown_id)

    async def list_rundowns(self) -> List[Any]:
        return list(self._store.values())

    async def delete_rundown(self, rundown_id: str) -> bool:
        return self._store.pop(rundown_id, None) is not None


class FakeStreamerAgent:
    """仅实现 apply_rundown_definition 门面的 Agent 替身。"""

    def __init__(self, *, running_id: Optional[str] = "rd_a") -> None:
        self.running_id = running_id
        self.applied: List[Dict[str, Any]] = []

    def apply_rundown_definition_impl(self, definition: Dict[str, Any]):
        self.applied.append(definition)
        if self.running_id is not None and definition.get("rundown_id") != self.running_id:
            return True, "流程单已保存（当前直播运行的是其他流程单，不受影响）", None
        return True, "流程单已保存并即时生效", None

    async def apply_rundown_definition(self, definition: Dict[str, Any]):
        return self.apply_rundown_definition_impl(definition)


class _FakeAgentManager:
    """仅暴露 get_agent_by_name 的最小 manager 替身。"""

    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        if name == "streamer":
            return self._agent
        return None


# ---------------------------------------------------------------------------
# Fixtures 与素材
# ---------------------------------------------------------------------------


def _write_config(config_dir: Path) -> None:
    """六文件基线铺设（main_config 来源；activate 的落盘管线依赖）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(config_dir)


def _make_client(
    config_dir: Path,
    repo: Optional[FakeRundownRepo],
    agent: Optional[FakeStreamerAgent] = None,
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
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
        agent_manager=_FakeAgentManager(agent),  # type: ignore[arg-type]
        rundown_repo=repo,
    )
    set_dashboard_server(server)
    return TestClient(create_app())


def _make_definition(rundown_id: str = "rd_a", *, dup_segment_ids: bool = False) -> Dict[str, Any]:
    """构造合法定义；dup_segment_ids=True 时制造重复环节 id（业务校验拒绝路径）。"""
    seg_b_id = "seg_b" if not dup_segment_ids else "seg_a"
    return {
        "rundown_id": rundown_id,
        "title": "测试流程单",
        "segments": [
            {
                "id": "seg_a",
                "title": "环节 A",
                "task_description": "目标 A",
                "key_points": ["要点 1"],
                "expected_ms": 300_000,
                "min_duration_ms": None,
                "notes": None,
            },
            {
                "id": seg_b_id,
                "title": "环节 B",
                "task_description": "目标 B",
                "key_points": [],
                "expected_ms": 600_000,
                "min_duration_ms": None,
                "notes": None,
            },
        ],
    }


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    _write_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# GET /rundowns
# ---------------------------------------------------------------------------


def test_list_rundowns_repo_missing_degrades(config_dir: Path) -> None:
    client = _make_client(config_dir, repo=None)
    resp = client.get("/api/v1/rundowns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "存储未就绪" in body["message"]


def test_list_rundowns_returns_definitions_and_current_id(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo)
    assert client.post("/api/v1/rundowns", json=_make_definition("rd_a")).json()["success"]
    assert client.post("/api/v1/rundowns", json=_make_definition("rd_b")).json()["success"]

    resp = client.get("/api/v1/rundowns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert {r["rundown_id"] for r in body["rundowns"]} == {"rd_a", "rd_b"}
    rd_a = next(r for r in body["rundowns"] if r["rundown_id"] == "rd_a")
    assert [s["id"] for s in rd_a["segments"]] == ["seg_a", "seg_b"]
    # 基线配置未选单 → 空串
    assert body["current_id"] == ""


# ---------------------------------------------------------------------------
# GET /rundowns/template
# ---------------------------------------------------------------------------


def test_get_template_returns_default_rundown(config_dir: Path) -> None:
    client = _make_client(config_dir, repo=FakeRundownRepo())
    resp = client.get("/api/v1/rundowns/template")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    definition = body["definition"]
    assert definition is not None
    assert definition["rundown_id"] == "default_first_stream"
    assert len(definition["segments"]) >= 1


# ---------------------------------------------------------------------------
# POST /rundowns（upsert）
# ---------------------------------------------------------------------------


def test_upsert_creates_and_persists(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo, agent=FakeStreamerAgent(running_id=None))

    resp = client.post("/api/v1/rundowns", json=_make_definition("rd_new"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["rundown_id"] == "rd_new"

    stored = repo._store["rd_new"]
    assert stored.title == "测试流程单"
    assert [s.id for s in stored.segments] == ["seg_a", "seg_b"]


def test_upsert_duplicate_segment_ids_rejected(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo)

    resp = client.post("/api/v1/rundowns", json=_make_definition("rd_bad", dup_segment_ids=True))
    body = resp.json()
    assert body["success"] is False
    assert "不合法" in body["message"]
    assert "rd_bad" not in repo._store


def test_upsert_writes_through_to_running_agent(config_dir: Path) -> None:
    agent = FakeStreamerAgent(running_id="rd_a")
    client = _make_client(config_dir, repo=FakeRundownRepo(), agent=agent)

    resp = client.post("/api/v1/rundowns", json=_make_definition("rd_a"))
    body = resp.json()
    assert body["success"] is True
    assert "即时生效" in body["message"]
    assert len(agent.applied) == 1
    assert agent.applied[0]["rundown_id"] == "rd_a"


def test_upsert_other_running_rundown_keeps_runtime(config_dir: Path) -> None:
    agent = FakeStreamerAgent(running_id="rd_other")
    client = _make_client(config_dir, repo=FakeRundownRepo(), agent=agent)

    body = client.post("/api/v1/rundowns", json=_make_definition("rd_a")).json()
    assert body["success"] is True
    # 门面被调用（由门面自行判定异 id 不动运行态）
    assert len(agent.applied) == 1


# ---------------------------------------------------------------------------
# DELETE /rundowns/{id}
# ---------------------------------------------------------------------------


def test_delete_existing_and_missing(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo)
    client.post("/api/v1/rundowns", json=_make_definition("rd_a"))

    ok = client.delete("/api/v1/rundowns/rd_a").json()
    assert ok["success"] is True
    assert "rd_a" not in repo._store

    missing = client.delete("/api/v1/rundowns/rd_a").json()
    assert missing["success"] is False
    assert "不存在" in missing["message"]


def test_delete_referenced_by_config_mentions_fallback(config_dir: Path) -> None:
    from src.modules.config.multi_file_loader import update_config_values

    update_config_values(config_dir, "agents.toml", {"agents.streamer.rundown_id": "rd_ref"})
    # 配置落盘后需刷新 ConfigService 的 main_config（服务在 client 构造时初始化）
    client = _make_client(config_dir, repo=FakeRundownRepo())
    client.post("/api/v1/rundowns", json=_make_definition("rd_ref"))

    ok = client.delete("/api/v1/rundowns/rd_ref").json()
    assert ok["success"] is True
    assert "回退" in ok["message"]


# ---------------------------------------------------------------------------
# POST /rundowns/{id}/duplicate
# ---------------------------------------------------------------------------


def test_duplicate_creates_copy(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo)
    client.post("/api/v1/rundowns", json=_make_definition("rd_src"))

    body = client.post("/api/v1/rundowns/rd_src/duplicate").json()
    assert body["success"] is True
    new_id = body["rundown_id"]
    assert new_id is not None and new_id.startswith("rd_src_copy_")

    copy = repo._store[new_id]
    assert copy.title == "测试流程单 副本"
    assert [s.id for s in copy.segments] == ["seg_a", "seg_b"]

    missing = client.post("/api/v1/rundowns/rd_ghost/duplicate").json()
    assert missing["success"] is False


# ---------------------------------------------------------------------------
# POST /rundowns/{id}/activate
# ---------------------------------------------------------------------------


def test_activate_persists_config(config_dir: Path) -> None:
    repo = FakeRundownRepo()
    client = _make_client(config_dir, repo=repo)
    client.post("/api/v1/rundowns", json=_make_definition("rd_pick"))

    body = client.post("/api/v1/rundowns/rd_pick/activate").json()
    assert body["success"] is True

    # 写回的 agents.toml 带 UTF-8 BOM（写回器现状），读取用 utf-8-sig
    agents_cfg = tomllib.loads((config_dir / "agents.toml").read_text(encoding="utf-8-sig"))
    assert agents_cfg["agents"]["streamer"]["rundown_id"] == "rd_pick"


def test_activate_unknown_id_rejected(config_dir: Path) -> None:
    client = _make_client(config_dir, repo=FakeRundownRepo())
    body = client.post("/api/v1/rundowns/rd_ghost/activate").json()
    assert body["success"] is False
    assert "不存在" in body["message"]
