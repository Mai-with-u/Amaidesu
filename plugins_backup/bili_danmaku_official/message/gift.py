from typing import Dict, Any, Optional
from dataclasses import dataclass
from maim_message import MessageBase, Seg
from .base import BiliBaseMessage


@dataclass
class AnchorInfo:
    uid: int  # 收礼主播uid
    open_id: str  # 收礼主播唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)
    uname: str  # 收礼主播昵称
    uface: str  # 收礼主播头像


@dataclass
class ComboInfo:
    combo_base_num: int  # 每次连击赠送的道具数量
    combo_count: int  # 连击次数
    combo_id: str  # 连击id
    combo_timeout: int  # 连击有效期秒


@dataclass
class BlindGift:
    blind_gift_id: int  # 盲盒id
    status: bool  # 是否是盲盒


@dataclass
class GiftMessage(BiliBaseMessage):
    """礼物消息 - LIVE_OPEN_PLATFORM_SEND_GIFT"""

    room_id: int  # 房间号
    open_id: str  # 用户唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)
    uname: str  # 送礼用户昵称
    uface: str  # 送礼用户头像
    gift_id: int  # 道具id(盲盒:爆出道具id)
    gift_name: str  # 道具名(盲盒:爆出道具名)
    gift_num: int  # 赠送道具数量
    price: int  # 礼物爆出单价，(1000 = 1元 = 10电池),盲盒:爆出道具的价值
    r_price: int  # 实际价值(1000 = 1元 = 10电池),盲盒:爆出道具的价值
    paid: bool  # 是否是付费道具
    fans_medal_level: int  # 实际送礼人的勋章信息
    fans_medal_name: str  # 粉丝勋章名
    fans_medal_wearing_status: bool  # 该房间粉丝勋章佩戴情况
    guard_level: int  # 大航海等级
    timestamp: int  # 收礼时间秒级时间戳
    anchor_info: AnchorInfo  # 主播信息
    msg_id: str  # 消息唯一id
    gift_icon: str  # 道具icon
    combo_gift: bool  # 是否是combo道具
    combo_info: ComboInfo  # 连击信息
    blind_gift: BlindGift  # 盲盒信息

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GiftMessage":
        """从字典创建礼物消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            room_id=msg_data.get("room_id", 0),
            open_id=msg_data.get("open_id", ""),
            union_id=msg_data.get("union_id", ""),
            uname=msg_data.get("uname", ""),
            uface=msg_data.get("uface", ""),
            gift_id=msg_data.get("gift_id", 0),
            gift_name=msg_data.get("gift_name", ""),
            gift_num=msg_data.get("gift_num", 1),
            price=msg_data.get("price", 0),
            r_price=msg_data.get("r_price", 0),
            paid=msg_data.get("paid", False),
            fans_medal_level=msg_data.get("fans_medal_level", 0),
            fans_medal_name=msg_data.get("fans_medal_name", ""),
            fans_medal_wearing_status=msg_data.get("fans_medal_wearing_status", False),
            guard_level=msg_data.get("guard_level", 0),
            timestamp=msg_data.get("timestamp", 0),
            anchor_info=AnchorInfo(
                uid=msg_data.get("anchor_info", {}).get("uid", 0),
                open_id=msg_data.get("anchor_info", {}).get("open_id", ""),
                union_id=msg_data.get("anchor_info", {}).get("union_id", ""),
                uname=msg_data.get("anchor_info", {}).get("uname", ""),
                uface=msg_data.get("anchor_info", {}).get("uface", ""),
            ),
            msg_id=msg_data.get("msg_id", ""),
            gift_icon=msg_data.get("gift_icon", ""),
            combo_gift=msg_data.get("combo_gift", False),
            combo_info=ComboInfo(
                combo_base_num=msg_data.get("combo_info", {}).get("combo_base_num", 0),
                combo_count=msg_data.get("combo_info", {}).get("combo_count", 0),
                combo_id=msg_data.get("combo_info", {}).get("combo_id", ""),
                combo_timeout=msg_data.get("combo_info", {}).get("combo_timeout", 0),
            ),
            blind_gift=BlindGift(
                blind_gift_id=msg_data.get("blind_gift", {}).get("blind_gift_id", 0),
                status=msg_data.get("blind_gift", {}).get("status", False),
            ),
        )

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
    ) -> MessageBase:
        """构建礼物消息的MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags, template_items, 0.7)

        # 创建消息段 - 礼物消息
        gift_name = self.gift_name or "礼物"
        data = f"{self.gift_name}:{self.gift_num}"
        text = f"{self.uname} 送出了 {self.gift_num} 个 {gift_name}"
        # message_segment = Seg(type="text", data=text)
        message_segment = Seg(
            "seglist",
            [
                Seg(type="gift", data=data),
                Seg("priority_info", {"message_type": "vip", "priority": 1}),
            ],
        )

        # 记录消息日志
        self.logger.info(f"[礼物] {self.uname}: {text}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)
