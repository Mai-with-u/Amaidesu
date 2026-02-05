from typing import Optional, Dict, Any
from dataclasses import dataclass
from maim_message import MessageBase, UserInfo, BaseMessageInfo, GroupInfo, FormatInfo
from enum import Enum
import time
from src.core.utils.logger import get_logger


class BiliMessageType(Enum):
    """B站直播消息命令类型枚举"""

    DANMAKU = "LIVE_OPEN_PLATFORM_DM"  # 弹幕消息
    ENTER = "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER"  # 进入直播间
    GIFT = "LIVE_OPEN_PLATFORM_SEND_GIFT"  # 礼物消息
    GUARD = "LIVE_OPEN_PLATFORM_GUARD"  # 大航海消息
    SUPER_CHAT = "LIVE_OPEN_PLATFORM_SUPER_CHAT"  # 醒目留言


@dataclass
class BiliBaseMessage:
    """所有bilibili消息类型的基类"""

    cmd: str
    raw_data: Dict[str, Any]

    def __post_init__(self):
        """dataclass 初始化后调用，设置logger等基础属性"""
        self.logger = get_logger(self.__class__.__name__)

    def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
    ) -> MessageBase:
        """
        构建MessageBase对象，子类需要重写此方法
        """
        raise NotImplementedError("子类必须实现 to_message_base 方法")

    def _create_user_info(self, core) -> UserInfo:
        """创建用户信息对象"""
        uname = self.uname if hasattr(self, "uname") else "未知用户"
        uid = self.open_id if hasattr(self, "open_id") else 0
        user_id = str(uid) if uid else f"bili_{uname}"

        return UserInfo(
            platform=core.platform,
            user_id=user_id,
            user_nickname=uname,
            user_cardname=uname,
        )

    def _generate_message_id(self) -> str:
        """生成消息ID"""

        if hasattr(self, "msg_id") and self.msg_id:
            return self.msg_id

        room_id = self.room_id if hasattr(self, "room_id") else ""
        timestamp = self.timestamp if hasattr(self, "timestamp") else int(time.time())
        uid = self.open_id if hasattr(self, "open_id") else 0

        return f"bili_live_{room_id}_{timestamp}_{hash(str(uid)) % 10000}"

    async def _create_base_message_info(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        maimcore_reply_probability_gain: float = 0,
    ) -> BaseMessageInfo:
        """创建基础消息信息对象"""

        user_info = self._create_user_info(core)
        message_id = self._generate_message_id()
        room_id = self.room_id if hasattr(self, "room_id") else ""
        timestamp = self.timestamp if hasattr(self, "timestamp") else int(time.time())

        # 群组信息（可选）
        group_info = None
        if config.get("enable_group_info", False):
            group_info = GroupInfo(
                platform=core.platform,
                group_id=config.get("group_id", str(room_id)),
                group_name=config.get("group_name", f"bili_{room_id}"),
            )

        # 格式信息
        format_info = FormatInfo(
            content_format=config.get("content_format", config.get("accept_format", ["text"])),
            accept_format=config.get("accept_format", config.get("content_format", ["text"])),
        )

        # 附加配置
        additional_config = config.get("additional_config", {}).copy()
        if maimcore_reply_probability_gain > 0 and maimcore_reply_probability_gain <= 1:
            additional_config["maimcore_reply_probability_gain"] = maimcore_reply_probability_gain

        # 模板信息已移除
        template_info = None

        return BaseMessageInfo(
            platform=core.platform,
            message_id=message_id,
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            template_info=template_info,
            format_info=format_info,
            additional_config=additional_config,
        )
