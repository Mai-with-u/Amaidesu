"""
决策Provider接口

定义了决策层(Layer 3: Decision)的Provider接口。
DecisionProvider负责将NormalizedMessage转换为决策结果(Intent)。

关键变更:
- 输入: NormalizedMessage (代替 CanonicalMessage)
- 输出: Intent (代替 MessageBase)
- 异步返回: 符合AI VTuber特性

示例实现:
- MaiCoreDecisionProvider: 使用WebSocket连接到MaiCore (异步+LLM意图解析)
- LocalLLMDecisionProvider: 使用本地LLM API (直接生成Intent)
- RuleEngineDecisionProvider: 使用本地规则引擎
"""

from typing import Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.decision.intent import Intent


class DecisionProvider(ABC):
    """
    决策Provider抽象基类

    职责: 将NormalizedMessage转换为决策结果(Intent)

    生命周期:
    1. 实例化(__init__)
    2. 设置(setup()) - 初始化并注册到EventBus
    3. 决策(decide()) - 异步处理NormalizedMessage
    4. 清理(cleanup()) - 释放资源

    Attributes:
        config: Provider配置(来自decision.providers.xxx配置)
        event_bus: EventBus实例(可选,用于事件通信)
        is_setup: 是否已完成设置
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自decision.providers.xxx配置)
        """
        self.config = config
        self.event_bus = None
        self.is_setup = False

    async def setup(self, event_bus, config: Optional[dict] = None):
        """
        设置Provider

        Args:
            event_bus: EventBus实例
            config: Provider配置（可选，如果传入则覆盖构造时的配置）

        Raises:
            ConnectionError: 如果无法连接到决策服务
        """
        self.event_bus = event_bus
        if config:
            self.config = config
        await self._setup_internal()
        self.is_setup = True

    @abstractmethod
    async def decide(self, message: "NormalizedMessage") -> "Intent":
        """
        决策（异步）

        根据NormalizedMessage生成决策结果(Intent)。

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图

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
