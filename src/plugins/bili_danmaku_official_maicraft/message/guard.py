from typing import Dict, Any, Optional
from dataclasses import dataclass
from maim_message import MessageBase, Seg
from .base import BiliBaseMessage


@dataclass
class UserInfo:
    open_id: str  # 用户唯一标识
    union_id: str  # 用户在同一个开发者下的唯一标识(默认为空，根据业务需求单独申请开通)
    uname: str  # 用户昵称
    uface: str  # 用户头像


@dataclass
class GuardMessage(BiliBaseMessage):
    """大航海消息 - LIVE_OPEN_PLATFORM_GUARD"""

    user_info: UserInfo  # 用户信息
    guard_level: int  # 大航海等级(1-总督, 2-提督, 3-舰长)
    guard_num: int  # 大航海数量
    guard_unit: str  # 大航海单位(正常单位为“月”，如为其他内容，无视guard_num以本字段内容为准，例如*3天)
    price: int  # 大航海金瓜子
    fans_medal_level: int  # 粉丝勋章等级
    fans_medal_name: str  # 粉丝勋章名
    fans_medal_wearing_status: bool  # 该房间粉丝勋章佩戴情况
    room_id: int  # 房间号
    msg_id: str  # 消息唯一id
    timestamp: int  # 上舰时间秒级时间戳

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

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
    ) -> MessageBase:
        """构建大航海消息的MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags, 1)

        # 创建消息段 - 大航海消息
        guard_level_map = {
            1: "总督",
            2: "提督",
            3: "舰长",
        }

        guard_money_map = {
            1: 20000,
            2: 2000,
            3: 138,
        }

        guard_name = guard_level_map.get(self.guard_level, "无等级")
        text = f"{self.uname} 开通了 {guard_name}"
        # message_segment = Seg(type="text", data=text)
        message_segment = Seg(
            "seglist",
            [
                Seg(type="guard", data=text),
                Seg("priority_info", {"message_type": "vip", "priority": guard_money_map.get(self.guard_level, 138)}),
            ],
        )

        # 记录消息日志
        self.logger.info(f"[大航海] {self.uname}: {text}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)
