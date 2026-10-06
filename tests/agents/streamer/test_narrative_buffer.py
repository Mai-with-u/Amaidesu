"""叙事缓冲回放测试——讲过的旧遭遇过了保留期不再回放，新事实至少被看到一次。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.narrative import NarrativeBuffer
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload

_MINUTE = 60_000


def test_render_marks_new_then_old_and_keeps_format() -> None:
    """首次交出标"新"，再次交出按到达时间陈述；行格式与原叙事渲染一致。"""
    buffer = NarrativeBuffer(ttl_ms=5 * _MINUTE, max_items=10)
    buffer.add("[minecraft·attacked] 正在被僵尸攻击", now=0)

    first = buffer.render(now=5_000)
    second = buffer.render(now=10_000)

    assert first.text == "[新·刚刚] [minecraft·attacked] 正在被僵尸攻击"
    assert first.fresh == ("[minecraft·attacked] 正在被僵尸攻击",)
    assert second.text == "[刚刚] [minecraft·attacked] 正在被僵尸攻击"
    assert second.fresh == ()


def test_delivered_entry_drops_after_ttl() -> None:
    """交给过决策窗、到达超过保留期的遭遇不再回放——十分钟前的掉血不会被当新闻再播。"""
    buffer = NarrativeBuffer(ttl_ms=5 * _MINUTE, max_items=10)
    buffer.add("[minecraft·attacked] 掉血了", now=0)
    buffer.render(now=1_000)
    buffer.add("[minecraft·respawned] 重生了", now=4 * _MINUTE)

    view = buffer.render(now=6 * _MINUTE)

    assert "掉血了" not in view.text
    assert view.text == "[新·2 分钟前] [minecraft·respawned] 重生了"
    assert len(buffer) == 1


def test_undelivered_entry_survives_ttl() -> None:
    """没交给过决策窗的条目不论多久都保留：没开播期间到的遭遇开播后仍能看到一次。"""
    buffer = NarrativeBuffer(ttl_ms=5 * _MINUTE, max_items=10)
    buffer.add("[minecraft·died] 死了", now=0)

    first = buffer.render(now=30 * _MINUTE)
    second = buffer.render(now=30 * _MINUTE + 1_000)

    assert "死了" in first.text and first.fresh == ("[minecraft·died] 死了",)
    # 交出过一次且早已超过保留期：下一窗不再回放
    assert second.text == ""


def test_max_items_keeps_newest() -> None:
    """超出条数上限时丢最早的，最新的遭遇总在。"""
    buffer = NarrativeBuffer(ttl_ms=5 * _MINUTE, max_items=3)
    for index in range(5):
        buffer.add(f"第{index}条", now=index)

    view = buffer.render(now=10)

    assert len(buffer) == 3
    assert [line.split("] ", 1)[1] for line in view.text.splitlines()] == ["第2条", "第3条", "第4条"]


def test_zero_ttl_replays_each_entry_once() -> None:
    """保留期为 0 时只交出一次：交出后的下一窗不再出现。"""
    buffer = NarrativeBuffer(ttl_ms=0, max_items=10)
    buffer.add("[minecraft·attacked] 掉血了", now=0)

    assert buffer.render(now=1).text != ""
    assert buffer.render(now=2).text == ""


@pytest.mark.asyncio
async def test_agent_buffers_follow_narrative_config() -> None:
    """StreamerAgent 的身体近况缓冲按 [agents.streamer.narrative] 段的上限裁剪。"""
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    agent = StreamerAgent(
        config=StreamerConfig.from_dict({"proactive": {"enabled": False}, "narrative": {"body_max_items": 2}}),
        llm_manager=MagicMock(),
        prompt_manager=prompt,
    )

    for index in range(4):
        await agent._on_body_event(
            CoreEvents.GAME_BODY_ATTACKED,
            BodyEventPayload(game="minecraft", kind="attacked", summary=f"挨打（{index}）"),
            "maicraft_attention",
        )

    text = agent._body_narrative_text()
    assert "挨打（2）" in text and "挨打（3）" in text
    assert "挨打（0）" not in text and "挨打（1）" not in text
