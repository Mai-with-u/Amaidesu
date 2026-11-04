"""BackgroundMaintainer 单元测试——一句话话题摘要

- ``_parse_summary``：纯文本 / 代码围栏 / 旧 JSON 契约兼容 / 只取首行 / 截长
- ``_summarize_topic``：弹幕 + 时间窗 SC 进摘要输入；摘要写进 RoomState；
  不再从摘要循环里提取观众事实，也不再投递画像任务

主播自己的记忆（场次回顾 / 观众画像）在收播时生成，测试见
``tests/agents/streamer/test_streamer_memory.py``。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.modules.storage.models.chat import LiveChatMessage, SuperChatRecord
from src.agents.streamer.config import StreamerBackgroundConfig


def _make_maintainer(
    *,
    llm_response: str = "观众在聊考试",
    llm_success: bool = True,
    danmaku_texts: Optional[List[str]] = None,
    sc_messages: Optional[List[str]] = None,
) -> tuple[BackgroundMaintainer, MagicMock]:
    """构造带 mock LLM / mock 仓储的 BackgroundMaintainer（摘要链路全通）。

    弹幕与 SC 内容可自定义；默认 2 条弹幕 + 1 条 SC（合计 3 条，满足
    摘要的最少观众发言下限）。
    """
    room_state = MagicMock()
    rs_snap = MagicMock()
    rs_snap.heat = "low"
    rs_snap.topics = []
    room_state.get_snapshot = MagicMock(return_value=rs_snap)
    room_state.last_message_ms = None

    llm = MagicMock()
    response = MagicMock()
    response.ok = llm_success
    response.success = llm_success
    response.content = llm_response
    llm.generate = AsyncMock(return_value=response)

    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")

    session_manager = MagicMock()
    session_manager.resolve_pk = AsyncMock(return_value=1)

    chat_repo = MagicMock()
    # 读面按真实契约模型返回（消费方取模型字段）
    texts = danmaku_texts if danmaku_texts is not None else ["下周要考试了", "复习不过来了"]
    chat_repo.list_recent_live_chat = AsyncMock(
        return_value=[
            LiveChatMessage(
                id=i + 1,
                live_session_id=1,
                timestamp_ms=100 + i,
                sender_role="viewer",
                message_type="danmaku",
                message_id=f"m{i + 1}",
                sender_name=f"观众{i}",
                sender_id=f"u{i}",
                platform="bilibili",
                content=text,
            )
            for i, text in enumerate(texts)
        ]
    )
    sc_texts = sc_messages if sc_messages is not None else ["加油"]
    chat_repo.list_super_chats_since = AsyncMock(
        return_value=[
            SuperChatRecord(
                id=i + 1,
                live_session_id=1,
                timestamp_ms=200 + i,
                message_id=f"s{i + 1}",
                user_name=f"观众乙{i}",
                user_id=f"u{i}",
                platform="bilibili",
                message=text,
            )
            for i, text in enumerate(sc_texts)
        ]
    )

    maintainer = BackgroundMaintainer(
        config=StreamerBackgroundConfig(summary_interval_ms=1000),
        room_state=room_state,
        llm_service=llm,
        chat_repo=chat_repo,
        session_manager=session_manager,
        prompt_manager=prompt_manager,
    )
    return maintainer, llm


class TestParseSummary:
    def test_plain_sentence(self) -> None:
        maintainer, _ = _make_maintainer()
        assert maintainer._parse_summary("观众在聊下周的考试") == "观众在聊下周的考试"

    def test_markdown_fence_and_quotes_stripped(self) -> None:
        maintainer, _ = _make_maintainer()
        assert maintainer._parse_summary("```\n「在聊新皮肤」\n```") == "在聊新皮肤"

    def test_legacy_json_contract_tolerated(self) -> None:
        """模型偶尔仍按旧契约输出 JSON：只取 summary 值，facts 忽略。"""
        maintainer, _ = _make_maintainer()
        raw = '{"summary": "在聊考试", "facts": [{"message_id": "m1", "fact": "x"}]}'
        assert maintainer._parse_summary(raw) == "在聊考试"

    def test_first_line_only_and_capped(self) -> None:
        maintainer, _ = _make_maintainer()
        assert maintainer._parse_summary("第一句\n第二句解释") == "第一句"
        assert len(maintainer._parse_summary("长" * 300)) == 80


class TestSummarizeTopic:
    @pytest.mark.asyncio
    async def test_summary_written_to_room_state(self) -> None:
        """摘要写进 RoomState；输入同时含弹幕与时间窗内的 SC。"""
        maintainer, llm = _make_maintainer(llm_response="观众在聊考试")
        await maintainer._summarize_topic(now_ms=10_000)

        maintainer._room_state.set_topic_summary.assert_called_with("观众在聊考试", now_ms=10_000)
        prompt = llm.generate.await_args.args[0]
        assert "下周要考试了" in prompt
        assert "发送 SC：加油" in prompt

    @pytest.mark.asyncio
    async def test_no_profile_task_enqueued(self) -> None:
        """摘要循环不再投递画像任务——常客印象改在收播时整理。"""
        maintainer, _ = _make_maintainer(llm_response='{"summary": "在聊考试", "facts": []}')
        await maintainer._summarize_topic(now_ms=10_000)
        assert maintainer._compress_queue.empty()

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_previous_summary(self) -> None:
        """LLM 失败 → 不写摘要、不推进摘要时刻（下轮重试）。"""
        maintainer, _ = _make_maintainer(llm_success=False)
        await maintainer._summarize_topic(now_ms=10_000)
        maintainer._room_state.set_topic_summary.assert_not_called()
        assert maintainer._last_summary_ms == 0


class TestSummaryRetryBackoff:
    """摘要失败退避：退避窗口内门控不放行，等待时长逐次翻倍封顶，成功后清零。"""

    @pytest.mark.asyncio
    async def test_failure_blocks_gate_until_retry_after(self) -> None:
        """失败一次后，退避窗口内不投递任务；窗口过后恢复放行。"""
        maintainer, _ = _make_maintainer(llm_success=False)
        await maintainer._summarize_topic(now_ms=10_000)
        assert maintainer._summary_retry_after_ms == 15_000

        await maintainer._maybe_summarize(now_ms=14_999)
        assert maintainer._compress_queue.empty()

        await maintainer._maybe_summarize(now_ms=15_000)
        assert maintainer._compress_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_backoff_doubles_and_caps(self) -> None:
        """连续失败时退避按 5→10→20→40→80→120 秒翻倍，之后封顶 120 秒。"""
        maintainer, _ = _make_maintainer(llm_success=False)
        expected_waits = [5_000, 10_000, 20_000, 40_000, 80_000, 120_000, 120_000]
        now_ms = 10_000
        for wait in expected_waits:
            await maintainer._summarize_topic(now_ms=now_ms)
            assert maintainer._summary_retry_after_ms == now_ms + wait
            now_ms = maintainer._summary_retry_after_ms

    @pytest.mark.asyncio
    async def test_success_resets_backoff(self) -> None:
        """一次成功后退避时刻与等待时长都清零，下次失败重新从基础值起算。"""
        maintainer, _ = _make_maintainer(llm_success=False)
        await maintainer._summarize_topic(now_ms=10_000)
        assert maintainer._summary_retry_backoff_ms == 10_000

        maintainer2, _ = _make_maintainer(llm_success=True)
        assert maintainer2._summary_retry_backoff_ms == 5_000
        await maintainer2._summarize_topic(now_ms=10_000)
        assert maintainer2._summary_retry_after_ms == 0
        assert maintainer2._summary_retry_backoff_ms == 5_000
        assert maintainer2._last_summary_ms == 10_000


class TestSummaryQueueDedup:
    """队列去重：已有待消费的摘要任务时，门控不重复入队。"""

    @pytest.mark.asyncio
    async def test_pending_task_prevents_duplicate_enqueue(self) -> None:
        maintainer, _ = _make_maintainer(llm_response="观众在聊考试")
        await maintainer._maybe_summarize(now_ms=10_000)
        assert maintainer._compress_queue.qsize() == 1
        assert maintainer._summary_pending is True

        await maintainer._maybe_summarize(now_ms=50_000)
        assert maintainer._compress_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_pending_cleared_after_consumption(self) -> None:
        """任务被压缩 worker 消费完复位待办标记，门控可再次放行。"""
        maintainer, _ = _make_maintainer(llm_response="观众在聊考试")
        await maintainer._maybe_summarize(now_ms=10_000)
        maintainer._running = True
        loop_task = asyncio.create_task(maintainer._compress_loop())
        try:
            while maintainer._summary_pending:
                await asyncio.sleep(0)
        finally:
            maintainer._running = False
            loop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop_task

        assert maintainer._compress_queue.empty()
        await maintainer._maybe_summarize(now_ms=50_000)
        assert maintainer._compress_queue.qsize() == 1


class TestSummaryMinMessages:
    """观众发言太少不调模型：弹幕 + 付费留言合计不足 3 条时保留原摘要。"""

    @pytest.mark.asyncio
    async def test_too_few_messages_skips_llm_and_keeps_summary(self) -> None:
        maintainer, llm = _make_maintainer(danmaku_texts=["第一条"], sc_messages=["好棒"])
        await maintainer._summarize_topic(now_ms=10_000)
        llm.generate.assert_not_called()
        maintainer._room_state.set_topic_summary.assert_not_called()

    @pytest.mark.asyncio
    async def test_minimum_reached_calls_llm(self) -> None:
        """1 条弹幕 + 2 条 SC 合计 3 条，达到下限，正常调用。"""
        maintainer, llm = _make_maintainer(
            llm_response="观众在聊考试",
            danmaku_texts=["第一条"],
            sc_messages=["好棒", "再来一个"],
        )
        await maintainer._summarize_topic(now_ms=10_000)
        llm.generate.assert_awaited_once()
        maintainer._room_state.set_topic_summary.assert_called_with("观众在聊考试", now_ms=10_000)

    @pytest.mark.asyncio
    async def test_failure_backoff_not_armed_by_too_few_messages(self) -> None:
        """发言不足是正常跳过而非失败：不进入退避，下轮窗口照常触发。"""
        maintainer, _ = _make_maintainer(danmaku_texts=["仅有一条"], sc_messages=[])
        await maintainer._summarize_topic(now_ms=10_000)
        assert maintainer._summary_retry_after_ms == 0
        await maintainer._maybe_summarize(now_ms=11_000)
        assert maintainer._compress_queue.qsize() == 1
