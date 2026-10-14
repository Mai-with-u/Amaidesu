"""流程单库 Dashboard API（/api/v1/rundowns*）：列表与模板、新建与运行中写穿、删除、复制、启用

用内存仓储与鸭子类型 Agent 替身，不触真实 SQLite。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, update_config_values
from src.modules.config.service import ConfigService


class FakeRundownRepo:
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
    """仅实现 apply_rundown_definition 门面；异 id 时门面自行声明不动运行态。"""

    def __init__(self, running_id: Optional[str] = "rd_a") -> None:
        self.running_id = running_id
        self.applied: List[Dict[str, Any]] = []

    async def apply_rundown_definition(self, definition: Dict[str, Any]):
        self.applied.append(definition)
        if self.running_id is not None and definition.get("rundown_id") != self.running_id:
            return True, "流程单已保存（当前直播运行的是其他流程单，不受影响）", None
        return True, "流程单已保存并即时生效", None


class _FakeAgentManager:
    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        return self._agent if name == "streamer" else None


def _definition(rundown_id: str = "rd_a", *, dup_segment_ids: bool = False) -> Dict[str, Any]:
    """合法定义；dup_segment_ids=True 制造重复环节 id（业务校验拒绝路径）。"""

    def segment(seg_id: str, title: str, expected_ms: int) -> Dict[str, Any]:
        return {
            "id": seg_id,
            "title": title,
            "task_description": f"目标 {title[-1]}",
            "key_points": [],
            "expected_ms": expected_ms,
            "min_duration_ms": None,
            "notes": None,
        }

    return {
        "rundown_id": rundown_id,
        "title": "测试流程单",
        "segments": [
            segment("seg_a", "环节 A", 300_000),
            segment("seg_a" if dup_segment_ids else "seg_b", "环节 B", 600_000),
        ],
    }


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    generate_default_configs(cfg)
    return cfg


@pytest.fixture
def repo() -> FakeRundownRepo:
    return FakeRundownRepo()


@pytest.fixture
def make_client(mount, config_dir: Path, repo: FakeRundownRepo):
    """配置落盘后再构造 ConfigService，使 main_config 带上落盘值。"""

    def _make(agent: Optional[FakeStreamerAgent] = None, with_repo: bool = True):
        service = ConfigService(base_dir=str(config_dir.parent))
        service.initialize()
        return mount(
            config_service=service,
            agent_manager=_FakeAgentManager(agent),
            rundown_repo=repo if with_repo else None,
        )

    return _make


def test_list_degrades_without_repo_and_returns_definitions_with_current_id(make_client) -> None:
    body = make_client(with_repo=False).get("/api/v1/rundowns").json()
    assert body["success"] is False and "存储未就绪" in body["message"]

    client = make_client()
    for rundown_id in ("rd_a", "rd_b"):
        assert client.post("/api/v1/rundowns", json=_definition(rundown_id)).json()["success"]
    body = client.get("/api/v1/rundowns").json()
    assert body["success"] is True and {r["rundown_id"] for r in body["rundowns"]} == {"rd_a", "rd_b"}
    rd_a = next(r for r in body["rundowns"] if r["rundown_id"] == "rd_a")
    assert [s["id"] for s in rd_a["segments"]] == ["seg_a", "seg_b"]
    assert body["current_id"] == ""  # 基线配置未选单


def test_template_is_the_builtin_default_rundown(make_client) -> None:
    body = make_client().get("/api/v1/rundowns/template").json()
    assert body["success"] is True and body["definition"]["rundown_id"] == "default_first_stream"
    assert len(body["definition"]["segments"]) >= 1


def test_upsert_persists_and_rejects_duplicate_segment_ids(make_client, repo: FakeRundownRepo) -> None:
    client = make_client(FakeStreamerAgent(running_id=None))
    body = client.post("/api/v1/rundowns", json=_definition("rd_new")).json()
    assert (body["success"], body["rundown_id"]) == (True, "rd_new")
    assert repo._store["rd_new"].title == "测试流程单"
    assert [s.id for s in repo._store["rd_new"].segments] == ["seg_a", "seg_b"]

    bad = client.post("/api/v1/rundowns", json=_definition("rd_bad", dup_segment_ids=True)).json()
    assert bad["success"] is False and "不合法" in bad["message"] and "rd_bad" not in repo._store


@pytest.mark.parametrize(("running_id", "message"), [("rd_a", "即时生效"), ("rd_other", "不受影响")])
def test_upsert_writes_through_to_running_agent(make_client, running_id: str, message: str) -> None:
    """门面总被调用，由门面自行判定异 id 不动运行态。"""
    agent = FakeStreamerAgent(running_id=running_id)
    body = make_client(agent).post("/api/v1/rundowns", json=_definition("rd_a")).json()
    assert body["success"] is True and message in body["message"]
    assert [d["rundown_id"] for d in agent.applied] == ["rd_a"]


def test_delete_existing_missing_and_referenced_by_config(make_client, config_dir: Path, repo) -> None:
    client = make_client()
    client.post("/api/v1/rundowns", json=_definition("rd_a"))
    assert client.delete("/api/v1/rundowns/rd_a").json()["success"] is True and "rd_a" not in repo._store
    missing = client.delete("/api/v1/rundowns/rd_a").json()
    assert missing["success"] is False and "不存在" in missing["message"]

    update_config_values(config_dir, "agents.toml", {"agents.streamer.rundown_id": "rd_ref"})
    client = make_client()  # 重新构造使 main_config 带上落盘值
    client.post("/api/v1/rundowns", json=_definition("rd_ref"))
    body = client.delete("/api/v1/rundowns/rd_ref").json()
    assert body["success"] is True and "回退" in body["message"]


def test_duplicate_creates_traceable_copy(make_client, repo: FakeRundownRepo) -> None:
    client = make_client()
    client.post("/api/v1/rundowns", json=_definition("rd_src"))
    body = client.post("/api/v1/rundowns/rd_src/duplicate").json()
    assert body["success"] is True and body["rundown_id"].startswith("rd_src_copy_")
    copy = repo._store[body["rundown_id"]]
    assert copy.title == "测试流程单 副本" and [s.id for s in copy.segments] == ["seg_a", "seg_b"]
    assert client.post("/api/v1/rundowns/rd_ghost/duplicate").json()["success"] is False


def test_activate_persists_choice_and_rejects_unknown_id(make_client, config_dir: Path) -> None:
    client = make_client()
    client.post("/api/v1/rundowns", json=_definition("rd_pick"))
    assert client.post("/api/v1/rundowns/rd_pick/activate").json()["success"] is True
    agents = tomllib.loads((config_dir / "agents.toml").read_text(encoding="utf-8-sig"))  # 写回器带 BOM
    assert agents["agents"]["streamer"]["rundown_id"] == "rd_pick"

    ghost = client.post("/api/v1/rundowns/rd_ghost/activate").json()
    assert ghost["success"] is False and "不存在" in ghost["message"]
