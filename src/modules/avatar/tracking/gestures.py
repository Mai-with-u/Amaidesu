"""有意的动作：主播想做才做的身体语言（点头同意、摇头否定、歪头疑惑……）

和自动的反应不同，这些动作由主播经 ``avatar_act`` 工具主动发起。每个动作由一串
定时脉冲组成（相对开始时刻的偏移毫秒、脉冲名、幅度），合成器按时刻依次叠加；
"看弹幕"是一次较长的瞥视，不走脉冲。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# 特殊动作名：瞥一眼弹幕区
LOOK_AT_CHAT = "看弹幕"
# 特殊动作名：摆一个表情并保持一段时间（需同时给情绪与秒数）
HOLD_EXPRESSION = "保持表情"
LOOK_AT_CHAT_MS = 1800


@dataclass(frozen=True)
class Gesture:
    """一个有意的动作：给主播看的说明 + 按时刻排好的脉冲序列。"""

    description: str
    steps: Tuple[Tuple[int, str, float], ...] = ()


GESTURES: Dict[str, Gesture] = {
    "点头": Gesture("同意、回应对方", ((0, "nod_deep", 1.0), (450, "nod_deep", 0.8))),
    "摇头": Gesture(
        "否定、不行",
        ((0, "shake_left", 1.0), (260, "shake_right", 1.0), (520, "shake_left", 0.8), (780, "shake_right", 0.6)),
    ),
    "歪头": Gesture("疑惑、好奇", ((0, "tilt", 1.0),)),
    "眼睛一亮": Gesture("看到好东西、有了主意", ((0, "brighten", 1.0),)),
    "吓一跳": Gesture("被吓到", ((0, "startle", 1.0),)),
    "皱眉": Gesture("不满、吃痛", ((0, "wince", 1.0),)),
    "抬眉": Gesture("诶？意外、追问", ((0, "alert", 1.0),)),
    LOOK_AT_CHAT: Gesture("看一眼弹幕区"),
}


__all__ = ["GESTURES", "HOLD_EXPRESSION", "LOOK_AT_CHAT", "LOOK_AT_CHAT_MS", "Gesture"]
