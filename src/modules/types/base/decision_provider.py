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
from typing import TYPE_CHECKING

from src.modules.di.context import ProviderContext

if TYPE_CHECKING:
    from src.modules.types.base.normalized_message import NormalizedMessage


class DecisionProvider(ABC):
    """决策Provider抽象基类 - 依赖注入版本"""

    def __init__(
        self,
        config: dict,
        context: ProviderContext = None,
    ):
        """
        初始化Provider

        Args:
            config: Provider配置(来自decision.providers.xxx配置)
            context: 依赖注入上下文（必填）
        """
        if context is None:
            raise ValueError("DecisionProvider 必须接收 context 参数")

        self.config = config
        self.context = context
        self.is_started = False
        self._override_event_bus = None  # 用于测试时覆盖 event_bus

    @property
    def event_bus(self):
        """EventBus实例"""
        # 优先使用覆盖值（用于测试）
        if self._override_event_bus is not None:
            return self._override_event_bus
        return self.context.event_bus

    async def init(self) -> None:  # noqa: B027
        """
        初始化 Provider（子类可重写）

        执行初始化逻辑，如建立连接、加载模型等。
        子类应重写此方法来实现具体的初始化逻辑。
        """
        pass

    async def start(self, event_bus=None, config=None, dependencies=None) -> None:
        """
        启动 Provider

        依赖已在构造时通过 context 注入，此方法仅进行初始化。

        Args:
            event_bus: 可选的 EventBus 实例（用于测试兼容性）
            config: 可选的配置覆盖（用于测试）
            dependencies: 可选的依赖注入（用于测试）

        Raises:
            ConnectionError: 如果无法连接到决策服务
        """
        # 如果传入 event_bus，设置覆盖值
        if event_bus is not None:
            self._override_event_bus = event_bus

        # 允许测试时注入依赖
        if dependencies is not None:
            self._dependencies = dependencies

        # 允许测试时覆盖配置
        if config is not None:
            self.config = config

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
