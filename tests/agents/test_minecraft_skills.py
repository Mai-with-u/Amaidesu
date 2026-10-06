"""MinecraftAgent 接入技能：目录进系统提示词、按已装模组筛选、minecraft_skill 读取正文"""

import json
from pathlib import Path
from textwrap import dedent
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.environment import ENVIRONMENT_URI, read_installed_mods
from src.modules.agents.factory import instantiate_agent
from src.modules.skills import SkillLibrary
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


def _library(tmp_path: Path) -> SkillLibrary:
    (tmp_path / "survival.md").write_text(
        dedent(
            """
            ---
            name: survival_opening
            description: 开局
            agents: [minecraft]
            category: survival
            ---
            先砍树
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "create.md").write_text(
        dedent(
            """
            ---
            name: create_power_network
            description: 动力网
            agents: [minecraft]
            category: create
            requires:
              mods: [create]
            ---
            先找动力
            """
        ).lstrip(),
        encoding="utf-8",
    )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    return library


def _environment_page(mods: dict[str, str], *, known: bool = True) -> dict[str, Any]:
    page = {"loader": "neoforge", "minecraft_version": "1.21.1", "mods_known": known, "mods": mods}
    return {"contents": [{"uri": ENVIRONMENT_URI, "mimeType": "application/json", "text": json.dumps(page)}]}


class _FakeClient:
    """只实现资源读取的 MCP 客户端替身，记录读取次数。"""

    def __init__(self, result: Optional[Any]) -> None:
        self.connected = True
        self.result = result
        self.reads: list[str] = []

    async def read_resource(self, uri: str) -> Optional[Any]:
        self.reads.append(uri)
        return self.result


def _agent(library: Optional[SkillLibrary], registry: Optional[ToolRegistry] = None) -> MinecraftAgent:
    return MinecraftAgent(MinecraftConfig(), event_bus=MagicMock(), tool_registry=registry, skill_library=library)


# ---------------------------------------------------------------------------
# 读取 Mod 报告的已装模组
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_installed_mods_from_environment_resource() -> None:
    client = _FakeClient(_environment_page({"create": "6.0.6", "minecraft": "1.21.1"}))

    mods = await read_installed_mods(client)

    assert mods == frozenset({"create", "minecraft"})
    assert client.reads == [ENVIRONMENT_URI]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,  # 旧版 Mod 没有该资源，客户端读取失败返回 None
        _environment_page({}, known=False),  # 加载器尚未登记：空清单不能当作“没装模组”
        {"contents": [{"text": "not json"}]},
        {"contents": []},
    ],
)
async def test_unreadable_environment_counts_as_unknown(result: Optional[Any]) -> None:
    assert await read_installed_mods(_FakeClient(result)) is None


# ---------------------------------------------------------------------------
# 工具注册与可见名单
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_tool_registered_for_minecraft_only_and_reads_body(tmp_path: Path) -> None:
    registry = ToolRegistry()
    agent = _agent(_library(tmp_path), registry)

    agent._register_tools()

    assert registry.visible_to_of("minecraft_skill") == ["minecraft"]
    assert "minecraft_skill" in {s.full_name for s in registry.list_tools(for_agent="minecraft")}
    assert "minecraft_skill" not in {s.full_name for s in registry.list_tools(for_agent="streamer")}
    result = await registry.invoke(
        ToolInvocation(tool_name="minecraft_skill", arguments={"name": "survival_opening"}, source="test")
    )
    assert result.success
    assert result.structured_content["content"] == "先砍树"


def test_without_skill_library_no_skill_tool_and_no_catalog() -> None:
    registry = ToolRegistry()
    agent = _agent(None, registry)

    agent._register_tools()

    assert "minecraft_skill" not in {s.full_name for s in registry.list_tools()}
    assert "## 技能" not in agent._system_prompt()


# ---------------------------------------------------------------------------
# 系统提示词里的技能目录随已装模组变化
# ---------------------------------------------------------------------------


def test_catalog_marks_mod_skill_pending_while_mods_unknown(tmp_path: Path) -> None:
    prompt = _agent(_library(tmp_path))._system_prompt()

    assert "## 技能" in prompt
    assert "`survival_opening`" in prompt
    assert "`create_power_network`：动力网（前提待确认 mods: create）" in prompt


@pytest.mark.asyncio
async def test_probe_drops_skill_for_missing_mod_and_rereads_after_recovery(tmp_path: Path) -> None:
    agent = _agent(_library(tmp_path))
    client = _FakeClient(_environment_page({"minecraft": "1.21.1"}))
    agent._mcp_client = client

    await agent._probe_installed_mods()
    await agent._probe_installed_mods()

    # 同一连接只读一次；确认没装 Create 后，动力网技能移出目录且读取给出原因
    assert client.reads == [ENVIRONMENT_URI]
    prompt = agent._system_prompt()
    assert "`survival_opening`" in prompt
    assert "create_power_network" not in prompt
    assert agent._read_skill("create_power_network")["missing"] == {"mods": ["create"]}

    # 连接恢复后游戏可能换了整合包：下一批重新读取
    agent._on_mcp_recovered()
    client.result = _environment_page({"create": "6.0.6"})
    await agent._probe_installed_mods()

    assert client.reads == [ENVIRONMENT_URI, ENVIRONMENT_URI]
    prompt = agent._system_prompt()
    assert "`create_power_network`：动力网" in prompt
    assert "动力网（前提待确认" not in prompt


@pytest.mark.asyncio
async def test_probe_waits_for_connection(tmp_path: Path) -> None:
    agent = _agent(_library(tmp_path))
    client = _FakeClient(_environment_page({"create": "6.0.6"}))
    client.connected = False
    agent._mcp_client = client

    await agent._probe_installed_mods()

    # 未连接时不消耗本次连接的读取机会，连上后再读
    assert client.reads == []
    client.connected = True
    await agent._probe_installed_mods()
    assert client.reads == [ENVIRONMENT_URI]


def test_factory_injects_repository_skill_library() -> None:
    agent = instantiate_agent(
        "minecraft",
        {},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )

    assert isinstance(agent, MinecraftAgent)
    assert agent._skills is not None
    assert "survival_opening" in agent._skills.list_skills()
