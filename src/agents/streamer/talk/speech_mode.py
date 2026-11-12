"""说话档位判定：边玩边说（play）还是杂谈唠嗑（talk）。

两档的差别在篇幅和节奏：边打游戏时一轮一事、干脆利落；纯杂谈或开场闲聊时一口气
自然讲两三句，有起头、有细节、有自己的吐槽，句子之间按自然停顿连着播。

判定只看三件事，按优先级：

1. 当前流程单环节显式写了说话模式：照环节来（导演在编排台定的节奏最权威）；
2. 没有启用任何能接委派的游戏 Agent：她只能聊天，按杂谈档；
3. 游戏处于失联等待：游戏里没事可说，按杂谈档撑场；其余按边玩边说。
"""

from __future__ import annotations

from typing import Literal, Optional

__all__ = ["SpeechMode", "resolve_speech_mode"]

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
