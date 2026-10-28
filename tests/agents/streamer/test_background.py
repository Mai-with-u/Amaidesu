"""BackgroundMaintainer 单元测试——一句话话题摘要

- ``_parse_summary``：纯文本 / 代码围栏 / 旧 JSON 契约兼容 / 只取首行 / 截长
- ``_summarize_topic``：弹幕 + 时间窗 SC 进摘要输入；摘要写进 RoomState；
  不再从摘要循环里提取观众事实，也不再投递画像任务

主播自己的记忆（场次回顾 / 观众画像）在收播时生成，测试见
``tests/agents/streamer/test_streamer_memory.py``。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.modules.storage.models.chat import LiveChatMessage, SuperChatRecord
from src.agents.streamer.config import StreamerBackgroundConfig


def _make_maintainer(*, llm_response: str = "", llm_success: bool = True) -> tuple[BackgroundMaintainer, MagicMock]:
    """构造带 mock LLM / mock 仓储的 BackgroundMaintainer（摘要链路全通）。"""
    room_state = MagicMock()
    rs_snap = MagicMock()
    rs_snap.heat = "low"
    rs_snap.topics = []
    room_state.get_snapshot = MagicMock(return_value=rs_snap)

    llm = MagicMock()
    response = MagicMock()
    response.success = llm_success
    response.content = llm_response
    llm.generate = AsyncMock(return_value=response)

    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")

    session_manager = MagicMock()
    session_manager.resolve_pk = AsyncMock(return_value=1)

    chat_repo = MagicMock()
    # 读面按真实契约模型返回（消费方取模型字段）
    chat_repo.list_recent_live_chat = AsyncMock(
        return_value=[
            LiveChatMessage(
                id=1,
                live_session_id=1,
                timestamp_ms=100,
                sender_role="viewer",
                message_type="danmaku",
                message_id="m1",
                sender_name="观众甲",
                sender_id="u1",
                platform="bilibili",
                content="下周要考试了",
            )
        ]
    )
    chat_repo.list_super_chats_since = AsyncMock(
        return_value=[
            SuperChatRecord(
                id=1,
                live_session_id=1,
                timestamp_ms=200,
                message_id="s1",
                user_name="观众乙",
                user_id="u2",
                platform="bilibili",
                message="加油",
            )
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
