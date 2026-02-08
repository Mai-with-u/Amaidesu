from typing import Dict, Any, Optional
from maim_message import MessageBase, Seg
from .base import BiliBaseMessage


class EnterMessage(BiliBaseMessage):
    """
    进入直播间消息 - LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER
    """

    room_id: int  # 发生的直播间
    uface: str  # 用户头像
    uname: str  # 用户昵称
    open_id: str  # 用户唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)
    timestamp: int  # 发生的时间戳

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

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
    ) -> MessageBase:
        """构建进入直播间消息的MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags)

        # 创建消息段 - 进入直播间消息
        text = f"{self.uname} 进入了直播间"
        message_segment = Seg(
            "seglist",
            [
                Seg(type="text", data=text),
                Seg("priority_info", {"message_type": "normal", "priority": 0}),
            ],
        )

        # 记录消息日志
        self.logger.info(f"[进入] {self.uname}: {text}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)
