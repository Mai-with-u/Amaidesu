"""流程单库 Dashboard API（/api/v1/rundowns*）：列表与模板、新建（服务端生成 id）与保存、
运行中写穿、热切流程单、删除、复制、启用

用内存仓储与鸭子类型 Agent 替身，不触真实 SQLite。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, update_config_values
from src.modules.config.service import ConfigService
from src.modules.storage.models.rundown import Rundown


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


class FakeSwitchAgent(FakeStreamerAgent):
    """带 switch_rundown 门面的替身：可编程切换结果，记录调用。"""

    def __init__(
        self,
        result: Tuple[bool, str, Optional[Dict[str, Any]]] = (True, "已切换至流程单「测试流程单」", None),
        raises: bool = False,
    ) -> None:
        super().__init__(running_id=None)
        self.switch_result = result
        self.switch_raises = raises
        self.switched: List[str] = []

    async def switch_rundown(self, rundown_id: str):
        self.switched.append(rundown_id)
        if self.switch_raises:
            raise RuntimeError("boom")
        return self.switch_result


class _FakeAgentManager:
    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        return self._agent if name == "streamer" else None


def _definition(rundown_id: str = "", *, dup_segment_ids: bool = False) -> Dict[str, Any]:
    """合法定义；rundown_id 传空 = 新建（服务端生成）；dup_segment_ids=True 制造重复环节 id。"""

    def segment(seg_id: str, title: str, expected_ms: int) -> Dict[str, Any]:
        return {
            "id": seg_id,
            "title": title,
            "task_description": f"目标 {title[-1]}",
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


def _seed(repo: FakeRundownRepo, rundown_id: str) -> None:
    """直接向仓储播种一份指定 id 的流程单（API 已无"指定 id 新建"入口）。"""
    repo._store[rundown_id] = Rundown.model_validate(_definition(rundown_id))


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


def test_list_degrades_without_repo_and_returns_definitions_with_current_id(make_client, repo: FakeRundownRepo) -> None:
    body = make_client(with_repo=False).get("/api/v1/rundowns").json()
    assert body["success"] is False and "存储未就绪" in body["message"]

    client = make_client()
    created = client.post("/api/v1/rundowns", json=_definition()).json()
    assert created["success"] is True and created["rundown_id"].startswith("rundown_")
    _seed(repo, "rd_a")
    body = client.get("/api/v1/rundowns").json()
    assert body["success"] is True and {r["rundown_id"] for r in body["rundowns"]} == {"rd_a", created["rundown_id"]}
    rd_a = next(r for r in body["rundowns"] if r["rundown_id"] == "rd_a")
    assert [s["id"] for s in rd_a["segments"]] == ["seg_a", "seg_b"]
    assert body["current_id"] == ""  # 基线配置未选单


def test_template_is_the_builtin_default_rundown(make_client) -> None:
    body = make_client().get("/api/v1/rundowns/template").json()
    assert body["success"] is True and body["definition"]["rundown_id"] == "default_first_stream"
    assert len(body["definition"]["segments"]) >= 1


def test_upsert_create_generates_id_and_rejects_duplicate_segment_ids(make_client, repo: FakeRundownRepo) -> None:
    client = make_client(FakeStreamerAgent(running_id=None))
    body = client.post("/api/v1/rundowns", json=_definition()).json()
    new_id = body["rundown_id"]
    assert (body["success"], new_id.startswith("rundown_")) == (True, True)
    assert repo._store[new_id].title == "测试流程单"
    assert [s.id for s in repo._store[new_id].segments] == ["seg_a", "seg_b"]

    bad = client.post("/api/v1/rundowns", json=_definition(dup_segment_ids=True)).json()
    assert bad["success"] is False and "不合法" in bad["message"]


def test_upsert_with_existing_id_saves_and_unknown_id_rejected(make_client, repo: FakeRundownRepo) -> None:
    """带 id = 保存这份单（id 必须已存在、不可变）；改 id 静默覆盖的路径被封死。"""
    client = make_client(FakeStreamerAgent(running_id=None))
    _seed(repo, "rd_a")
    ok = client.post("/api/v1/rundowns", json=_definition("rd_a")).json()
    assert (ok["success"], ok["rundown_id"]) == (True, "rd_a")
    assert repo._store["rd_a"].title == "测试流程单"

    ghost = client.post("/api/v1/rundowns", json=_definition("rd_ghost")).json()
    assert ghost["success"] is False and "流程单不存在，无法保存" in ghost["message"]
    assert "rd_ghost" not in repo._store


@pytest.mark.parametrize(("running_id", "message"), [("rd_a", "即时生效"), ("rd_other", "不受影响")])
def test_upsert_writes_through_to_running_agent(
    make_client, repo: FakeRundownRepo, running_id: str, message: str
) -> None:
    """门面总被调用，由门面自行判定异 id 不动运行态。"""
    agent = FakeStreamerAgent(running_id=running_id)
    client = make_client(agent)
    _seed(repo, "rd_a")
    body = client.post("/api/v1/rundowns", json=_definition("rd_a")).json()
    assert body["success"] is True and message in body["message"]
    assert [d["rundown_id"] for d in agent.applied] == ["rd_a"]


def test_delete_existing_missing_and_referenced_by_config(make_client, config_dir: Path, repo: FakeRundownRepo) -> None:
    client = make_client()
    _seed(repo, "rd_a")
    assert client.delete("/api/v1/rundowns/rd_a").json()["success"] is True and "rd_a" not in repo._store
    missing = client.delete("/api/v1/rundowns/rd_a").json()
    assert missing["success"] is False and "不存在" in missing["message"]

    update_config_values(config_dir, "agents.toml", {"agents.streamer.rundown_id": "rd_ref"})
    client = make_client()  # 重新构造使 main_config 带上落盘值
    _seed(repo, "rd_ref")
    body = client.delete("/api/v1/rundowns/rd_ref").json()
    assert body["success"] is True and "回退" in body["message"]


def test_duplicate_creates_traceable_copy(make_client, repo: FakeRundownRepo) -> None:
    client = make_client()
    _seed(repo, "rd_src")
    body = client.post("/api/v1/rundowns/rd_src/duplicate").json()
    assert body["success"] is True and body["rundown_id"].startswith("rd_src_copy_")
    copy = repo._store[body["rundown_id"]]
    assert copy.title == "测试流程单 副本" and [s.id for s in copy.segments] == ["seg_a", "seg_b"]
    assert client.post("/api/v1/rundowns/rd_ghost/duplicate").json()["success"] is False


def test_activate_degrades_to_config_only_without_online_agent(
    make_client, config_dir: Path, repo: FakeRundownRepo
) -> None:
    """Agent 不在线（无 switch 门面）：只写配置，message 说明启动后生效。"""
    client = make_client()
    _seed(repo, "rd_pick")
    body = client.post("/api/v1/rundowns/rd_pick/activate").json()
    assert body["success"] is True and "启动后生效" in body["message"]
    agents = tomllib.loads((config_dir / "agents.toml").read_text(encoding="utf-8-sig"))  # 写回器带 BOM
    assert agents["agents"]["streamer"]["rundown_id"] == "rd_pick"

    ghost = client.post("/api/v1/rundowns/rd_ghost/activate").json()
    assert ghost["success"] is False and "不存在" in ghost["message"]


def test_activate_hot_switches_online_agent(make_client, config_dir: Path, repo: FakeRundownRepo) -> None:
    client = make_client(FakeSwitchAgent())
    _seed(repo, "rd_pick")
    body = client.post("/api/v1/rundowns/rd_pick/activate").json()
    assert body["success"] is True and "已切换至流程单" in body["message"]
    agents = tomllib.loads((config_dir / "agents.toml").read_text(encoding="utf-8-sig"))
    assert agents["agents"]["streamer"]["rundown_id"] == "rd_pick"


def test_activate_keeps_config_when_switch_fails(make_client, config_dir: Path, repo: FakeRundownRepo) -> None:
    """热切失败不回滚配置：显式告知"配置已指向，重启后生效"。"""
    agent = FakeSwitchAgent(result=(False, "读取流程单失败: boom", None))
    client = make_client(agent)
    _seed(repo, "rd_pick")
    body = client.post("/api/v1/rundowns/rd_pick/activate").json()
    assert body["success"] is False and "配置已指向，重启主播 Agent 后生效" in body["message"]
    agents = tomllib.loads((config_dir / "agents.toml").read_text(encoding="utf-8-sig"))
    assert agents["agents"]["streamer"]["rundown_id"] == "rd_pick"


def test_activate_swallows_switch_exception(make_client, repo: FakeRundownRepo) -> None:
    agent = FakeSwitchAgent(raises=True)
    client = make_client(agent)
    _seed(repo, "rd_pick")
    body = client.post("/api/v1/rundowns/rd_pick/activate").json()
    assert body["success"] is False and "配置已指向" in body["message"]
