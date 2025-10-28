from typing import Dict, Any, Optional
from dataclasses import dataclass
from maim_message import MessageBase, Seg
from .base import BiliBaseMessage


@dataclass
class DanmakuMessage(BiliBaseMessage):
    """弹幕消息 - LIVE_OPEN_PLATFORM_DM"""

    # 用户信息
    uname: str  # 用户昵称
    uface: str  # 用户头像
    open_id: str  # 用户唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)

    # 弹幕内容
    msg: str
    msg_id: str

    # 房间信息
    room_id: int  # 弹幕接收的直播间
    timestamp: int  # 弹幕发送时间秒级时间戳

    # 弹幕类型
    dm_type: int  # 0-文字弹幕, 1-表情弹幕
    emoji_img_url: str  # 表情图片URL

    # 粉丝牌
    fans_medal_level: int  # 粉丝牌等级
    fans_medal_name: str  # 粉丝牌名称
    fans_medal_wearing_status: bool  # 是否佩戴粉丝牌

    # 大航海等级
    guard_level: int  # 0-无大航海, 1-总督, 2-提督, 3-舰长

    # 其他属性
    is_admin: int  # 是否房管，1-是，0-否
    glory_level: int  # 荣耀等级

    # 回复相关
    reply_open_id: str  # 被at用户唯一标识
    reply_uname: str

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

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
    ) -> MessageBase:
        """构建弹幕消息的MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags)

        # 创建消息段 - 弹幕消息直接使用msg内容
        message_segment = Seg(
            "seglist",
            [
                Seg(type="text", data=self.msg.strip()),
                Seg("priority_info", {"message_type": "normal", "priority": 0}),
            ],
        )

        # 记录消息日志
        self.logger.info(f"[弹幕] {self.uname}: {self.msg}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=self.msg)
