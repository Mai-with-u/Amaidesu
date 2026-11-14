"""Skills API（/api/v1/skills）测试：目录快照（本地+远端）、远端登记状态降级、本地正文预览

技能库用受控实例经 monkeypatch 注入（生产走全局单例，测试不依赖仓库真实技能文档）；
AgentManager 用真实实例 + 工厂替身产出 minecraft Agent，与 test_agents_api.py 同一基建。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import AsyncGenerator, Iterable, Optional

import pytest

from src.modules.agents import AgentManager, AgentState, BaseAgent
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.skills import RemoteSkill, SkillLibrary
from src.modules.tools.models import ToolSpec


def _build_library(tmp_path: Path, *, with_remote: bool) -> SkillLibrary:
    """受控技能库：一份本地文档，按需登记一项 MaiCraft 远端技能。"""
    doc = tmp_path / "streaming" / "streaming_policy.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        dedent(
            """
            ---
            name: streaming_policy
            description: 直播期间的言行与上报策略
            agents: [minecraft]
            category: streaming
            ---

            # streaming_policy

            直播策略正文
            """
        ).lstrip(),
        encoding="utf-8",
    )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    if with_remote:
        library.replace_remote(
            "MaiCraft",
            [
                RemoteSkill(
                    name="maid_getting_started",
                    description="女仆入门做法",
                    category="gameplay",
                    uri="skill://gameplay/maid_getting_started/SKILL.md",
                )
            ],
        )
    return library


class _MinecraftStub(BaseAgent):
    name = "minecraft"
    description = "minecraft agent stub for skills api tests"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.remote_loaded = True
        self.read_result: dict = {"success": True, "content": ""}

    def remote_skill_status(self) -> dict:
        return {"loaded": self.remote_loaded}

    async def read_skill_body(self, name: str) -> dict:
        return self.read_result

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        return True

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        return None

    def cancel_task(self, task_id: str, source: str = "") -> bool:
        return True

    async def stop_actions(self, reason: str = "", *, cancel_delegations: bool = False) -> bool:
        return True

    async def _on_start(self) -> None:
        return None

    async def _on_stop(self) -> None:
        return None


@pytest.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AgentManager, None]:
    """真实 AgentManager；工厂按名产出 _MinecraftStub（绕过生产三类重依赖）。"""

    def _fake_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        # 心跳关闭：TestClient 与 pytest-asyncio 分属两个事件循环，心跳任务跨循环取消会抛 RuntimeError
        return _MinecraftStub(heartbeat_interval_ms=0) if name == "minecraft" else None

    monkeypatch.setattr("src.modules.agents.factory.instantiate_agent", _fake_instantiate)
    mgr = AgentManager(supervisor_config=AgentSupervisorConfig(check_interval_ms=0))
    assert await mgr.enable_agent("minecraft") is True
    yield mgr
    await mgr.stop_supervisor()
    for name in mgr.list_agents():
        agent = mgr.get_agent_by_name(name)
        if agent is not None and agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
            await agent.stop()


def _mount_with_library(mount, monkeypatch: pytest.MonkeyPatch, library: SkillLibrary, **components):
    monkeypatch.setattr("src.modules.dashboard.api.skills.get_skill_library", lambda: library)
    return mount(**components)


def test_lists_local_and_remote_entries_with_sources(
    mount, initialized_config, manager: AgentManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = _build_library(tmp_path, with_remote=True)
    client = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config, agent_manager=manager)

    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    # 目录排序：分类在前（gameplay < streaming）
    assert [(entry["name"], entry["origin"]) for entry in data["entries"]] == [
        ("maid_getting_started", "MaiCraft"),
        ("streaming_policy", "local"),
    ]
    local = data["entries"][1]
    # 测试库在 tmp_path（不在 src/ 下），路径走原样回退；分隔符按平台归一后比对
    assert local["path"].replace("\\", "/").endswith("streaming/streaming_policy.md") and local["uri"] == ""
    assert local["agents"] == ["minecraft"]
    remote = data["entries"][0]
    assert remote["uri"] == "skill://gameplay/maid_getting_started/SKILL.md" and remote["path"] == ""

    status = data["remote"]
    assert status == {"observable": True, "agent_running": True, "remote_loaded": True, "remote_count": 1}


def test_remote_status_degrades_without_agent_manager(
    mount, initialized_config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = _build_library(tmp_path, with_remote=True)
    client = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config)

    data = client.get("/api/v1/skills").json()
    # AgentManager 未注入：状态不可观测，但目录里已登记的远端条目照实展示
    assert data["remote"] == {"observable": False, "agent_running": None, "remote_loaded": None, "remote_count": 1}


def test_remote_status_reflects_not_loaded_agent(
    mount, initialized_config, manager: AgentManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = _build_library(tmp_path, with_remote=False)
    client = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config, agent_manager=manager)
    manager.get_agent_by_name("minecraft").remote_loaded = False  # type: ignore[union-attr]

    status = client.get("/api/v1/skills").json()["remote"]
    assert status == {"observable": True, "agent_running": True, "remote_loaded": False, "remote_count": 0}


def test_local_body_preview(mount, initialized_config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    library = _build_library(tmp_path, with_remote=False)
    client = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config)

    resp = client.get("/api/v1/skills/streaming_policy/body")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "streaming_policy" and "直播策略正文" in body["content"]
    assert body["path"].replace("\\", "/").endswith("streaming/streaming_policy.md")
    assert body["source"] == "local"


def test_remote_body_proxied_through_agent(
    mount, initialized_config, manager: AgentManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """远端技能正文经游戏 Agent 代读（minecraft_skill 同通道），Agent 未运行时 503 附原因。"""
    library = _build_library(tmp_path, with_remote=True)
    client = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config, agent_manager=manager)
    stub = manager.get_agent_by_name("minecraft")
    assert stub is not None
    stub.read_result = {"success": True, "content": "远端技能正文"}  # type: ignore[union-attr]

    resp = client.get("/api/v1/skills/maid_getting_started/body")
    assert resp.status_code == 200
    assert resp.json() == {
        "name": "maid_getting_started",
        "path": "",
        "content": "远端技能正文",
        "source": "MaiCraft",
    }

    # 代读失败：把读取观察里的原因透出给页面
    stub.read_result = {"success": False, "error": "读取技能 'maid_getting_started' 失败：MaiCraft 没连上"}  # type: ignore[union-attr]
    failed = client.get("/api/v1/skills/maid_getting_started/body")
    assert failed.status_code == 503 and "没连上" in failed.json()["detail"]

    # AgentManager 未注入（Agent 不在运行）：技能存在但代读通道不在
    bare = _mount_with_library(mount, monkeypatch, library, config_service=initialized_config)
    unreachable = bare.get("/api/v1/skills/maid_getting_started/body")
    assert unreachable.status_code == 503 and "未运行" in unreachable.json()["detail"]

    ghost = client.get("/api/v1/skills/ghost_skill/body")
    assert ghost.status_code == 404 and "没有名为 ghost_skill 的技能" in ghost.json()["detail"]
