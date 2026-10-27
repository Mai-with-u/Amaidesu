"""Planner 决策请求拼装纯函数：初始消息序列的顺序与空窗兜底、每步附加消息的挂载规则"""

from typing import Any, Dict, Optional

from src.agents.streamer.planner_render import assemble_request_messages, step_extra_messages


def test_system_first_reference_last_and_dialogue_in_between() -> None:
    dialogue = [
        {"role": "user", "content": "弹幕"},
        {"role": "assistant", "content": "台词"},
    ]
    messages = assemble_request_messages("系统提示词", dialogue, "参考段")
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"] == "系统提示词"
    assert messages[1:] == dialogue + [{"role": "user", "content": "参考段"}]


def test_empty_dialogue_and_reference_falls_back_to_placeholder_user_message() -> None:
    messages: list[Dict[str, Any]] = assemble_request_messages("系统提示词", [], "")
    assert messages == [
        {"role": "system", "content": "系统提示词"},
        {"role": "user", "content": "（本窗无对话与参考内容）"},
    ]


def test_reference_appended_after_dialogue_without_fallback() -> None:
    dialogue = [{"role": "assistant", "content": "台词"}]
    messages = assemble_request_messages("系统提示词", dialogue, "参考段")
    assert messages[-1] == {"role": "user", "content": "参考段"} and "（本窗无对话与参考内容）" not in str(messages)


def test_frame_tail_only_on_first_step_and_tail_after_it() -> None:
    frame_tail: Optional[Dict[str, Any]] = {"role": "user", "content": [{"type": "image_ref", "ref": "frame"}]}
    tail: Optional[Dict[str, Any]] = {"role": "user", "content": [{"type": "image_ref", "ref": "shot"}]}
    assert step_extra_messages(frame_tail, tail, 1) == [frame_tail, tail]  # 画面在前、截图在后
    assert step_extra_messages(frame_tail, tail, 2) == [tail]  # 画面只挂第一步
    assert step_extra_messages(None, tail, 1) == [tail]
    assert step_extra_messages(frame_tail, None, 1) == [frame_tail]
    assert step_extra_messages(None, None, 3) == []  # 两者皆无则本步零附加
