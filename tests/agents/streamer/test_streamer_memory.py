"""主播 Agent 的记忆接线：账本仓储接上时才注册回想工具，开播拼常驻简报。

旧的场次回顾 / 长期记忆 / 观众画像已停写停注入（阶段 4 删除了对应模块），这里只测账本这条线。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools import ToolRegistry
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-memory-"))
    yield td / "memory.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


def _prompts() -> Any:
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    return prompt_manager


async def _ended_session(store: SQLiteDatabase, *, started: int = 1_000, ended: int = 9_000, kind: str = "live") -> int:
    pk = await store.sessions.insert_live_session(
        stream_id="room", platform="bilibili", started_at_ms=started, kind=kind
    )
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


def _build_agent(
    store: SQLiteDatabase | None,
    llm: Any,
    tool_registry: ToolRegistry | None = None,
    *,
    session_manager: Any = None,
    **memory: Any,
) -> StreamerAgent:
    """装一个只接账本仓储的主播 Agent（旧三份记忆在本阶段已停写）。"""
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "background": {"enabled": False},
            "memory": memory,
        }
    )
    repos: dict[str, Any] = {}
    if store is not None:
        repos = {
            "chat_repo": store.chat,
            "sessions_repo": store.sessions,
            "topic_repo": store.topics,
            "viewer_repo": store.viewers,
            "event_repo": store.events,
            "commitments_repo": store.commitments,
            "archive_repo": store.archive,
        }
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=_prompts(),
        tool_registry=tool_registry,
        session_manager=session_manager,
        **repos,
    )


# ---------------------------------------------------------------------------
# 账本接线：回想工具与常驻简报
# ---------------------------------------------------------------------------


async def test_agent_registers_memory_recall_and_retired_tools_stay_gone(store: SQLiteDatabase) -> None:
    """阶段 3b 起只有一个记忆工具 memory_recall；旧的三个工具不再注册。"""
    registry = ToolRegistry()
    agent = _build_agent(store, FakeLLMManager(), tool_registry=registry)
    agent._register_tools()

    names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert "memory_recall" in names
    assert not names & {"memory_recall_session", "memory_recall_viewer", "streamer_notebook"}


async def test_refresh_brief_reads_previous_archive_and_pending_commitments(store: SQLiteDatabase) -> None:
    """常驻简报取上一场档案与不针对人、不挂游戏的未了结承诺。"""
    previous_pk = await _ended_session(store, started=1_000, ended=2_000)
    await store.archive.insert_line(
        live_session_id=previous_pk,
        text="Minecraft：水车搭好了",
        quote="水车搭好啦",
        evidence_chat_id=1,
    )
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT")
    # 对某位观众的、挂游戏的都不进常驻简报
    await store.commitments.insert_commitment(what="给 ta 盖房子", viewer_id=9)
    await store.commitments.insert_commitment(what="继续挖矿", game="Minecraft")

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()

    brief = agent.memory_brief_text
    assert "上一场：" in brief and "[档#" in brief and "水车搭好啦" in brief
    assert "下次接着炸 TNT" in brief
    assert "盖房子" not in brief and "继续挖矿" not in brief
    assert len(agent.memory_brief_commitment_ids) == 1


async def test_next_brief_reflects_operator_corrections(store: SQLiteDatabase) -> None:
    """运营者删一行档案、判一条承诺误记之后，下一场开播重拼的简报就少那几行。

    简报只依赖表快照、每场开播重拼一次：删掉的行在下一份简报里不再出现，没动的照旧在。
    """
    previous_pk = await _ended_session(store, started=1_000, ended=2_000)
    kept_line = await store.archive.insert_line(
        live_session_id=previous_pk, text="水车搭好了", quote="水车搭好啦", evidence_chat_id=1
    )
    deleted_line = await store.archive.insert_line(
        live_session_id=previous_pk, text="答应过画头像", quote="答应你画头像", evidence_chat_id=2
    )
    kept_commitment = await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT")
    voided_commitment = await store.commitments.insert_commitment(what="随口答应的", quote="回头再说")
    assert kept_line and deleted_line and kept_commitment and voided_commitment

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    first = agent.memory_brief_text
    assert "水车搭好啦" in first and "下次接着炸 TNT" in first
    assert "答应你画头像" in first and "回头再说" in first

    assert await store.archive.delete_line(line_id=deleted_line) is True
    assert await store.commitments.void_commitment(commitment_id=voided_commitment) is True
    await agent._refresh_memory_brief()

    second = agent.memory_brief_text
    assert "水车搭好啦" in second and "下次接着炸 TNT" in second, "没删的行照旧在"
    assert "答应你画头像" not in second and "回头再说" not in second, "删掉/判误记的行下一份简报不再出现"


async def test_brief_is_empty_without_ledger_material(store: SQLiteDatabase) -> None:
    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    assert agent.memory_brief_text == "" and agent.memory_brief_commitment_ids == ()
