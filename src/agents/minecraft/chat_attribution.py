"""聊天归属：一条聊天栏消息到底是谁说的、说的是谁，给主播附一句说明。

聊天流的 ``sender`` 只在带发言人 UUID 的消息上才是真正的发言人。没有 UUID 的"玩家消息"（服务器或模组
构造的伪装聊天、命令方块的 /say 这类没签名的消息），名字只是聊天栏上显示的：车万女仆的女仆死后，
模组会以主人的名字发一行 ``<主人名> [name:"winefox_momo", x:…]`` 的坐标，复盘里它被记成主人这位玩家
说的话、被当成第二个玩家。判断借谁的名字要知道角色自己是谁，这件事 Mod 在客户端做：以角色名字显示、
却没有发言人 UUID 的消息，Mod 改记成系统消息并在 ``shown_as`` 写出借的名字。这里按消息自带的事实附说明：

- 系统消息带 ``shown_as``：不是任何人说的话，是模组借角色名义发的；
- 没有发言人 UUID 的玩家消息：照记成玩家消息，但说明"没有发言人凭据，不一定是这个人说的"
  （旧版 Mod 不做上一条判断时，借角色名字发的坐标也落在这里，至少不会被当成真发言）；
- 带 UUID 的玩家消息与普通系统消息照旧，不加说明；
- 整行是 ``<名字> 正文`` 的系统消息：有的服务器把玩家聊天当系统消息下发（实测带正版登录模组的局域网服），
  Mod 走的是系统消息路径，既认不出发言人、也滤不掉角色自己的回显。名字是角色自己的账号名，就是角色刚发出去
  的话回到了聊天栏，按回显丢掉；是别人的名字，就按没有发言人凭据的玩家消息转出。不认的话，玩家搭话被当成
  服务器公告、叫不醒主播，角色自己的话又被当成另一个叫这个名字的人说的。还不知道角色账号名时分不清是不是
  回显，照系统消息转出并说明发言人未确认：宁可漏接一次玩家搭话，也不能让她把自己的话当别人的话去接，
  接了又在游戏里回一句、回的话再回流，自己跟自己聊起来。这种格式谁都能伪造（服务器插件、起了名的生物），
  所以认出来的也只是没有凭据的玩家发言，不带任何额外分量。

整行文字里聊天格式加的 ``<名字> `` 前缀去掉（发言人已经单独写出），正文其余原样保留。

消息里点到的实体（``mentions``：死亡播报的死者与凶手、进出提示的玩家）与消息模板编号（Minecraft 的翻译键）
原样转出，游戏 Agent 按 UUID 认出死的是名册里哪一只，不靠名字猜。判断写成纯函数，聊天采集器调用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.modules.events.payloads.game_chat import GameChatMention

__all__ = ["ChatAttribution", "attribute_chat", "line_speaker", "mentions_of"]

#: 借角色名义发的机器消息的说明
NOTE_SHOWN_AS_SELF = "模组借你的名义发的消息，不是谁说的话"
#: 没有发言人 UUID 的玩家消息的说明
NOTE_UNVERIFIED = "没有发言人凭据，名字只是聊天栏上显示的，不一定是这个人说的"
#: 还不知道角色账号名时，"<名字> 正文"这种系统消息的说明
NOTE_SPEAKER_UNCONFIRMED = "可能是玩家聊天，发言人未确认，也可能是你自己刚说的话"

#: 系统消息里套着聊天格式的整行："<账号名> 正文"。账号名按 Minecraft 的规矩只有字母、数字、下划线，最长 16 个；
#: 带称号、颜色前缀的服务器格式对不上就不认，照普通系统消息转出，不去猜
_CHAT_LINE = re.compile(r"<([A-Za-z0-9_]{1,16})> (.+)", re.DOTALL)


@dataclass(frozen=True)
class ChatAttribution:
    """归属后的一条聊天：种类、发言人、正文与给主播的一句说明（无说明为空串）。"""

    kind: str
    sender: str
    sender_id: str
    content: str
    note: str = ""


def line_speaker(event: Dict[str, Any]) -> str:
    """系统消息整行是 ``<账号名> 正文`` 时的账号名；不是这种整行（或 Mod 已标出借名）时为空串。

    采集器据此决定要不要先认一下角色自己的账号名：只有这种整行才需要分辨是不是角色自己的回显。
    """
    if event.get("kind") != "system" or event.get("shown_as"):
        return ""
    matched = _CHAT_LINE.fullmatch(str(event.get("text") or ""))
    return matched.group(1) if matched else ""


def attribute_chat(event: Dict[str, Any], self_name: str = "") -> Optional[ChatAttribution]:
    """一条 ``events(topic=chat)`` 事件 → 归属结果；是角色自己的回显时为 None（不转出）。

    ``self_name`` 是角色的账号名，认 ``<账号名> 正文`` 这种系统消息是不是回显用；不知道时传空串，
    这类整行照系统消息转出并说明发言人未确认（分不清是不是自己的回显，不当成别人的话）。
    """
    text = str(event.get("text") or "")
    if event.get("kind") == "system":
        speaker = line_speaker(event)
        if speaker:
            # 服务器把玩家聊天当系统消息发来：自己的名字 -> 角色刚说的话回显，丢掉；别人的名字 -> 按玩家发言转出，
            # 主播才会像接弹幕一样及时接这句话，而不是当成服务器公告；还不知道自己叫什么 -> 照系统消息转出，
            # 不叫醒主播，免得她接自己的话、在游戏里回一句、回的话再回流成"别人说"
            if not self_name:
                return ChatAttribution(
                    kind="system", sender="", sender_id="", content=text, note=NOTE_SPEAKER_UNCONFIRMED
                )
            if speaker.casefold() == self_name.casefold():
                return None
            said = _CHAT_LINE.fullmatch(text)
            content = said.group(2) if said else text
            return ChatAttribution(kind="player", sender=speaker, sender_id="", content=content, note=NOTE_UNVERIFIED)
        # Mod 已经把"以角色名字显示、却没有角色 UUID"的消息改判成系统消息，并标出显示名
        note = NOTE_SHOWN_AS_SELF if event.get("shown_as") else ""
        return ChatAttribution(kind="system", sender="", sender_id="", content=text, note=note)
    sender = str(event.get("sender") or "")
    sender_id = str(event.get("sender_id") or "")
    if sender_id or not sender:
        return ChatAttribution(kind="player", sender=sender, sender_id=sender_id, content=text)
    # 没有发言人 UUID：名字是聊天栏上显示的；整行文字带着聊天格式的"<名字> "前缀
    prefix = f"<{sender}> "
    content = text[len(prefix) :] if text.startswith(prefix) else text
    return ChatAttribution(kind="player", sender=sender, sender_id="", content=content, note=NOTE_UNVERIFIED)


def mentions_of(event: Dict[str, Any]) -> List[GameChatMention]:
    """消息里点到的实体，按出现先后；没有 UUID 的条目认不出是谁，不要。"""
    mentions: List[GameChatMention] = []
    for item in event.get("mentions") or []:
        if not isinstance(item, dict) or not item.get("uuid"):
            continue
        mentions.append(
            GameChatMention(name=str(item.get("name") or ""), type=str(item.get("type") or ""), id=str(item["uuid"]))
        )
    return mentions
