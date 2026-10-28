"""本文件测 MinecraftAgent 的远端玩法技能主题：skill:// 目录登记与按地址读正文、本地技能按能力清单筛选。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List


from src.modules.llm.payload import Response
from src.modules.skills import SkillLibrary

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _wait_until,
    make_minecraft_agent,
)


class _RemoteSkillsClient:
    """只实现资源接口的 MCP 客户端替身：目录一项，正文按地址返回。"""

    connected = True

    def __init__(self) -> None:
        self.reads: List[str] = []

    async def list_resources(self) -> List[Any]:
        return [
            SimpleNamespace(
                uri="skill://survival/survival-opening/SKILL.md",
                name="survival-opening",
                description="生存开局：从空手到有石器、食物、床和据点的稳定状态",
                mimeType="text/markdown",
            )
        ]

    async def read_resource(self, uri: str) -> Any:
        self.reads.append(uri)
        return SimpleNamespace(
            contents=[
                SimpleNamespace(
                    uri=uri,
                    mimeType="text/markdown",
                    text="---\nname: survival-opening\n---\n\nbody-of-survival-opening",
                )
            ]
        )


async def test_remote_skills_are_listed_and_read_by_uri() -> None:
    """玩法技能由 MaiCraft 随包发布：目录里标明来源，正文按 skill:// 地址读。"""
    server = FakeMaicraft()
    library = SkillLibrary()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(
            message.get("role") == "tool" and "body-of-survival-opening" in str(message.get("content"))
            for message in messages
        ):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "读过远程技能了"})])
        return _resp(calls=[_call("minecraft_skill", {"name": "survival-opening"})])

    # MCP 客户端换资源替身：resources/read 走客户端，工具调用仍经注册表里的替身 server
    client = _RemoteSkillsClient()
    agent, llm, bus = await make_minecraft_agent(server, script, skills=library, mcp_client=client)
    agent.receive_delegation(instruction="先看看生存开局", task_id="d-remote")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    system = str(llm.seen[0][0]["content"])
    assert "survival-opening" in system
    assert "来自 MaiCraft" in system
    assert client.reads == ["skill://survival/survival-opening/SKILL.md"]
    await agent.stop()


async def test_skill_catalog_follows_the_abilities_the_mod_has(tmp_path: Path) -> None:
    """本地技能仍按能力清单筛（直播策略一般不用，机制保留）：用到的能力都在才进目录，为旧版 Mod 写的不进；正文按名读。"""
    for name, requires in (
        ("chop_trees", "abilities: [maicraft:gather]"),
        ("make_tools", "abilities: [maicraft:obtain]"),
        ("old_machines", "maicraft: [v0]"),
    ):
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {name} guide\nagents: [minecraft]\ncategory: survival\n"
            f"requires:\n  {requires}\n---\nbody-of-{name}\n",
            encoding="utf-8",
        )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    server = FakeMaicraft()  # 替身的能力清单只有 gather 与 remember

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(
            message.get("role") == "tool" and "body-of-chop_trees" in str(message.get("content"))
            for message in messages
        ):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "读过砍树的打法了"})])
        return _resp(calls=[_call("minecraft_skill", {"name": "chop_trees"})])

    agent, llm, bus = await make_minecraft_agent(server, script, skills=library)
    agent.receive_delegation(instruction="先看看怎么砍树", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    system = str(llm.seen[0][0]["content"])
    assert "chop_trees" in system
    assert "make_tools" not in system, "Mod 没有 obtain，用到它的技能不进目录"
    assert "old_machines" not in system, "为旧版 Mod 写的技能不进目录"
    assert "minecraft_skill" in json.dumps(llm.tools_given[0]), "有技能库时提供按名读技能的工具"
    assert len(server.calls_to("lookup")) == 1, "能力清单每次连接只读一次，技能目录与开局资料共用"
    await agent.stop()
