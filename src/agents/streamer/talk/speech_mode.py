"""说话档位判定：边玩边说（play）还是杂谈唠嗑（talk）。

两档的差别在篇幅和节奏：边打游戏时一轮一事、干脆利落；纯杂谈或开场闲聊时一口气
自然讲两三句，有起头、有细节、有自己的吐槽，句子之间按自然停顿连着播。

判定只看三件事，按优先级：

1. 当前流程单环节显式写了说话模式：照环节来（导演在编排台定的节奏最权威）；
2. 没有启用任何能接委派的游戏 Agent：她只能聊天，按杂谈档；
3. 游戏处于失联等待：游戏里没事可说，按杂谈档撑场；其余按边玩边说。
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

__all__ = ["SpeechMode", "resolve_speech_mode", "split_talk_paragraph"]

# 杂谈档一段话最多拆成几句连播：再多就成了念稿，播放队列也容易被一段话占满
_MAX_TALK_PARTS = 3
# 句末标点（含省略号、波浪号）后断句；逗号不断，半句话不单独成句
_TALK_SENTENCE = re.compile(r"[^。！？!?…~～]+[。！？!?…~～]*")

SpeechMode = Literal["play", "talk"]


def resolve_speech_mode(
    *,
    segment_mode: Optional[str],
    game_active: bool,
    game_waiting: bool = False,
) -> SpeechMode:
    """按环节模式、游戏是否启用、游戏是否失联等待判定本轮说话档位。"""
    if segment_mode in ("play", "talk"):
        return segment_mode  # type: ignore[return-value]
    if not game_active or game_waiting:
        return "talk"
    return "play"


def split_talk_paragraph(text: str, *, max_parts: int = _MAX_TALK_PARTS) -> List[str]:
    """杂谈档台词按句末标点拆成至多 ``max_parts`` 句，连起来与原文逐字相同。

    她一口气讲两三句时，拆开后句与句之间按播放自然停顿，被更急的弹幕插进来时
    还没播的后半段可以单独作废。句子比上限多时，多出来的并进最后一句；只有一句
    （或全是标点）时原样返回一句。
    """
    stripped = text.strip()
    parts = [part for part in _TALK_SENTENCE.findall(stripped) if part.strip()]
    if len(parts) <= 1:
        return [stripped] if stripped else []
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + ["".join(parts[max_parts - 1 :])]
    return parts
