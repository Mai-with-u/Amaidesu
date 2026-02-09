"""
动作工厂系统模块

定义抽象工厂接口和具体工厂实现。
支持通过配置切换不同的动作实现系列。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.core.types import IntentAction
from src.core.utils.logger import get_logger

from .action_types import MaicraftActionType

if TYPE_CHECKING:
    from src.core.base.normalized_message import NormalizedMessage


class AbstractActionFactory(ABC):
    """
    抽象动作工厂基类。

    定义工厂接口，具体工厂实现负责创建特定类型的动作。
    """

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)

    @abstractmethod
    def get_factory_type(self) -> str:
        """
        获取工厂类型标识符。

        Returns:
            工厂类型字符串，如 "log", "mcp"
        """
        pass

    async def initialize(self) -> bool:
        """
        初始化工厂。

        Returns:
            是否初始化成功
        """
        return True

    async def cleanup(self) -> None:  # noqa: B027
        """清理工厂资源（子类可选重写）"""
        ...

    @abstractmethod
    def create_chat_action(self, message: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建聊天动作。

        Args:
            message: 聊天消息内容
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        pass

    @abstractmethod
    def create_attack_action(self, mob_name: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建攻击动作。

        Args:
            mob_name: 生物名称
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        pass

    def create_action(
        self,
        action_type: MaicraftActionType,
        params: Dict[str, Any],
        original_message: "NormalizedMessage",
    ) -> Optional[IntentAction]:
        """
        根据动作类型创建对应的动作。

        Args:
            action_type: 动作类型
            params: 动作参数
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象，如果不支持该类型则返回 None
        """
        if action_type == MaicraftActionType.CHAT:
            message = params.get("message", "")
            return self.create_chat_action(message, original_message)
        elif action_type == MaicraftActionType.ATTACK:
            mob_name = params.get("mob_name", "")
            return self.create_attack_action(mob_name, original_message)
        else:
            self.logger.warning(f"不支持的动作类型: {action_type}")
            return None


class LogActionFactory(AbstractActionFactory):
    """
    日志动作工厂。

    生成用于测试的日志动作，不执行实际操作。
    """

    def get_factory_type(self) -> str:
        return "log"

    def create_chat_action(self, message: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建日志聊天动作。

        Args:
            message: 聊天消息内容
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        self.logger.info(f"[LOG] 聊天动作: {message}")

        return IntentAction(
            type="game_action",
            params={
                "action": "chat",
                "message": message,
                "factory_type": "log",
                "source": original_message.source,
            },
            priority=50,
        )

    def create_attack_action(self, mob_name: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建日志攻击动作。

        Args:
            mob_name: 生物名称
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        self.logger.info(f"[LOG] 攻击动作: {mob_name}")

        return IntentAction(
            type="game_action",
            params={
                "action": "attack",
                "mob_name": mob_name,
                "factory_type": "log",
                "source": original_message.source,
            },
            priority=50,
        )


class McpActionFactory(AbstractActionFactory):
    """
    MCP (Model Context Protocol) 动作工厂。

    生成符合 MCP 协议的动作，用于与游戏服务交互。
    """

    def __init__(self):
        super().__init__()
        self.mcp_server_url: Optional[str] = None
        self.is_connected = False

    def get_factory_type(self) -> str:
        return "mcp"

    async def initialize(self) -> bool:
        """
        初始化 MCP 连接。

        Returns:
            是否初始化成功
        """
        # TODO: 实现 MCP 连接初始化
        # STATUS: PENDING - MCP 协议待实现
        # 目前只做日志记录
        self.logger.info("MCP 工厂初始化（当前为模拟模式）")
        self.is_connected = True
        return True

    async def cleanup(self) -> None:
        """清理 MCP 连接"""
        self.is_connected = False
        self.logger.info("MCP 工厂清理完成")

    def create_chat_action(self, message: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建 MCP 聊天动作。

        Args:
            message: 聊天消息内容
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        if not self.is_connected:
            self.logger.warning("MCP 未连接，使用模拟模式")

        self.logger.info(f"[MCP] 聊天动作: {message}")

        return IntentAction(
            type="game_action",
            params={
                "action": "chat",
                "message": message,
                "factory_type": "mcp",
                "source": original_message.source,
                "protocol": "mcp",
            },
            priority=50,
        )

    def create_attack_action(self, mob_name: str, original_message: "NormalizedMessage") -> IntentAction:
        """
        创建 MCP 攻击动作。

        Args:
            mob_name: 生物名称
            original_message: 原始标准化消息

        Returns:
            IntentAction 对象
        """
        if not self.is_connected:
            self.logger.warning("MCP 未连接，使用模拟模式")

        self.logger.info(f"[MCP] 攻击动作: {mob_name}")

        return IntentAction(
            type="game_action",
            params={
                "action": "attack",
                "mob_name": mob_name,
                "factory_type": "mcp",
                "source": original_message.source,
                "protocol": "mcp",
            },
            priority=50,
        )
