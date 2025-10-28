from typing import Dict, Any, Optional
from dataclasses import dataclass
from maim_message import MessageBase, Seg
from .base import BiliBaseMessage


@dataclass
class SuperChatMessage(BiliBaseMessage):
    """醒目留言消息 - LIVE_OPEN_PLATFORM_SUPER_CHAT"""

    room_id: int  # 直播间id
    open_id: str  # 用户唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)
    uname: str  # 购买的用户昵称
    uface: str  # 购买用户头像
    message_id: int  # 留言id(风控场景下撤回留言需要)
    message: str  # 留言内容
    rmb: int  # 支付金额(元)
    timestamp: int  # 赠送时间秒级
    start_time: int  # 生效开始时间
    end_time: int  # 生效结束时间
    guard_level: int  # 对应房间大航海等级
    fans_medal_level: int  # 对应房间勋章信息
    fans_medal_name: str  # 对应房间勋章名字
    fans_medal_wearing_status: bool  # 该房间粉丝勋章佩戴情况
    msg_id: str  # 消息唯一id

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SuperChatMessage":
        """从字典创建SC消息对象"""
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

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
    ) -> MessageBase:
        """构建醒目留言消息的MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags, 1)

        # 创建消息段 - 醒目留言消息
        if self.message.strip():
            text = f"[SC {self.rmb}元] {self.uname}: {self.msg.strip()}"
            data = f"{self.rmb}:{self.message}"
        else:
            text = f"[SC {self.rmb}元] {self.uname} 发送了醒目留言"
            data = f"{self.rmb}:{self.message}"

        message_segment = Seg(
            "seglist",
            [
                Seg(type="superchat", data=data),
                Seg("priority_info", {"message_type": "vip", "priority": self.rmb}),
            ],
        )

        # 记录消息日志
        self.logger.info(f"[SC] {self.uname}: {text}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)
