"""
大航海消息类型
"""

from typing import Any, Dict

from pydantic import BaseModel, Field

from .base import BiliBaseMessage


class UserInfo(BaseModel):
    """用户信息"""

    open_id: str = ""
    union_id: str = ""
    uname: str = ""
    uface: str = ""


class GuardMessage(BiliBaseMessage):
    """大航海消息 - LIVE_OPEN_PLATFORM_GUARD"""

    user_info: UserInfo = Field(default_factory=UserInfo)
    guard_level: int = Field(default=0)
    guard_num: int = Field(default=0)
    guard_unit: str = Field(default="")
    price: int = Field(default=0)
    fans_medal_level: int = Field(default=0)
    fans_medal_name: str = Field(default="")
    fans_medal_wearing_status: bool = Field(default=False)
    room_id: int = Field(default=0)
    msg_id: str = Field(default="")
    timestamp: int = Field(default=0)

    @property
    def uname(self) -> str:
        """用户昵称"""
        return self.user_info.uname

    @property
    def open_id(self) -> str:
        """用户 ID"""
        return self.user_info.open_id

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuardMessage":
        """从字典创建大航海消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            user_info=UserInfo(
                open_id=msg_data.get("open_id", ""),
                union_id=msg_data.get("union_id", ""),
                uname=msg_data.get("uname", ""),
                uface=msg_data.get("uface", ""),
            ),
            guard_level=msg_data.get("guard_level", 0),
            guard_num=msg_data.get("guard_num", 0),
            guard_unit=msg_data.get("guard_unit", ""),
            price=msg_data.get("price", 0),
            fans_medal_level=msg_data.get("fans_medal_level", 0),
            fans_medal_name=msg_data.get("fans_medal_name", ""),
            fans_medal_wearing_status=msg_data.get("fans_medal_wearing_status", False),
            room_id=msg_data.get("room_id", 0),
            msg_id=msg_data.get("msg_id", ""),
            timestamp=msg_data.get("timestamp", 0),
        )
