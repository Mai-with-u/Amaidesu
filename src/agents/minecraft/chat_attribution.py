"""聊天归属：一条聊天栏消息到底是谁说的、说的是谁，给主播附一句说明。

聊天流的 ``sender`` 只在带发言人 UUID 的消息上才是真正的发言人。没有 UUID 的"玩家消息"（服务器或模组
构造的伪装聊天、命令方块的 /say 这类没签名的消息），名字只是聊天栏上显示的那个：车万女仆的女仆死后，
模组会以主人的名字发一行 ``<主人名> [name:"winefox_momo", x:…]`` 的坐标，复盘里它被记成"玩家 ChangingSelf
说的话"、被当成第二个玩家。这里按消息自带的事实分出来：

- 显示名就是角色自己、又没有发言人 UUID：不是任何人说的话，是模组借角色名义发的，改记成系统消息；
- 显示名是别人、没有发言人 UUID：照记成玩家消息，但说明"没有发言人凭据，不一定是这个人说的"；
- 带 UUID 的玩家消息与普通系统消息照旧，不加说明。

整行文字里聊天格式加的 ``<名字> `` 前缀去掉（发言人已经单独写出），正文其余原样保留。
判断写成纯函数，聊天采集器调用；角色自己的账号名由调用方给（Mod 报出之前为空，此时只做"无凭据"标注）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

__all__ = ["ChatAttribution", "attribute_chat"]

#: 借角色名义发的机器消息的说明
NOTE_SHOWN_AS_SELF = "模组借你的名义发的消息，不是谁说的话"
#: 没有发言人 UUID、显示名不是自己的玩家消息的说明
NOTE_UNVERIFIED = "没有发言人凭据，名字只是聊天栏上显示的，不一定是这个人说的"


@dataclass(frozen=True)
class ChatAttribution:
    """归属后的一条聊天：种类、发言人、正文与给主播的一句说明（无说明为空串）。"""

    kind: str
    sender: str
    sender_id: str
    content: str
    note: str = ""


def attribute_chat(event: Dict[str, Any], self_name: str = "") -> ChatAttribution:
    """一条 ``events(topic=chat)`` 事件 → 归属结果。``self_name`` 是角色自己的账号名（未知为空串）。"""
    text = str(event.get("text") or "")
    if event.get("kind") == "system":
        shown_as = str(event.get("shown_as") or "")
        # Mod 已经把"以角色名字显示、却没有角色 UUID"的消息改判成系统消息，并标出显示名
        note = NOTE_SHOWN_AS_SELF if shown_as else ""
        return ChatAttribution(kind="system", sender="", sender_id="", content=text, note=note)
    sender = str(event.get("sender") or "")
    sender_id = str(event.get("sender_id") or "")
    if sender_id or not sender:
        return ChatAttribution(kind="player", sender=sender, sender_id=sender_id, content=text)
    # 没有发言人 UUID：名字是聊天栏上显示的；整行文字带着聊天格式的"<名字> "前缀
    prefix = f"<{sender}> "
    content = text[len(prefix) :] if text.startswith(prefix) else text
    if self_name and sender == self_name:
        return ChatAttribution(kind="system", sender="", sender_id="", content=content, note=NOTE_SHOWN_AS_SELF)
    return ChatAttribution(kind="player", sender=sender, sender_id="", content=content, note=NOTE_UNVERIFIED)
