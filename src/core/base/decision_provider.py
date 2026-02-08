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
- LocalLLMDecisionProvider: 使用本地LLM API (直接生成Intent)
- RuleEngineDecisionProvider: 使用本地规则引擎
"""

from typing import Optional, TYPE_CHECKING, Dict, Any
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from src.core.base.normalized_message import NormalizedMessage
    from src.domains.decision.intent import Intent


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

    async def setup(self, event_bus, config: Optional[dict] = None, dependencies: Optional[dict] = None):
        """
        设置Provider

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

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息（子类重写）

        用于显式注册模式，避免模块导入时的自动注册。

        Returns:
            注册信息字典，包含:
            - layer: "decision"
            - name: Provider 名称（唯一标识符）
            - class: Provider 类
            - source: 注册来源（如 "builtin:maicore"）

        Raises:
            NotImplementedError: 如果子类未实现此方法

        Example:
            @classmethod
            def get_registration_info(cls):
                return {
                    "layer": "decision",
                    "name": "maicore",
                    "class": cls,
                    "source": "builtin:maicore"
                }
        """
        raise NotImplementedError(
            f"{cls.__name__} 必须实现 get_registration_info() 类方法以支持显式注册。"
            "如果使用自动注册模式，可以在 __init__.py 中直接调用 ProviderRegistry.register_decision()。"
        )
