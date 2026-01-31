"""
决策Provider接口

定义了决策层(Decision Layer)的Provider接口。
DecisionProvider负责将CanonicalMessage转换为决策结果(MessageBase)。

示例实现:
- MaiCoreDecisionProvider: 使用WebSocket连接到MaiCore
- LocalLLMDecisionProvider: 使用本地LLM API
- RuleEngineDecisionProvider: 使用本地规则引擎
"""

from typing import Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

from maim_message import MessageBase

if TYPE_CHECKING:
    from src.canonical.canonical_message import CanonicalMessage


class DecisionProvider(ABC):
    """
    决策Provider抽象基类

    职责: 将CanonicalMessage转换为决策结果(MessageBase)

    生命周期:
    1. 实例化(__init__)
    2. 设置(setup()) - 初始化并注册到EventBus
    3. 决策(decide()) - 处理CanonicalMessage
    4. 清理(cleanup()) - 释放资源

    Attributes:
        config: Provider配置(来自decision.providers.xxx配置)
        event_bus: EventBus实例(可选,用于事件通信)
        is_setup: 是否已完成设置
    """

    def __init__(self, config: dict, event_bus: Optional = None):
        """
        初始化Provider

        Args:
            config: Provider配置(来自decision.providers.xxx配置)
            event_bus: EventBus实例(可选,用于事件通信)
        """
        self.config = config
        self.event_bus = event_bus
        self.is_setup = False

    async def setup(self, event_bus, config: dict):
        """
        设置Provider

        Args:
            event_bus: EventBus实例
            config: Provider配置

        Raises:
            ConnectionError: 如果无法连接到决策服务
        """
        self.event_bus = event_bus
        self.config = config
        await self._setup_internal()
        self.is_setup = True

    @abstractmethod
    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """
        决策

        根据CanonicalMessage生成决策结果(MessageBase)。

        Args:
            canonical_message: 标准消息

        Returns:
            MessageBase: 决策结果

        Raises:
            ValueError: 如果输入消息无效
            Exception: 决策过程中的错误
        """
        pass

    async def _setup_internal(self):  # noqa: B027
        """内部设置逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行初始化逻辑,如连接到服务、加载模型等。
        ...

    async def cleanup(self):
        """
        清理资源

        停止Provider并释放所有资源。
        """
        await self._cleanup_internal()
        self.is_setup = False

    async def _cleanup_internal(self):  # noqa: B027
        """内部清理逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行清理逻辑,如关闭连接、释放资源等。
        ...
