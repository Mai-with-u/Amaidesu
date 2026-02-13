"""
进入直播间消息类型
"""

from typing import Any, Dict

from pydantic import Field

from .base import BiliBaseMessage


class EnterMessage(BiliBaseMessage):
    """进入直播间消息 - LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER"""

    room_id: int = Field(default=0)
    uface: str = Field(default="")
    uname: str = Field(default="")
    open_id: str = Field(default="")
    union_id: str = Field(default="")
    timestamp: int = Field(default=0)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnterMessage":
        """从字典创建进入消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            room_id=msg_data.get("room_id", 0),
            uface=msg_data.get("uface", ""),
            uname=msg_data.get("uname", ""),
            open_id=msg_data.get("open_id", ""),
            union_id=msg_data.get("union_id", ""),
            timestamp=msg_data.get("timestamp", 0),
        )
