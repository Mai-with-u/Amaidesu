"""长弹幕、早期历史和长叙事完整进入主播的决策上下文。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState


def _make_planner() -> Planner:
    """构造只组装上下文的决策器，测试不访问外部模型。"""
    return Planner(
        config={"planner_max_steps": 2},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        reply_provider=MagicMock(),
    )


@pytest.mark.parametrize("size", [0, 2000, 12000, 30000])
def test_long_message_keeps_tail_and_message_id(size: int) -> None:
    """尾部要求和消息 ID 不能因正文较长而丢失。"""
    text = "长" * size + "保留蓝色屋顶"
    content = canonical.canonical_content(role="user", nickname="小明", text=text, message_id="target")
    assert content == f"小明: {text} [id:target]"
    assert canonical.canonical_content(role="assistant", text=text) == text


def test_planner_keeps_all_history_messages_in_order() -> None:
    """大量完整历史保持原顺序，最初要求不会被字符预算挤掉。"""
    history = [
        SimpleNamespace(
            role="user",
            content=f"第{i}条" + "内容" * 3000,
            sender_name="观众",
            message_id=f"m{i}",
            message_type="danmaku",
        )
        for i in range(45)
    ]
    assert _make_planner()._build_dialogue_messages([], history) == [
        canonical.turn_to_message(turn) for turn in history
    ]


def test_batch_message_keeps_long_user_instruction() -> None:
    """本批新指令也保留超过旧单条上限的具体限制。"""
    item = SimpleNamespace(
        user=SimpleNamespace(name="观众"),
        content="建房" * 4000 + "保留入口",
        message_id="current",
        message_type="danmaku",
    )
    assert _make_planner()._build_dialogue_messages([item], []) == [canonical.batch_item_to_message(item)]
    assert "保留入口" in _make_planner()._build_dialogue_messages([item], [])[0]["content"]
