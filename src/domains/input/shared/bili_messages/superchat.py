"""
醒目留言消息类型
"""

from typing import Any, Dict

from pydantic import Field

from .base import BiliBaseMessage


class SuperChatMessage(BiliBaseMessage):
    """醒目留言消息 - LIVE_OPEN_PLATFORM_SUPER_CHAT"""

    room_id: int = Field(default=0)
    open_id: str = Field(default="")
    union_id: str = Field(default="")
    uname: str = Field(default="")
    uface: str = Field(default="")
    message_id: int = Field(default=0)
    message: str = Field(default="")
    rmb: int = Field(default=0)
    timestamp: int = Field(default=0)
    start_time: int = Field(default=0)
    end_time: int = Field(default=0)
    guard_level: int = Field(default=0)
    fans_medal_level: int = Field(default=0)
    fans_medal_name: str = Field(default="")
    fans_medal_wearing_status: bool = Field(default=False)
    msg_id: str = Field(default="")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SuperChatMessage":
        """从字典创建 SC 消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            room_id=msg_data.get("room_id", 0),
            open_id=msg_data.get("open_id", ""),
            union_id=msg_data.get("union_id", ""),
            uname=msg_data.get("uname", ""),
            uface=msg_data.get("uface", ""),
            message_id=msg_data.get("message_id", 0),
            message=msg_data.get("message", ""),
            rmb=msg_data.get("rmb", 0),
            timestamp=msg_data.get("timestamp", 0),
            start_time=msg_data.get("start_time", 0),
            end_time=msg_data.get("end_time", 0),
            guard_level=msg_data.get("guard_level", 0),
            fans_medal_level=msg_data.get("fans_medal_level", 0),
            fans_medal_name=msg_data.get("fans_medal_name", ""),
            fans_medal_wearing_status=msg_data.get("fans_medal_wearing_status", False),
            msg_id=msg_data.get("msg_id", ""),
        )
