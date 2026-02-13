"""
礼物消息类型
"""

from typing import Any, Dict

from pydantic import BaseModel, Field

from .base import BiliBaseMessage


class AnchorInfo(BaseModel):
    """主播信息"""
    uid: int = 0
    open_id: str = ""
    union_id: str = ""
    uname: str = ""
    uface: str = ""


class ComboInfo(BaseModel):
    """连击信息"""
    combo_base_num: int = 0
    combo_count: int = 0
    combo_id: str = ""
    combo_timeout: int = 0


class BlindGift(BaseModel):
    """盲盒信息"""
    blind_gift_id: int = 0
    status: bool = False


class GiftMessage(BiliBaseMessage):
    """礼物消息 - LIVE_OPEN_PLATFORM_SEND_GIFT"""

    room_id: int = Field(default=0)
    open_id: str = Field(default="")
    union_id: str = Field(default="")
    uname: str = Field(default="")
    uface: str = Field(default="")
    gift_id: int = Field(default=0)
    gift_name: str = Field(default="")
    gift_num: int = Field(default=1)
    price: int = Field(default=0)
    r_price: int = Field(default=0)
    paid: bool = Field(default=False)
    fans_medal_level: int = Field(default=0)
    fans_medal_name: str = Field(default="")
    fans_medal_wearing_status: bool = Field(default=False)
    guard_level: int = Field(default=0)
    timestamp: int = Field(default=0)
    anchor_info: AnchorInfo = Field(default_factory=AnchorInfo)
    msg_id: str = Field(default="")
    gift_icon: str = Field(default="")
    combo_gift: bool = Field(default=False)
    combo_info: ComboInfo = Field(default_factory=ComboInfo)
    blind_gift: BlindGift = Field(default_factory=BlindGift)

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
