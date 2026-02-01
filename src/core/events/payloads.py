"""
Event Payloads 类型定义

为 EventBus 事件提供类型注解，改善 IDE 自动完成和类型检查。

使用示例:
    from src.core.events.payloads import CommandRouterData

    def handle_command(event_data: CommandRouterData, **kwargs):
        command = event_data.command  # IDE 可以自动提示
        print(f"Received command: {command}")
"""

from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from maim_message import MessageBase


@dataclass
class CommandRouterData:
    """命令路由事件数据"""
    command: str
    prefix: str
    message: "MessageBase"
    user_id: Optional[str]
    username: Optional[str]
    timestamp: float


@dataclass
class TTSStartedData:
    """TTS 开始事件数据"""
    text: str
    voice: str


@dataclass
class TTSErrorData:
    """TTS 错误事件数据"""
    text: str
    error: str


@dataclass
class VTSTriggerHotkeyData:
    """VTS 触发热键事件数据"""
    hotkey_name: str


# 便捷类型别名，用于类型注解
EventDataType = CommandRouterData | TTSStartedData | TTSErrorData | VTSTriggerHotkeyData | dict
