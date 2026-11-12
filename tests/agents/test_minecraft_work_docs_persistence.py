"""MinecraftAgent 工作笔记与待办按存档跨重启持久化、开局限长注入上一场笔记、笔记长度上限。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.agents.minecraft.state import LAST_SESSION_NOTEBOOK_MAX_CHARS, MAX_NOTEBOOK_CHARS
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


def _flatten_content(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


async def test_work_docs_persist_across_restart_per_save_and_inject_capped_last_session_note(
    db_path: Path,
) -> None:
    """跨重启保留各存档的笔记与待办；新任务开局以"记于上一场"限长注入，本场改写后转回当场笔记。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        # 第一场：在「麦窝」写一份超过开局截断线（1500 字，仍在 2000 字总上限内）的笔记与一条待办，
        # 再切到「新的世界」写另一份笔记，互不串味
        long_home_note = "【刷铁机方案】北侧平台高度 Y=72；" + ("关键尺寸核对无误；" * 160)
        assert LAST_SESSION_NOTEBOOK_MAX_CHARS < len(long_home_note) < MAX_NOTEBOOK_CHARS

        server1 = FakeMaicraft()
        server1.enter_world("save-home", "麦窝")
        rounds1 = iter(
            [
                _resp(
                    calls=[
                        _call(
                            "minecraft_todo",
                            {"action": "write", "todos": [{"content": "核对刷铁机高度", "status": "done"}]},
                            call_id="c-todo",
                        ),
                        _call(
                            "minecraft_notebook",
                            {"action": "write", "content": long_home_note},
                            call_id="c-nb",
                        ),
                        _call(
                            "minecraft_report",
                            {"kind": "delivery", "content": "刷铁机高度核对完毕"},
                            call_id="c-rep",
                        ),
                    ]
                )
            ]
        )
        agent1, llm1, bus1 = await make_minecraft_agent(
            server1, lambda _m: next(rounds1), work_docs_repo=db.game_work_docs
        )
        agent1.receive_delegation(instruction="核对刷铁机高度", task_id="d-s1")
        await _wait_until(lambda: len(_reports(bus1)) == 1 and not agent1.batch_active)

        # 切去另一个存档写另一份笔记，验证按存档隔离
        server1.enter_world("save-new", "新的世界")
        await _wait_until(lambda: agent1.memo_snapshot()["notebook"] == "")
        rounds1_new = iter(
            [
                _resp(
                    calls=[
                        _call("minecraft_notebook", {"action": "write", "content": "新的世界陨石坑在北边"}, "c-nb2"),
                        _call("minecraft_report", {"kind": "delivery", "content": "记下了"}, "c-rep2"),
                    ]
                )
            ]
        )
        llm1.script = lambda _m: next(rounds1_new)
        agent1.receive_delegation(instruction="记下新的世界特征", task_id="d-s1-b")
        await _wait_until(lambda: len(_reports(bus1)) == 2 and not agent1.batch_active)
        await agent1.stop()

        # 第二场（进程重启，全新 Agent 实例）：进「麦窝」，尚未派新活前 memo_snapshot 就能看到持久化的笔记与待办
        server2 = FakeMaicraft()
        server2.enter_world("save-home", "麦窝")
        rounds2 = iter(
            [
                _resp(
                    calls=[
                        _call("minecraft_notebook", {"action": "write", "content": "本场新笔记：刷铁机已投产"}, "c-w"),
                        _call("minecraft_report", {"kind": "delivery", "content": "投产确认完成"}, "c-r"),
                    ]
                ),
                _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "二次检查完成"}, "c-r2")]),
            ]
        )
        agent2, llm2, bus2 = await make_minecraft_agent(
            server2, lambda _m: next(rounds2), work_docs_repo=db.game_work_docs
        )
        await _wait_until(lambda: agent2.memo_snapshot()["notebook"] == long_home_note)
        assert agent2.memo_snapshot()["todos"] == [{"content": "核对刷铁机高度", "status": "done"}]
        assert "新的世界陨石坑" not in agent2.memo_snapshot()["notebook"]

        # 新任务开局：注入"上次在这个存档里的笔记（记于上一场...）"且限长截断
        agent2.receive_delegation(instruction="检查刷铁机投产情况", task_id="d-s2-1")
        await _wait_until(lambda: len(_reports(bus2)) == 1 and not agent2.batch_active)
        first_task_history = "\n".join(_flatten_content(m) for m in llm2.seen[0])
        assert "上次在这个存档里的笔记（记于上一场" in first_task_history
        assert "【刷铁机方案】北侧平台高度 Y=72" in first_task_history
        assert "已截断，完整内容可用 minecraft_notebook 读取" in first_task_history

        # 本场已经改写过笔记后，同场第二个新任务按本场笔记读取，不再标"记于上一场"
        agent2.receive_delegation(instruction="再看一眼产铁箱", task_id="d-s2-2")
        await _wait_until(lambda: len(_reports(bus2)) == 2 and not agent2.batch_active)
        second_task_history = "\n".join(_flatten_content(m) for m in llm2.seen[-1])
        assert "本场新笔记：刷铁机已投产" in second_task_history
        assert "记于上一场" not in second_task_history
        await agent2.stop()
    finally:
        await db.close()


async def test_notebook_write_enforces_max_length_without_overwriting_existing_notes() -> None:
    """笔记写入超过上限时返回明确错误提示模型精简，原笔记内容不被破坏。"""
    registry = ToolRegistry()
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, registry=registry)
    try:
        ok_res = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_notebook",
                arguments={"action": "write", "content": "原笔记：东侧矿洞入口 (-120, 64, 30)"},
                caller_profile="minecraft",
            )
        )
        assert ok_res.structured_content["success"] is True

        too_long = "超长流水账" * (MAX_NOTEBOOK_CHARS // 3)
        assert len(too_long) > MAX_NOTEBOOK_CHARS
        bad_res = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_notebook",
                arguments={"action": "write", "content": too_long},
                caller_profile="minecraft",
            )
        )
        assert bad_res.structured_content["success"] is False
        assert str(MAX_NOTEBOOK_CHARS) in bad_res.structured_content["error"]
        assert agent.memo_snapshot()["notebook"] == "原笔记：东侧矿洞入口 (-120, 64, 30)"
    finally:
        await agent.stop()
