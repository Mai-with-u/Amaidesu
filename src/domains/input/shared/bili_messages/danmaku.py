"""
弹幕消息类型
"""

from typing import Any, Dict

from pydantic import Field

from .base import BiliBaseMessage


class DanmakuMessage(BiliBaseMessage):
    """弹幕消息 - LIVE_OPEN_PLATFORM_DM"""

    # 用户信息
    uname: str = Field(default="")
    uface: str = Field(default="")
    open_id: str = Field(default="")
    union_id: str = Field(default="")

    # 弹幕内容
    msg: str = Field(default="")
    msg_id: str = Field(default="")

    # 房间信息
    room_id: int = Field(default=0)
    timestamp: int = Field(default=0)

    # 弹幕类型
    dm_type: int = Field(default=0)
    emoji_img_url: str = Field(default="")

    # 粉丝牌
    fans_medal_level: int = Field(default=0)
    fans_medal_name: str = Field(default="")
    fans_medal_wearing_status: bool = Field(default=False)

    # 大航海等级
    guard_level: int = Field(default=0)

    # 其他属性
    is_admin: int = Field(default=0)
    glory_level: int = Field(default=0)

    # 回复相关
    reply_open_id: str = Field(default="")
    reply_uname: str = Field(default="")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DanmakuMessage":
        """从字典创建弹幕消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            uname=msg_data.get("uname", ""),
            uface=msg_data.get("uface", ""),
            open_id=msg_data.get("open_id", ""),
            union_id=msg_data.get("union_id", ""),
            msg=msg_data.get("msg", ""),
            msg_id=msg_data.get("msg_id", ""),
            room_id=msg_data.get("room_id", 0),
            timestamp=msg_data.get("timestamp", 0),
            dm_type=msg_data.get("dm_type", 0),
            emoji_img_url=msg_data.get("emoji_img_url", ""),
            fans_medal_level=msg_data.get("fans_medal_level", 0),
            fans_medal_name=msg_data.get("fans_medal_name", ""),
            fans_medal_wearing_status=msg_data.get("fans_medal_wearing_status", False),
            guard_level=msg_data.get("guard_level", 0),
            is_admin=msg_data.get("is_admin", 0),
            glory_level=msg_data.get("glory_level", 0),
            reply_open_id=msg_data.get("reply_open_id", ""),
            reply_uname=msg_data.get("reply_uname", ""),
        )
