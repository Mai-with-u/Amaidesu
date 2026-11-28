"""MinecraftAgent 自建可读写/可修订技能卡（minecraft_skill）与短窗防重读测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

from src.agents.minecraft.skill_support import (
    LEARNED_SKILL_MAX_CONTENT_CHARS,
    LEARNED_SKILL_MAX_PER_SCOPE,
    SkillSupport,
)
from src.agents.minecraft.state import MinecraftAgentState
from src.modules.logging import get_logger
from src.modules.llm.payload import Response
from src.modules.skills import RemoteSkill, SkillLibrary
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _wait_until,
    make_minecraft_agent,
)
from tests.helpers import FakeClock


def _make_skill_library(tmp_path: Path) -> SkillLibrary:
    """构造含 1 篇内置只读技能与 1 篇远端只读技能的测试技能库。"""
    (tmp_path / "builtin_policy.md").write_text(
        "---\nname: builtin_policy\ndescription: 内置策略\nagents: [minecraft]\ncategory: streaming\n---\n内置策略正文内容\n",
        encoding="utf-8",
    )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    library.replace_remote(
        "MaiCraft",
        [
            RemoteSkill(
                name="remote_guide",
                description="随 Mod 下发的玩法指南",
                category="survival",
                uri="skill://survival/remote_guide/SKILL.md",
                agents=("minecraft",),
            )
        ],
    )
    return library


async def test_learned_skill_crud_limits_readonly_guard_and_reread_window(tmp_path: Path) -> None:
    """SkillSupport：upsert/patch/remove/list/read 全流程、只读保护、字数/条数上限与近 10 轮短窗防重读。"""
    clock = FakeClock(100_000)
    save_box = {"id": "save-alpha"}
    library = _make_skill_library(tmp_path)
    remote_reads: List[str] = []

    async def _read_resource(uri: str) -> List[Any]:
        remote_reads.append(uri)
        return [SimpleNamespace(uri=uri, mimeType="text/markdown", text="---\nname: remote_guide\n---\n远端技能正文")]

    support = SkillSupport(
        skills=library,
        agent_name="minecraft",
        state=MinecraftAgentState(),
        logger=get_logger("test_learned_skills"),
        maicraft_ready=lambda: True,
        mcp_client=lambda: SimpleNamespace(read_resource=_read_resource),
        execute_tool=lambda _n, _a: None,  # type: ignore[arg-type,return-value]
        absorb_reply=lambda _n, _a, _o: None,
        save_id_reader=lambda: save_box["id"],
        reread_guard_steps=10,
        clock_ms=clock.now_ms,
    )

    # 1) 禁止覆盖/修订/删除内置或远端只读技能
    for readonly_name in ("builtin_policy", "remote_guide"):
        up_res = await support.handle_skill_tool(
            {"action": "upsert", "name": readonly_name, "scope": "global", "trigger": "测试", "content": "尝试覆盖"}
        )
        assert up_res["success"] is False and "只读" in up_res["error"]
        rm_res = await support.handle_skill_tool({"action": "remove", "name": readonly_name})
        assert rm_res["success"] is False and "只读" in rm_res["error"]

    # 2) 单篇字数上限保护（1200 字）
    too_long = await support.handle_skill_tool(
        {
            "action": "upsert",
            "name": "auto_smelter",
            "scope": "world",
            "trigger": "营地批量烧炼矿石时",
            "content": "步" * (LEARNED_SKILL_MAX_CONTENT_CHARS + 1),
        }
    )
    assert too_long["success"] is False and str(LEARNED_SKILL_MAX_CONTENT_CHARS) in too_long["error"]

    # 3) 新建 world 与 global 技能，系统提示词紧凑目录只挂名称/scope/trigger 不灌正文
    created_world = await support.handle_skill_tool(
        {
            "action": "upsert",
            "name": "auto_smelter",
            "scope": "world",
            "trigger": "营地批量烧矿与圆石时",
            "content": "1. 把原料放进北侧进料箱\n2. 避免放入生食\n3. 从南侧出料箱取成品",
        }
    )
    assert created_world["success"] is True and created_world["created"] is True
    created_global = await support.handle_skill_tool(
        {
            "action": "upsert",
            "name": "create_waterwheel",
            "scope": "global",
            "trigger": "搭建水车提供机械动力时",
            "content": "1. 水流沿叶片顺向流过\n2. 接传动杆输出应力",
        }
    )
    assert created_global["success"] is True

    catalog = support.catalog_section()
    assert "`auto_smelter` [本服]：营地批量烧矿与圆石时" in catalog
    assert "`create_waterwheel` [通用]：搭建水车提供机械动力时" in catalog
    assert "把原料放进北侧进料箱" not in catalog, "系统提示词只挂紧凑目录，不灌正文"

    # 4) 向后兼容：不传 action 默认按 read 读取；同一篇在近 10 轮内重读命中短窗防重读
    first_read = await support.handle_skill_tool({"name": "auto_smelter"}, current_step=3)
    assert first_read["success"] is True
    assert "把原料放进北侧进料箱" in first_read["content"]
    assert first_read.get("cached_in_context") is not True

    reread_within_window = await support.handle_skill_tool({"name": "auto_smelter"}, current_step=8)
    assert reread_within_window["success"] is True
    assert reread_within_window["cached_in_context"] is True
    assert "minecraft_context_recall" in reread_within_window["note"]
    assert "content" not in reread_within_window, "短窗内重复 read 不得再次灌入全文"

    # 远端技能同样受短窗防重读保护：第 2 次不重复走 MCP read_resource
    remote_first = await support.handle_skill_tool({"name": "remote_guide"}, current_step=4)
    assert remote_first["success"] is True and remote_first["content"] == "远端技能正文"
    remote_second = await support.handle_skill_tool({"name": "remote_guide"}, current_step=9)
    assert remote_second["cached_in_context"] is True
    assert len(remote_reads) == 1

    # 上下文发生折叠后允许重新读回全文
    support.note_context_folded()
    after_fold = await support.handle_skill_tool({"name": "auto_smelter"}, current_step=9)
    assert after_fold["success"] is True and "把原料放进北侧进料箱" in after_fold["content"]

    # 超过 10 轮后也允许重新读回全文
    after_window = await support.handle_skill_tool({"name": "auto_smelter"}, current_step=25)
    assert after_window["success"] is True and "把原料放进北侧进料箱" in after_window["content"]

    # 5) patch 局部替换与追加，并自动失效旧防重读记录
    patched = await support.handle_skill_tool(
        {
            "action": "patch",
            "name": "auto_smelter",
            "old_text": "避免放入生食",
            "new_text": "切记配置排除食物标签",
        }
    )
    assert patched["success"] is True
    reread_after_patch = await support.handle_skill_tool({"name": "auto_smelter"}, current_step=26)
    assert "切记配置排除食物标签" in reread_after_patch["content"]

    appended = await support.handle_skill_tool(
        {"action": "patch", "name": "auto_smelter", "append": "4. 烧完顺手检查燃料箱"}
    )
    assert appended["success"] is True
    listed = await support.handle_skill_tool({"action": "list", "scope": "world"})
    assert listed["success"] is True and [s["name"] for s in listed["skills"]] == ["auto_smelter"]

    # 6) 切换存档：world 技能按存档隔离，global 技能跨存档可见
    save_box["id"] = "save-beta"
    beta_list = await support.handle_skill_tool({"action": "list"})
    beta_names = [item["name"] for item in beta_list["skills"]]
    assert "create_waterwheel" in beta_names
    assert "auto_smelter" not in beta_names

    # 7) 单域条数上限保护（20 条）
    for idx in range(LEARNED_SKILL_MAX_PER_SCOPE):
        res = await support.handle_skill_tool(
            {
                "action": "upsert",
                "name": f"world_skill_{idx}",
                "scope": "world",
                "trigger": f"场景 {idx}",
                "content": f"步骤 {idx}",
            }
        )
        assert res["success"] is True
    overflow = await support.handle_skill_tool(
        {
            "action": "upsert",
            "name": "world_skill_overflow",
            "scope": "world",
            "trigger": "超限场景",
            "content": "超限步骤",
        }
    )
    assert overflow["success"] is False and str(LEARNED_SKILL_MAX_PER_SCOPE) in overflow["error"]

    # 8) 删除自建技能后不再可见
    removed = await support.handle_skill_tool({"action": "remove", "name": "create_waterwheel", "scope": "global"})
    assert removed["success"] is True
    missing = await support.handle_skill_tool({"name": "create_waterwheel"}, current_step=30)
    assert missing["success"] is False


def _has_recognized_save(records: Iterable[Dict[str, Any]], save_name: str) -> bool:
    """这些日志里有没有「认出所在存档」这一行：Agent 把该存档的工作文档和自建技能全部加载完才写它。"""
    return any(f"认出所在存档：「{save_name}」" in record["message"] for record in records)


async def test_minecraft_agent_auto_derives_learned_skill_repo_from_work_docs_repo_and_persists(
    db_path: Path, tmp_path: Path, loguru_capture: Any
) -> None:
    """端到端：MinecraftAgent 仅注入 work_docs_repo 时自动派生 LearnedSkillRepo，跨重启持久化并防短窗重读。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        save_name = "持久化测试档"
        library = _make_skill_library(tmp_path)
        server = FakeMaicraft()
        server.enter_world("save-persisted", save_name)

        phase = {"step": 0}

        def script_first(messages: List[Dict[str, Any]]) -> Response:
            phase["step"] += 1
            if phase["step"] == 1:
                return _resp(
                    "把学到的加工站步骤记成自建技能卡",
                    calls=[
                        _call(
                            "minecraft_skill",
                            {
                                "action": "upsert",
                                "name": "camp_smelter_station",
                                "scope": "world",
                                "trigger": "营地自动烧矿时",
                                "content": "进料箱在西侧，出料箱在东侧，登记加工站后交给 obtain",
                            },
                            "c_upsert",
                        )
                    ],
                )
            if phase["step"] == 2:
                return _resp(
                    "首次读取刚写的技能确认内容",
                    calls=[_call("minecraft_skill", {"name": "camp_smelter_station"}, "c_read_1")],
                )
            if phase["step"] == 3:
                return _resp(
                    "紧接着重复读取同一篇技能验证短窗拦截",
                    calls=[_call("minecraft_skill", {"name": "camp_smelter_station"}, "c_read_2")],
                )
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "技能已记好"}, "c_done")])

        agent1, llm1, bus1 = await make_minecraft_agent(
            server,
            script_first,
            skills=library,
            work_docs_repo=db.game_work_docs,
        )
        try:
            # 新启动的实例靠后台事件循环读到的页面认出存档，认出后才加载这个存档的自建技能目录。
            # 不等它就走，写入本存档的技能可能被记到"还没认出存档"名下
            await _wait_until(lambda: _has_recognized_save(loguru_capture.records, save_name))
            agent1.receive_delegation(instruction="记录营地自动熔炉用法", task_id="d-skill-1")
            await _wait_until(lambda: len(_reports(bus1)) == 1)

            # 第 1 次 read 返回全文，第 2 次短窗内重复 read 返回 cached_in_context 简洁提示
            tool_msgs = [msg for msg in llm1.seen[-1] if msg.get("role") == "tool"]
            read1_msg = next(msg for msg in tool_msgs if msg.get("tool_call_id") == "c_read_1")
            read2_msg = next(msg for msg in tool_msgs if msg.get("tool_call_id") == "c_read_2")
            assert "进料箱在西侧" in str(read1_msg["content"])
            assert "cached_in_context" in str(read2_msg["content"])
            assert "进料箱在西侧" not in str(read2_msg["content"])
        finally:
            await agent1.stop()

        # 模拟重启：构造全新 MinecraftAgent 实例（同样只传 work_docs_repo），验证自建技能从库里恢复进目录并可读
        registry2 = ToolRegistry()
        logs_before_restart = len(loguru_capture.records)
        agent2, llm2, bus2 = await make_minecraft_agent(
            server,
            lambda _msgs: _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "重启验证完成"})]),
            registry=registry2,
            skills=library,
            work_docs_repo=db.game_work_docs,
        )
        try:
            # 经工具面读一次自己。这里直接调工具注册表，不经过 Agent 自己的工具执行入口，
            # 所以不会让 Agent 认出存档
            res = await registry2.invoke(
                ToolInvocation(
                    tool_name="maicraft_observe",
                    arguments={"what": "self"},
                    source="minecraft-react",
                    invoking_agent="minecraft",
                )
            )
            assert res.success is True
            # 重启后的实例同样要等它认出存档：认出之前自建技能目录里只有跨存档的通用技能，
            # 这时派活，第一次调模型拿到的系统提示词里就没有本存档的技能
            await _wait_until(lambda: _has_recognized_save(loguru_capture.records[logs_before_restart:], save_name))
            agent2.receive_delegation(instruction="核对技能目录", task_id="d-skill-2")
            await _wait_until(lambda: len(_reports(bus2)) == 1)

            # 重启后系统提示词目录直接包含跨重启持久化的自建技能
            system_prompt = str(llm2.seen[0][0]["content"])
            assert "camp_smelter_station" in system_prompt
            assert "营地自动烧矿时" in system_prompt
        finally:
            await agent2.stop()
    finally:
        await db.close()


async def test_refresh_learned_skills_on_save_switch_clears_reread_guard(db_path: Path, tmp_path: Path) -> None:
    """切换存档刷新自建技能时清空短窗防重读缓存，避免存档 A 读过的同名技能拦截存档 B 的首次读取。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        clock = FakeClock(1_795_000_000_000)
        save_box = {"id": "save-A"}
        library = _make_skill_library(tmp_path)
        support = SkillSupport(
            skills=library,
            agent_name="minecraft",
            state=MinecraftAgentState(),
            logger=get_logger("test_learned_skills_switch"),
            maicraft_ready=lambda: True,
            mcp_client=lambda: None,
            execute_tool=lambda _n, _a: None,  # type: ignore[arg-type,return-value]
            absorb_reply=lambda _n, _a, _o: None,
            learned_skill_repo=db.learned_skills,
            save_id_reader=lambda: save_box["id"],
            reread_guard_steps=10,
            clock_ms=clock.now_ms,
        )

        # 存档 A 写入同名 world 技能并在第 2 步读取一次
        await support.refresh_learned_skills("save-A")
        up_a = await support.handle_skill_tool(
            {
                "action": "upsert",
                "name": "camp_smelter",
                "scope": "world",
                "trigger": "存档A自动熔炉",
                "content": "存档A步骤：进料箱在北侧",
            }
        )
        assert up_a["success"] is True
        read_a = await support.handle_skill_tool({"name": "camp_smelter"}, current_step=2)
        assert read_a["success"] is True and "存档A步骤" in read_a["content"]

        # 切到存档 B 并预先写入同名 world 技能，紧接着第 3 步读取不得被存档 A 的短窗缓存拦截
        save_box["id"] = "save-B"
        await db.learned_skills.upsert_skill(
            game="minecraft",
            scope="world",
            save_id="save-B",
            name="camp_smelter",
            trigger="存档B自动熔炉",
            content="存档B步骤：进料箱在南侧",
            updated_at_ms=clock.now_ms(),
        )
        await support.refresh_learned_skills("save-B")
        read_b = await support.handle_skill_tool({"name": "camp_smelter"}, current_step=3)
        assert read_b["success"] is True
        assert read_b.get("cached_in_context") is not True
        assert "存档B步骤：进料箱在南侧" in read_b["content"]
    finally:
        await db.close()
