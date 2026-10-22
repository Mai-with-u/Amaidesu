"""身体通知的重复判断：一条通知在对话历史或待读队列里还留着，就不再整段重抄。

饿了没吃上的说明会把每个食物来源没弄到的原因逐个列出，常有几千字；同样的情况再来一遍时，
把整段原样塞进历史只会涨上下文。是否重复只看"这一次实际要交给模型的历史里还有没有"：
历史整理之后旧那份被摘要取代、或根本还没被读到，就得完整展示，不能指向一条模型已经看不到的通知。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

__all__ = ["notice_present", "repeat_reference"]

# 通知开头冒号前的那截当作标签，用来说明指的是哪一条；太长就不是标签，改用统一说法
_MAX_LABEL_CHARS = 24
_GENERIC_LABEL = "身体处理不了的事"


def notice_present(content: str, messages: List[Dict[str, Any]], queued: Iterable[Tuple[str, str]]) -> bool:
    """同样的通知正文在模型的对话历史里，或在排队等着被读的消息里。

    历史整理会把旧消息换成摘要，摘要里没有这段原文，所以整理之后这里返回 False，调用方重新完整展示。
    """
    if any(message.get("role") == "user" and message.get("content") == content for message in messages):
        return True
    return any(text == content for _task_id, text in queued)


def repeat_reference(message: str) -> str:
    """只留一句指回前面那条的通知：说明是同一件事、没有新变化，不再抄一遍正文。"""
    head, separator, _rest = message.partition("：")
    label = head if separator and len(head) <= _MAX_LABEL_CHARS else _GENERIC_LABEL
    return f"[身体] 「{label}」和前面那条 [身体] 通知一模一样，没有新变化。"
