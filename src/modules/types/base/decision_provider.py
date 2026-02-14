"""
决策Provider接口

定义了决策域（Decision Domain）的Provider接口。
DecisionProvider负责将NormalizedMessage转换为决策结果(Intent)。

关键变更:
- 输入: NormalizedMessage (代替 CanonicalMessage)
- 输出: Intent (代替 MessageBase)
- 异步返回: 符合AI VTuber特性

示例实现:
- MaiCoreDecisionProvider: 使用WebSocket连接到MaiCore (异步+LLM意图解析)
- LLMPDecisionProvider: 使用LLM API (直接生成Intent)
- RuleEngineDecisionProvider: 使用本地规则引擎
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.modules.types.base.normalized_message import NormalizedMessage


class DecisionProvider(ABC):
    """
    决策Provider抽象基类

    职责: 将NormalizedMessage转换为决策结果(Intent)

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 初始化并注册到EventBus
    3. 决策(decide()) - 异步处理NormalizedMessage
    4. 停止(stop()) - 释放资源

    Attributes:
        config: Provider配置(来自decision.providers.xxx配置)
        event_bus: EventBus实例(可选,用于事件通信)
        is_started: 是否已启动
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自decision.providers.xxx配置)
        """
        self.config = config
        self.event_bus = None
        self.is_started = False

    async def init(self) -> None:  # noqa: B027
        """
        初始化 Provider（子类可重写）

        执行初始化逻辑，如建立连接、加载模型等。
        子类应重写此方法来实现具体的初始化逻辑。
        """
        pass

    async def start(
        self,
        event_bus,
        config: Optional[dict] = None,
        dependencies: Optional[dict] = None,
    ) -> None:
        """
        启动 Provider

        Args:
            event_bus: EventBus实例
            config: Provider配置（可选，如果传入则覆盖构造时的配置）
            dependencies: 可选的依赖注入（如 llm_service 等）

        Raises:
            ConnectionError: 如果无法连接到决策服务
        """
        self.event_bus = event_bus
        if config:
            self.config = config
        self._dependencies = dependencies or {}
        await self.init()
        self.is_started = True

    async def stop(self) -> None:
        """
        停止 Provider

        停止Provider并释放所有资源。
        """
        await self.cleanup()
        self.is_started = False

    async def cleanup(self) -> None:  # noqa: B027
        """
        清理资源（子类可重写）

        执行清理逻辑，如关闭连接、释放资源等。
        子类应重写此方法来实现具体的清理逻辑。
        """
        pass

    @abstractmethod
    async def decide(self, message: "NormalizedMessage") -> None:
        """
        决策（异步，fire-and-forget）

        根据NormalizedMessage生成决策结果，并通过EventBus发布decision.intent事件。

        此方法是 fire-and-forget：
        - 不等待决策完成，不返回结果
        - Provider内部负责通过event_bus发布decision.intent事件
        - 不需要返回值，决策结果通过事件传递
        - 不会阻塞，每条消息独立处理（类似InputProvider）

        Args:
            message: 标准化消息

        Raises:
            ValueError: 如果输入消息无效
            Exception: 决策过程中的错误
        """
        pass
