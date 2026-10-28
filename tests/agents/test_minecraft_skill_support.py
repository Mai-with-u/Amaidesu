"""本文件测 SkillSupport（MaiCraft 技能世界映射进 Agent 上下文的支撑件）的核心行为：
能力清单按连接只读一次、开局代读资料包、技能目录段与技能正文的读取判定。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from src.agents.minecraft.skill_support import SkillSupport
from src.agents.minecraft.state import MinecraftAgentState
from src.modules.logging import get_logger
from src.modules.skills import SkillLibrary


def _support(
    skills: Optional[SkillLibrary],
    ready: bool = True,
    client: Optional[Any] = None,
) -> Tuple[SkillSupport, List[Tuple[str, Dict[str, Any]]], List[Tuple[str, Dict[str, Any], Dict[str, Any]]]]:
    """搭一个最小支撑件：工具执行与回复吸收都记进列表，返回 (支撑件, 执行记录, 吸收记录)。"""
    executed: List[Tuple[str, Dict[str, Any]]] = []
    absorbed: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []

    async def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        executed.append((name, arguments))
        if name == "maicraft_lookup":
            return {"ok": True, "data": {"abilities": [{"ability": "maicraft:gather"}]}}
        return {"ok": True, "data": {"echo": name}}

    support = SkillSupport(
        skills=skills,
        agent_name="minecraft",
        state=MinecraftAgentState(),
        logger=get_logger("test_skill_support"),
        maicraft_ready=lambda: ready,
        mcp_client=lambda: client,
        execute_tool=execute_tool,
        absorb_reply=lambda name, arguments, observation: absorbed.append((name, arguments, observation)),
    )
    return support, executed, absorbed


async def test_ability_listing_is_read_once_per_connection() -> None:
    """能力清单每次连接只读一次：读到了就缓存复用，未连接不读，读失败下次再试。"""
    support, executed, _absorbed = _support(None)

    await support.ensure_ability_listing()
    await support.ensure_ability_listing()
    assert len(executed) == 1, "第二次开批复用缓存，不再读"

    support.reset_connection_cache()
    support_not_ready, executed_not_ready, _absorbed2 = _support(None, ready=False)
    await support_not_ready.ensure_ability_listing()
    assert executed_not_ready == [], "没连上不读"
    await support.ensure_ability_listing()
    assert len(executed) == 2, "作废缓存后重读"


async def test_opening_bundle_reads_self_scene_abilities_and_goals_and_is_absorbed() -> None:
    """开局代读：自己、周围、能力清单、手上的目标各读一项并逐项吸收，拼成一条宿主消息放在末尾。"""
    support, executed, absorbed = _support(None)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": "去砍点木头"}]

    await support.append_opening_bundle(messages)

    assert [name for name, _arguments in executed] == [
        "maicraft_observe",
        "maicraft_observe",
        "maicraft_lookup",
        "maicraft_goal",
    ]
    assert len(absorbed) == 4, "每项读完先吸收进目标跟踪状态"
    opening = messages[-1]
    assert opening["role"] == "user" and len(messages) == 2, "开局资料是一条紧跟任务指令的宿主消息"


async def test_opening_bundle_is_skipped_while_disconnected_but_notebook_still_read() -> None:
    """没连上时代读的现场四项不读；有工作笔记时笔记这一项照常带上。"""
    support, executed, _absorbed = _support(None, ready=False)
    support._state.notebook = "家门口有条河"
    messages: List[Dict[str, Any]] = [{"role": "user", "content": "去砍点木头"}]

    await support.append_opening_bundle(messages)

    assert [name for name, _arguments in executed] == ["minecraft_notebook"], "只读笔记，现场四项跳过"


def test_catalog_section_is_omitted_without_a_skill_library() -> None:
    """没有技能库时目录段整段省略，系统提示词不留空段。"""
    support, _executed, _absorbed = _support(None)
    assert support.catalog_section() == ""


async def test_read_skill_serves_local_skills_from_the_library(tmp_path: Path) -> None:
    """minecraft_skill 读本地技能：按名读正文（去掉 frontmatter），环境里带 v1 与能力清单。"""
    (tmp_path / "chop_trees.md").write_text(
        "---\nname: chop_trees\ndescription: chop guide\nagents: [minecraft]\ncategory: survival\n---\nbody-of-chop_trees\n",
        encoding="utf-8",
    )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    support, _executed, _absorbed = _support(library)
    await support.ensure_ability_listing()

    result = await support.read_skill("chop_trees")

    assert result["success"] is True
    assert result["content"] == "body-of-chop_trees"
    missing = await support.read_skill("no_such_skill")
    assert missing["success"] is False, "目录里没有的技能如实报不可用"


async def test_remote_skills_are_registered_from_the_client_and_dropped_when_disconnected() -> None:
    """远端玩法技能：目录里只收 skill:// 地址的 SKILL.md，按地址能读到正文；连接断了就撤下。"""
    library = SkillLibrary()

    async def list_resources() -> List[Any]:
        return [
            SimpleNamespace(
                uri="skill://survival/survival-opening/SKILL.md",
                name="survival-opening",
                description="生存开局",
                mimeType="text/markdown",
            )
        ]

    async def read_resource(uri: str) -> Any:
        return SimpleNamespace(
            contents=[
                SimpleNamespace(uri=uri, mimeType="text/markdown", text="---\nname: survival-opening\n---\nremote-body")
            ]
        )

    client = SimpleNamespace(list_resources=list_resources, read_resource=read_resource)
    support, _executed, _absorbed = _support(library, client=client)

    await support.ensure_remote_skills()
    catalog = [entry.name for entry in library.catalog("minecraft")]
    assert catalog == ["survival-opening"], "远端技能进了目录"

    read = await support.read_skill("survival-opening")
    assert read["success"] is True and read["content"] == "remote-body" and read["source"] == "MaiCraft"

    support.drop_remote_if_disconnected()
    assert [entry.name for entry in library.catalog("minecraft")] == ["survival-opening"], "连接还就绪时不撤下"

    support_down, _executed2, _absorbed3 = _support(library, ready=False, client=client)
    support_down._remote_skills_loaded = True
    support_down.drop_remote_if_disconnected()
    assert [entry.name for entry in library.catalog("minecraft")] == [], "连接断了远端技能撤下"
