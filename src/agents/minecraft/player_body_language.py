"""别的玩家冲角色做的肢体动作：Mod 的 ``player_body_language`` 事件读成给主播的一句话和给游戏 Agent 的通知。

很多玩家不打字，用动作说话：蹲下站起是打招呼，拍一下是叫你，扔东西过来是送你的，蹲起再拿剑对着你挥是约切磋。
Mod 只认 16 格内看得见、冲着角色做的，按人归成一段，在 ``topic=self`` 流里发一条（与目标无关，不带目标编号）。
那句话按先后写谁、做了什么、几下、对着谁或哪儿、拿着什么，分号后面写他在角色哪个方位几格、此刻看没看着角色，
例如"玩家 Alex（e4）看着你蹲起 4 下，拿出铁剑，对着你挥了 2 下；他在你前方 3 格"；同一个人一分钟太多时，
剩下的合成一句"这一分钟又蹲起 40 下、挥手 12 下"。被别的玩家轻轻拍一下也算在这里（"拍了你一下：空手，没伤到"），
不进挨打的 ``hurt``；真连着打才进 ``hurt``。

主播这头要的是一句能顺口接的话：动作照说，观察编号和方位距离去掉，"你"换成"我"（主播读的是她自己的遭遇），
后面附上口径——这是在跟她玩，不是打她，开心地俏皮接一句，不提血量。

游戏 Agent 这头（:data:`PLAYER_BODY_LANGUAGE_NOTICE`）：别人在跟你互动，可以用 ``body_language`` 回一个动作，
它插一下就做完，不打断手上的活；闲着时回完就收尾，不交付、不上报（主播已经从上面那句话知道了）。
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["PLAYER_BODY_LANGUAGE", "PLAYER_BODY_LANGUAGE_NOTICE", "spoken_fact", "spoken_line"]

#: Mod 事件种类：别的玩家冲角色做的肢体动作；给主播的叙事种类与它同名
PLAYER_BODY_LANGUAGE: Final = "player_body_language"

#: 给游戏 Agent 的通知模板（``{message}`` 是 Mod 原话）：点明是社交、怎么回、回完怎么收尾
PLAYER_BODY_LANGUAGE_NOTICE = (
    "[互动] {message}。这是别的玩家在用动作跟你打交道，不是打你：可以用 body_language 回一个动作，"
    "它插一下就做完，不打断手上的活；这些动作多半是什么意思、怎么回，读技能 player-body-language。"
    "手上有活就回完接着做；闲着时回完就结束这一轮（单独调用 minecraft_wait，或不调工具），"
    "不用交付也不用上报，主播那边已经知道了。"
)

#: 括号里的观察编号（e4、b5 这类）：给游戏 Agent 指东西用的，主播念出来没有意义
_OBSERVATION_IDS = re.compile(r"\s*[（(][a-z]\d+[）)]")
#: 紧跟在英文玩家名后面、后面还接着话的观察编号："玩家 Alex（e4）看着你"去掉编号后留一个空格，不粘成"Alex看着你"
_ID_AFTER_NAME = re.compile(r"(?<=[A-Za-z0-9_])\s*[（(][a-z]\d+[）)](?=[^\s，；：。、,;:])")

#: 方位里的格数（"落在你左侧 1 格""往北走了 6 格"）：去掉数字，方向留着
_BLOCKS_AWAY = re.compile(r"\s*\d+(?:\.\d+)?\s*格")

#: 冲着角色的那几种说法：Mod 写给游戏 Agent 的"你"，在主播嘴里是"我"。只换这几处，物品名里带的"你"不碰；
#: 挥向方块时方位写在括号里（"对着石头（你左前方 2 格）"），括号开头的"你"也换
_TOWARD_ME = re.compile(r"(看着|拍了|拍|朝|对着|落在|（)你")

#: 给主播的口径：动作在玩家之间多半是什么意思，以及怎么接（俏皮、开心优先、不提血量）
_HOW_TO_TAKE_IT = (
    "玩家之间蹲起多半是打招呼、拍一下是叫我、扔过来的多半是送我的、拿着剑对着我挥是约切磋；"
    "这是在跟我玩，不是打我：开心地俏皮接一句，不提血量"
)


def spoken_fact(message: str) -> str:
    """Mod 那句话里的动作部分，改成主播嘴里的说法。

    分号后面是他在角色哪个方位几格、此刻看没看着角色，主播用不上，整段去掉；没有分号的（一分钟合成的那句）整句都是动作。
    动作里的观察编号与格数去掉，"看着你""朝你"这类换成"我"。
    """
    text = _OBSERVATION_IDS.sub("", _ID_AFTER_NAME.sub(" ", str(message or ""))).strip().rstrip("。")
    acts = text.rsplit("；", 1)[0] if "；" in text else text
    acts = _BLOCKS_AWAY.sub("", acts)
    return _TOWARD_ME.sub(r"\1我", acts).strip("，； ")


def spoken_line(message: str) -> str:
    """给主播的一句话：动作本身，后面跟着这些动作多半是什么意思、该怎么接。"""
    fact = spoken_fact(message) or "有玩家冲我比划了几下"
    return f"{fact}。{_HOW_TO_TAKE_IT}"
