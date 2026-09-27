"""
观看数变化消息类型
"""

from typing import Any, Dict

from pydantic import Field

from src.modules.types.bili.base import BiliBaseMessage


class WatchedChangeMessage(BiliBaseMessage):
    """观看数变化消息 - LIVE_OPEN_PLATFORM_WATCHED_CHANGE

    房间统计类状态推送：``watched_count`` 是本场累计观看人次（UV 口径，
    只增不减），变化即推、值即当前状态；``watched_show`` 是平台人读展示
    文案（如 "1.2万人看过"）。无发送者、无消息内容，不属行为流。
    """

    # 房间信息
    channel_id: str = Field(default="")

    # 观看数（累计观看人次）
    watched_count: int = Field(default=0, ge=0)
    watched_show: str = Field(default="")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchedChangeMessage":
        """从字典创建观看数变化消息对象"""
        msg_data = data.get("data", {})
        return cls(
            cmd=data.get("cmd", ""),
            raw_data=data,
            channel_id=str(msg_data.get("channel_id", "")),
            watched_count=int(msg_data.get("watched_count", 0) or 0),
            watched_show=str(msg_data.get("watched_show", "")),
        )
