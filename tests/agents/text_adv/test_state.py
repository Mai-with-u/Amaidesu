"""text_adv 状态模型测试：环缓冲有界、快照键集精确、选项序列化形状。"""

from __future__ import annotations

from src.agents.text_adv.state import TextAdvGameAgentState
from src.agents.text_adv.vlm import Option


def test_ring_buffer_bounded_keeps_recent() -> None:
    """maxlen=3 依次记录 5 屏 → 只剩最近 3 屏。"""
    state = TextAdvGameAgentState(max_recent_screens=3)
    for i in range(1, 6):
        state.record_screen(f"屏{i}")
    assert len(state.recent_screens) == 3
    assert list(state.recent_screens) == ["屏3", "屏4", "屏5"]
    assert state.current_text == "屏5"


def test_to_dict_key_set_exact() -> None:
    """快照键集恰为 {text, options, auto, updated_at_ms}，多一个少一个都失败。"""
    state = TextAdvGameAgentState()
    state.record_screen("一段剧情")
    snapshot = state.to_dict(auto=True)
    assert set(snapshot.keys()) == {"text", "options", "auto", "updated_at_ms"}
    assert snapshot["text"] == "一段剧情"
    assert snapshot["auto"] is True
    assert isinstance(snapshot["updated_at_ms"], int)
    assert snapshot["updated_at_ms"] > 0


def test_to_dict_options_index_one_based_and_clickable() -> None:
    """options 序列化为 1-based index + label + clickable；缺坐标选项不可点。"""
    state = TextAdvGameAgentState()
    state.record_screen(
        "面临选择",
        options=[
            Option(label="去学校", vlm_xy=(320, 240)),
            Option(label="留在家里"),
        ],
    )
    snapshot = state.to_dict(auto=False)
    options = snapshot["options"]
    assert isinstance(options, list)
    assert [item["index"] for item in options] == [1, 2]
    assert [item["label"] for item in options] == ["去学校", "留在家里"]
    assert [item["clickable"] for item in options] == [True, False]
    assert snapshot["auto"] is False


def test_record_screen_updates_timestamp_monotonic() -> None:
    """每次记录新屏都刷新 updated_at_ms（毫秒时间戳单调不回退）。"""
    state = TextAdvGameAgentState()
    state.record_screen("第一屏")
    first = state.updated_at_ms
    state.record_screen("第二屏")
    assert state.updated_at_ms >= first
