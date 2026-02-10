from typing import Optional, Dict, Any
from functools import cached_property
from pydantic import BaseModel, ConfigDict
from maim_message import MessageBase, UserInfo, BaseMessageInfo, GroupInfo, FormatInfo, TemplateInfo
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


class BiliBaseMessage(BaseModel):
    """所有bilibili消息类型的基类"""

    model_config = ConfigDict(ignored_types=(cached_property,))

    cmd: str
    raw_data: Dict[str, Any]

    @cached_property
    def logger(self):
        """获取logger实例（延迟初始化）"""
        return get_logger(self.__class__.__name__)

    def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
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
        template_items: Optional[Dict[str, Any]] = None,
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

        # 模板信息（可选）
        template_info = await self._create_template_info(core, config, context_tags, template_items, room_id)

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

    async def _create_template_info(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
        room_id: str = "",
    ) -> Optional[TemplateInfo]:
        """创建模板信息对象

        注意: 此方法已简化，不再动态获取上下文。
        如需动态上下文，请在配置中使用 PromptManager 模板变量，
        例如在 template_items 中使用 {{context.summary}} 等占位符。
        """
        if not config.get("enable_template_info", False) or not template_items:
            return None

        # 直接使用 template_items，不再调用 ContextManager
        return TemplateInfo(
            template_items=template_items,
            template_name=config.get("template_name", f"bili_official_{room_id}"),
            template_default=False,
        )
