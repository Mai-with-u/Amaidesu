"""说话档位判定：环节显式模式优先，其次看游戏是否启用与是否失联等待。"""

from __future__ import annotations

import pytest

from src.agents.streamer.talk.speech_mode import resolve_speech_mode, split_talk_paragraph


@pytest.mark.parametrize(
    ("segment_mode", "game_active", "game_waiting", "expected"),
    [
        ("talk", True, False, "talk"),  # 游戏场开场杂谈段：照环节来
        ("play", False, False, "play"),  # 导演显式要求边玩边说
        (None, False, False, "talk"),  # 纯杂谈场没启用游戏
        (None, True, True, "talk"),  # 游戏失联等待：撑场靠聊天
        (None, True, False, "play"),
        ("unknown", True, False, "play"),  # 不认识的值按未指定处理
    ],
)
def test_resolve_speech_mode(segment_mode, game_active: bool, game_waiting: bool, expected: str) -> None:
    assert (
        resolve_speech_mode(segment_mode=segment_mode, game_active=game_active, game_waiting=game_waiting) == expected
    )


def test_split_talk_paragraph_groups_up_to_three_clauses_without_losing_chars() -> None:
    """杂谈档按句末标点拆成至多三句，超出的并入末句，拼回去与原文逐字一致。"""
    assert split_talk_paragraph("") == []
    assert split_talk_paragraph("只有一句话。") == ["只有一句话。"]
    two = split_talk_paragraph("我是麦麦，先打个招呼！今天是出道杂谈回。")
    assert two == ["我是麦麦，先打个招呼！", "今天是出道杂谈回。"]

    four = "第一句起个头。第二句展开细节！第三句补个梗？第四句收尾~"
    parts = split_talk_paragraph(four)
    assert parts == ["第一句起个头。", "第二句展开细节！", "第三句补个梗？第四句收尾~"]
    assert "".join(parts) == four
