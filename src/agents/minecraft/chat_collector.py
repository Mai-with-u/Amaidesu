"""
MaicraftChatCollector —— 游戏内聊天采集器

上游是 MaiCraft v1 的聊天事件流（``events(topic=chat)``）：聊天栏收到的玩家聊天与服务器系统消息，
角色自己说的话 Mod 已经滤掉；服务器把玩家聊天当系统消息下发时 Mod 滤不掉，这里按角色账号名认出回显再丢掉
（账号名每次接上连接后读一次 ``observe(self)``，只在遇到 ``<名字> 正文`` 这种系统消息时才去读）。本采集器**常驻**带着游标长轮询，把别人说的话转成通用的
``game.chat.received`` 事件，交给主播 Agent。连接、长轮询与游标都在 :class:`MaicraftEventsCollector`。

私聊（别人用 /msg 对角色说的话）照常转出并标 ``private``：主播据此只拿它做决定，不在直播里念出或转述。
没有发言人 UUID 的"玩家消息"名字只是聊天栏上显示的：按 ``chat_attribution`` 给 Mod 判成借角色名义发的
机器消息与无凭据的发言附一句说明给主播；消息里点到的实体与模板编号原样转出，游戏 Agent 据此认死讯。
玩家聊天正文提到她（人设名、账号名，词表由组合根透传）时补一条 ``type="self"`` 的点名词条目：
主播规划器据此分清"这句在跟她说话"和"玩家互相聊"，判定只做事实标注、不判该不该回。
首次接入只对齐游标、不补发历史；Mod 换世界换了流编号后，同样只对齐游标。

每条转出的消息带一个随机短串消息号：主播规划器按它指认"依据的是哪一句"，落库时一并记下。不用进程内
计数器：断线续播后同一场接着发号，计数从头会与旧号相撞。
"""

from __future__ import annotations

import math
import secrets
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from src.agents.minecraft.chat_attribution import (
    SELF_MENTION_TYPE,
    attribute_chat,
    line_speaker,
    matched_self_mention,
    mentions_of,
)
from src.agents.minecraft.events_collector import MaicraftEventsCollector
from src.agents.minecraft.maicraft import OBSERVE, reply_of
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload
from src.modules.tools.models import ToolInvocation


class MaicraftChatCollector(MaicraftEventsCollector):
    """游戏内聊天采集器（常驻长轮询 MaiCraft 的聊天事件流）。"""

    name = "maicraft_chat"
    description = "常驻读 MaiCraft 的聊天事件流，把游戏里别人说的话转成 game.chat.received 事件"
    topic = "chat"
    stream_label = "聊天流"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        self_mention_names: Sequence[str] = (),
    ) -> None:
        super().__init__(config=config, event_bus=event_bus, client_factory=client_factory)
        # 角色的账号名：认"<名字> 正文"这种系统消息是不是自己的回显用。None = 这条连接上还没读到
        self._account: Optional[str] = None
        # 判提及她的名字词表（组合根透传，如主播人设名）：玩家聊天正文含词表里的词就算提及她；
        # 角色账号名不在构造时给（运行时经 observe(self) 读），见 _self_mention_names
        self._injected_mention_names = tuple(self_mention_names)

    async def _teardown(self) -> None:
        """拆连接时忘掉账号名：重新接上可能已经换了号，下次遇到要认的整行再读。"""
        self._account = None
        await super()._teardown()

    async def _self_account(self) -> str:
        """角色的账号名；这条连接上第一次要用时读一次 ``observe(self)``，读不到返回空串、下次再试。

        Mod 那边读一次自己会顺带"读完顺着看一眼"（闲着时打开背包看两三秒），所以每条连接只读一次、
        只在遇到要认的整行时才读，不定时去读。
        """
        if self._account is not None:
            return self._account
        provider = self._provider
        if provider is None:
            return ""
        try:
            reply = reply_of(
                await provider.invoke(
                    ToolInvocation(
                        tool_name=OBSERVE, arguments={"what": "self"}, source=self.name, invoking_agent="minecraft"
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001 - 读不到账号名只影响认回显，聊天照转
            self.logger.warning("读角色账号名失败，这条聊天按别人的话转出", exc=exc)
            return ""
        name = reply.data.get("name") if reply.ok and isinstance(reply.data, dict) else None
        if not isinstance(name, str) or not name:
            self.logger.warning(f"observe(self) 没给出角色账号名，这条聊天按别人的话转出：{reply.raw}")
            return ""
        self._account = name
        return name

    async def _self_mention_names(self) -> Tuple[str, ...]:
        """判提及用的名字词表：构造时注入的词表（如人设名）+ 角色账号名（去重，大小写不敏感算同一词）。

        账号名在这条连接上第一次要用时经 ``_self_account`` 读一次（MCP 有读后顺看的副作用，所以只在
        真要用时才读、读一次就缓存）。账号名常是玩家打字叫她的名字，词表缺了它就接不住账号名的点名。
        """
        names = list(self._injected_mention_names)
        account = await self._self_account()
        if account:
            folded = {name.casefold() for name in names}
            if account.casefold() not in folded:
                names.append(account)
        return tuple(names)

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条收到的聊天 → 一条 ``game.chat.received``；空白的、角色自己的回显不转，私聊照转并标出来。"""
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        # 只有"<名字> 正文"这种系统消息才要分辨是不是自己说的：先认账号名，再归属
        account = await self._self_account() if line_speaker(event) else ""
        attributed = attribute_chat(event, self_name=account)
        if attributed is None:
            return False
        system = attributed.kind == "system"
        # 玩家聊天正文含她的名字（人设名/账号名，casefold 子串）时补一条自提及条目：Mod 的点名词只填
        # 系统消息，玩家文本点名没有结构化数据，归因层在这里按文字判定补上（只做"文本提及了"的事实，
        # 不判"该不该回"）；游戏报的点名词照旧在前，自提及条目追加在后
        mention_names = tuple() if system else await self._self_mention_names()
        mentioned = matched_self_mention(attributed.content, mention_names)
        mentions = mentions_of(event)
        if mentioned:
            mentions.append(GameChatMention(name=mentioned, type=SELF_MENTION_TYPE, id=""))
        # 这一页事件所在的存档：存储据此记"在哪个存档见过这位玩家"（旧版 Mod 与不明时为空串）
        world = self._page_world
        payload = GameChatPayload(
            game="minecraft",
            kind="system" if system else "player",
            sender=attributed.sender,
            sender_id=attributed.sender_id,
            # 游戏收到时核对过的"发言人此刻在线"：存储只为核对过的玩家建档，控制台、命令方块借名发的不认
            sender_online=not system and event.get("sender_online") is True,
            # 玩家消息透传上游报出的距离格数（用于主控态 24 格分级唤醒），系统消息与旧版 Mod 恒为 None
            sender_distance=None if system else _extract_sender_distance(event),
            content=attributed.content,
            detail=extract_chat_detail(event),
            truncated=event.get("truncated") is True,
            # 私聊只有玩家发得出来：系统消息即使带了标记也不算
            private=not system and event.get("private") is True,
            note=attributed.note,
            mentions=mentions,
            message_key=str(event.get("translation_key") or ""),
            save_id=world.id if world is not None else "",
            save_name=world.name if world is not None else "",
            # 这台服务器验不验正版账号：存储记在"在哪个存档见过他"上，运营者据此知道认人能信几分
            save_online_mode=world.online_mode if world is not None else "unknown",
            message_id=new_message_id(),
        )
        await self.emit_event(CoreEvents.GAME_CHAT_RECEIVED, payload, source=self.name)
        return True


def extract_chat_detail(event: Mapping[str, Any]) -> str:
    """从上游聊天事件提取可选附加描述（如成就/进度悬停说明）：缺失、布尔或空白返回空串。"""
    raw = event.get("detail")
    if raw is None or isinstance(raw, bool):
        return ""
    return str(raw).strip()


def _extract_sender_distance(event: Dict[str, Any]) -> Optional[float]:
    """从上游聊天事件提取有效非负距离格数；缺失、布尔值、负数或非有限数时返回 None。"""
    raw = event.get("sender_distance")
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if math.isfinite(value) and value >= 0.0:
            return value
    return None


def new_message_id() -> str:
    """一条游戏公屏消息的消息号：8 位十六进制随机串（一场几百句，撞号可忽略；工具按"恰好命中一行"兜底）。"""
    return secrets.token_hex(4)


__all__ = ["MaicraftChatCollector", "extract_chat_detail", "new_message_id"]
