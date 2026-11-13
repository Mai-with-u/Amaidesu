"""下播整理断点续跑与切片排队（D2-5）回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.memory.settle import SessionSettler
from src.modules.background.registry import BackgroundTaskRegistry
from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.service import ClippingService
from src.modules.events.event_bus import EventBus
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.types.session_kind import SESSION_KIND_LIVE
from tests.agents.streamer.helpers import stub_prompt
from tests.modules.llm.fakes import FakeLLMManager


@pytest.mark.asyncio
async def test_settle_resumes_from_saved_checkpoints_and_clears_on_completion(tmp_path: Path) -> None:
    """前两步完成后第三步中断，重启续跑直接复用前两步断点（不重调 LLM），整场完成后清空断点；requeue 亦清断点。"""
    db = SQLiteDatabase(tmp_path / "ckpt.db")
    await db.initialize()
    try:
        # 铺第 1 场的一条未了结承诺，使第 2 场整理包含 archive + commitment + closure 三次调用
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        c1 = await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_010_000,
            sender_role="assistant",
            content="答应大家下次唱一首新歌",
            message_type="speech",
        )
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=4_000_000)
        await db.sessions.mark_settle_done(live_session_id=s1, settled_at_ms=4_010_000)
        comm_id = await db.commitments.insert_commitment(
            what="唱一首新歌",
            quote="答应大家下次唱一首新歌",
            evidence_chat_id=int(c1),
            created_session_id=s1,
            now_ms_value=1_010_000,
        )
        assert comm_id is not None

        s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)
        await db.chat.insert_live_chat(
            live_session_id=s2,
            timestamp_ms=10_050_000,
            sender_role="assistant",
            content="今天把答应大家的新歌唱完啦，还定了个直播间暗号叫早安麦",
            message_type="speech",
        )
        await db.sessions.close_live_session(live_session_id=s2, ended_at_ms=14_000_000)

        # 第一次运行：archive 与 commitment 成功，closure 失败
        llm_first = FakeLLMManager(
            responses=[
                Response(
                    success=True,
                    content=json.dumps(
                        {
                            "previous": [],
                            "archive": [
                                {
                                    "line": 1,
                                    "title": "定下直播间暗号早安麦",
                                    "quote": "还定了个直播间暗号叫早安麦",
                                    "game": None,
                                    "lasting": True,
                                }
                            ],
                            "viewer_candidates": [],
                        },
                        ensure_ascii=False,
                    ),
                ),
                Response(
                    success=True,
                    content=json.dumps(
                        {"commitments": [], "claimed_missing": [], "player_candidates": []},
                        ensure_ascii=False,
                    ),
                ),
                Response(success=False, error="模拟网络中断"),
            ]
        )
        settler_first = SessionSettler(
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            llm_service=llm_first,
            prompt_manager=stub_prompt(),
        )
        assert await settler_first.settle_session(s2) is False
        assert len(llm_first.calls) == 3
        assert await db.sessions.count_sessions_failed_settle(max_attempts=1) == 1

        # 核对 archive 与 commitment 断点均已落库，closure 未落库
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="archive") is not None
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="commitment") is not None
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="closure") is None

        # 第二次续跑（模拟重启后新构造的 SessionSettler）：只需提供第 3 笔 closure 的响应！
        llm_resume = FakeLLMManager(
            responses=[
                Response(
                    success=True,
                    content=json.dumps(
                        {
                            "closed": [
                                {
                                    "id": comm_id,
                                    "line": 1,
                                    "quote": "今天把答应大家的新歌唱完啦",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            ]
        )
        settler_resume = SessionSettler(
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            llm_service=llm_resume,
            prompt_manager=stub_prompt(),
        )
        assert await settler_resume.settle_session(s2) is True
        # 只调了 1 次模型（closure），前两步完全命中库内断点
        assert len(llm_resume.calls) == 1

        # 整场落库后断点已清除，档案行与承诺了结均已生效
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="archive") is None
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="commitment") is None
        lines = await db.archive.list_lines(live_session_id=s2)
        assert len(lines) == 1 and "早安麦" in lines[0].text
        closed_comm = await db.commitments.get_commitment(commitment_id=comm_id)
        assert closed_comm is not None and closed_comm.status == "closed"

        # 验证手工写一条残留断点后调 requeue_settlement 会一并清掉断点
        await db.sessions.save_settle_checkpoint(
            live_session_id=s2,
            step="archive",
            fingerprint="fp-old",
            output_json="{}",
            saved_at_ms=15_000_000,
        )
        assert await db.sessions.requeue_settlement(live_session_id=s2) is True
        assert await db.sessions.get_settle_checkpoint(live_session_id=s2, step="archive") is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_clipping_auto_select_waits_until_session_settle_finishes(tmp_path: Path) -> None:
    """下播切片自动选段排在该场记忆整理之后：在途 settle:{id} 未结束前不抢跑选段。"""
    db = SQLiteDatabase(tmp_path / "clip_wait.db")
    await db.initialize()
    bus = EventBus()
    try:
        sid = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        await db.sessions.close_live_session(live_session_id=sid, ended_at_ms=5_000_000)

        registry = BackgroundTaskRegistry(bus)
        await registry.begin(f"settle:{sid}", f"整理场次 #{sid}")

        rec_dir = tmp_path / "rec"
        rec_dir.mkdir()
        service = ClippingService(
            event_bus=bus,
            database=db,
            llm_manager=MagicMock(),
            config=ClippingConfigSchema(enabled=True, recording_dir=str(rec_dir)),
            task_registry=registry,
        )

        order: list[str] = []
        polled = asyncio.Event()

        async def _finish_settle_when_polled() -> None:
            await polled.wait()
            await db.sessions.mark_settle_done(live_session_id=sid, settled_at_ms=5_010_000)
            order.append("settle_done")
            await registry.finish(f"settle:{sid}", success=True)

        async def _run_waiter() -> None:
            polled.set()
            # 轮询间隔给到现实量级：过密的轮询会在对方协程的落库（过线程池执行器）完成前
            # 把礼让次数耗尽，负载下必现抢跑——测试意图是"会等"，不是"耗尽礼让也放行"
            await service.wait_for_session_settle(sid, poll_s=0.05, max_wait_s=5.0)
            order.append("clip_proceed")

        await asyncio.gather(_run_waiter(), _finish_settle_when_polled())
        assert order == ["settle_done", "clip_proceed"]
    finally:
        await bus.cleanup()
        await db.close()
