"""
B站消息基类

定义所有 B 站消息类型的公共接口。
"""

from abc import ABC
from enum import Enum
from functools import cached_property
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict

from src.modules.logging import get_logger


class BiliMessageType(Enum):
    """B站直播消息命令类型枚举"""

    DANMAKU = "LIVE_OPEN_PLATFORM_DM"
    ENTER = "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER"
    GIFT = "LIVE_OPEN_PLATFORM_SEND_GIFT"
    GUARD = "LIVE_OPEN_PLATFORM_GUARD"
    SUPER_CHAT = "LIVE_OPEN_PLATFORM_SUPER_CHAT"


class BiliBaseMessage(BaseModel, ABC):
    """
    所有 B 站消息类型的基类

    Attributes:
        cmd: 消息命令类型
        raw_data: 原始消息数据（完整保留）
    """

    model_config = ConfigDict(ignored_types=(cached_property,))

    cmd: str
    raw_data: Dict[str, Any] = {}

    @cached_property
    def logger(self):
        """获取 logger 实例（延迟初始化）"""
        return get_logger(self.__class__.__name__)
