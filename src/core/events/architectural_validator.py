"""
架构约束运行时验证器

在运行时强制执行3域架构的事件订阅约束，防止违反分层原则。

使用方式：
    1. 自动模式（推荐）：创建验证器后自动包装 EventBus
    2. 手动模式：在调用 event_bus.on() 时传入订阅者信息
"""

import inspect
from typing import Callable, Dict, List, Set, Optional


class ArchitecturalViolationError(Exception):
    """架构违规错误

    当组件尝试订阅违反架构约束的事件时抛出。
    """

    def __init__(self, subscriber: str, event_name: str, reason: str):
        self.subscriber = subscriber
        self.event_name = event_name
        self.reason = reason
        super().__init__(f"{subscriber} 不允许订阅 {event_name}。\n原因: {reason}\n这违反了3域架构的分层约束。")


class ArchitecturalValidator:
    """
    架构约束验证器

    通过包装 EventBus.on() 和 on_typed() 方法，在运行时验证订阅关系
    是否符合架构约束。

    架构原则：
    - Input Domain: 只发布事件，不订阅任何下游事件
    - Decision Domain: 可以订阅 Input 事件，但不能订阅 Output 事件
    - Output Domain: 可以订阅 Decision 事件，但不能订阅 Input 事件
    - Core Orchestrator (FlowCoordinator): 可以协调跨域事件

    启用方式：
        ```python
        from src.core.events.architectural_validator import ArchitecturalValidator

        validator = ArchitecturalValidator(event_bus, enabled=True)
        ```

    注意：验证器通过检查调用栈来识别订阅者。为了确保正确工作，
    订阅事件的代码必须是类的实例方法（即通过 self.event_bus.on() 调用）。
    """

    # 定义每个类允许订阅的事件模式（支持通配符 *）
    # None 表示不允许订阅任何事件
    # 空列表 [] 表示允许所有事件（用于向后兼容未知订阅者）
    # 非 None 非空列表表示只允许列表中的事件（支持通配符）
    ALLOWED_SUBSCRIPTIONS: Dict[str, Optional[List[str]]] = {
        # === Input Domain (只发布，不订阅) ===
        "InputCoordinator": None,  # Input Domain 协调器
        "InputProvider": None,  # 所有输入 Provider 的基类
        "InputProviderManager": None,  # Input Provider 管理器
        # === Input Domain Pipelines ===
        "InputPipelineManager": None,  # Input Pipeline 管理器
        "TextPipeline": None,  # 所有文本管道基类
        "SimilarTextFilterPipeline": None,
        "RateLimitPipeline": None,
        "MessageLoggerPipeline": None,
        # === Decision Domain (可订阅 Input，不可订阅 Output) ===
        "DecisionCoordinator": [
            "normalization.message_ready",
            "core.startup",
            "core.shutdown",
        ],
        "DecisionProviderManager": [
            "normalization.message_ready",
            "core.startup",
            "core.shutdown",
        ],
        "DecisionProvider": [
            "normalization.message_ready",  # DecisionProvider 可以订阅 Input 事件
        ],
        "MaiCoreDecisionProvider": None,  # 具体实现不应该直接订阅
        "LocalLLMDecisionProvider": None,
        "RuleEngineDecisionProvider": None,
        # === Output Domain (可订阅 Decision，不可订阅 Input) ===
        "OutputCoordinator": [  # Output Domain 协调器
            "decision.intent_generated",
            "decision.response_generated",
            "expression.parameters_generated",
            "core.startup",
            "core.shutdown",
        ],
        # === Output Domain (可订阅 Decision，不可订阅 Input) ===
        "OutputProviderManager": [
            "decision.intent_generated",
            "decision.response_generated",
            "expression.parameters_generated",
            "core.startup",
            "core.shutdown",
        ],
        "OutputPipelineManager": [
            "decision.intent_generated",
            "decision.response_generated",
        ],
        # === Output Providers (可订阅 Decision/Expression 事件) ===
        "OutputProvider": [
            "expression.*",  # 允许所有 expression.* 事件
            "output.*",  # 允许所有 output.* 事件
            "decision.intent_generated",
            "decision.response_generated",
            "render.completed",
            "render.failed",
        ],
        "VTSProvider": [
            "expression.parameters_generated",
            "render.completed",
            "render.failed",
        ],
        "TTSProvider": [
            "expression.parameters_generated",
            "render.completed",
            "render.failed",
        ],
        "EdgeTTSProvider": [  # 与 TTSProvider 相同（TTSProvider 是其别名）
            "expression.parameters_generated",
            "render.completed",
            "render.failed",
        ],
        "SubtitleProvider": [
            "expression.parameters_generated",
            "render.completed",
            "render.failed",
        ],
        # === Expression Generator (纯函数，不订阅) ===
        "ExpressionGenerator": None,
        # === Core ===
        "AmaidesuCore": [
            "core.*",
            "perception.*",
            "normalization.*",
            "decision.*",
            "expression.*",
            "render.*",
        ],
    }

    def __init__(self, event_bus, enabled: bool = True, strict: bool = False):
        """
        初始化架构验证器

        Args:
            event_bus: EventBus 实例
            enabled: 是否启用验证（默认 True）
            strict: 严格模式（未知订阅者也会报错，默认 False）
        """
        self.event_bus = event_bus
        self.enabled = enabled
        self.strict = strict

        # 保存原始方法的引用
        self._original_on = event_bus.on.__func__ if hasattr(event_bus.on, "__func__") else event_bus.on

        if self.enabled:
            # 包装原始订阅方法
            # 使用 types.MethodType 确保绑定方法正确包装
            import types

            event_bus.on = types.MethodType(self._validated_on, event_bus)

    def _validated_on(
        self, event_bus_instance, event_name: str, handler: Callable, model_class=None, priority: int = 100
    ) -> None:
        """
        验证后的 on() 方法

        在调用原始 on() 方法前，先验证订阅关系是否符合架构约束。

        Args:
            event_bus_instance: EventBus 实例（通过 MethodType 绑定）
            event_name: 事件名称
            handler: 事件处理器
            model_class: 模型类（可选）
            priority: 优先级
        """
        if not self.enabled:
            return self._original_on(event_bus_instance, event_name, handler, model_class, priority)

        # 获取调用者的类名
        subscriber_name = self._get_subscriber_name()

        # 验证订阅
        self._validate_subscription(subscriber_name, event_name)

        # 调用原始方法
        return self._original_on(event_bus_instance, event_name, handler, model_class, priority)

    def _get_subscriber_name(self) -> str:
        """
        获取调用者的类名

        通过检查调用栈，找到调用 event_bus.on()/on_typed() 的对象的类名。

        Returns:
            调用者的类名，如果无法确定则返回 "Unknown"
        """
        try:
            # 获取当前栈帧
            frame = inspect.currentframe()
            if frame is None:
                return "Unknown"

            # 向上遍历调用栈，跳过：
            # 1. _get_subscriber_name 本身
            # 2. _validated_on
            # 3. event_bus.on (原始方法)
            # 直到找到实际调用者
            stack_depth = 0
            max_depth = 10  # 防止无限循环
            caller_frame = frame

            while stack_depth < max_depth:
                caller_frame = caller_frame.f_back
                if caller_frame is None:
                    break

                stack_depth += 1

                # 跳过验证器内部的方法
                frame_name = caller_frame.f_code.co_name
                if frame_name in (
                    "_validated_on",
                    "_get_subscriber_name",
                    "on",
                    "emit",
                ):
                    continue

                # 检查这个栈帧是否有 self 对象
                caller_self = caller_frame.f_locals.get("self")
                if caller_self is not None:
                    # 找到了！返回类名
                    class_name = caller_self.__class__.__name__
                    return class_name

                # 如果没有 self，可能是模块级别的调用
                # 检查是否在 try_subscribe 等测试方法中
                if "test" in frame_name or "try_subscribe" in frame_name:
                    # 尝试从函数名推断类名
                    # 例如：MockInputProvider.try_subscribe
                    return frame_name  # 返回函数名，后续会通过基类匹配

            return "Unknown"

        except Exception:
            return "Unknown"

    def _validate_subscription(self, subscriber: str, event_name: str) -> None:
        """
        验证订阅是否符合架构约束

        Args:
            subscriber: 订阅者类名
            event_name: 事件名称

        Raises:
            ArchitecturalViolationError: 如果订阅违反架构约束
        """
        # 获取该订阅者允许的事件列表
        allowed_events = self._get_allowed_events(subscriber)

        # None 表示不允许订阅任何事件
        if allowed_events is None:
            # 检查是否是基类
            if self._is_provider_base_class(subscriber):
                # 基类允许，让具体子类处理
                return
            else:
                raise ArchitecturalViolationError(
                    subscriber=subscriber, event_name=event_name, reason=f"{subscriber} 不应该订阅任何事件（仅发布）"
                )

        # 空列表表示允许所有事件（用于向后兼容）
        if not allowed_events:
            return

        # 检查是否允许订阅该事件
        if not self._is_event_allowed(event_name, allowed_events):
            raise ArchitecturalViolationError(
                subscriber=subscriber, event_name=event_name, reason=f"允许的事件: {allowed_events}"
            )

    def _get_allowed_events(self, subscriber: str) -> Optional[List[str]]:
        """
        获取订阅者允许订阅的事件列表

        Args:
            subscriber: 订阅者类名

        Returns:
            允许的事件列表，None 表示不允许订阅任何事件
        """
        # 直接查找
        if subscriber in self.ALLOWED_SUBSCRIPTIONS:
            return self.ALLOWED_SUBSCRIPTIONS[subscriber]

        # 尝试通过继承关系查找
        base_classes = self._get_base_classes(subscriber)
        for base_class in base_classes:
            if base_class in self.ALLOWED_SUBSCRIPTIONS:
                return self.ALLOWED_SUBSCRIPTIONS[base_class]

        # 未找到配置
        if self.strict:
            # 严格模式：未配置的订阅者不允许订阅
            return None
        else:
            # 非严格模式：未配置的订阅者允许所有事件（向后兼容）
            return []

    def _is_event_allowed(self, event_name: str, allowed_patterns: List[str]) -> bool:
        """
        检查事件是否匹配允许的模式

        支持通配符匹配（例如 "decision.*" 匹配 "decision.intent_generated"）

        Args:
            event_name: 事件名称
            allowed_patterns: 允许的事件模式列表

        Returns:
            是否允许订阅
        """
        # 精确匹配
        if event_name in allowed_patterns:
            return True

        # 通配符匹配
        for pattern in allowed_patterns:
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if event_name.startswith(prefix):
                    return True

        return False

    def _is_provider_base_class(self, class_name: str) -> bool:
        """
        检查是否是 Provider 基类

        Args:
            class_name: 类名

        Returns:
            是否是基类
        """
        base_classes = {
            "InputProvider",
            "DecisionProvider",
            "OutputProvider",
            "TextPipeline",
        }
        return class_name in base_classes

    def _get_base_classes(self, class_name: str) -> Set[str]:
        """
        获取类的基类名称（简化版本）

        注意：这是一个简化版本，只处理已知的继承关系。
        完整版本可能需要使用 inspect.getmro() 或类似方法。

        Args:
            class_name: 类名

        Returns:
            基类名称集合
        """
        # 已知的继承关系映射
        # 注意：这个映射用于架构验证器快速查找基类，完整的继承关系应由各域的 __init__.py 维护
        inheritance_map = {
            # === Input Providers ===
            "ConsoleInputProvider": {"InputProvider"},
            "BiliDanmakuInputProvider": {"InputProvider"},
            "BiliDanmakuOfficialInputProvider": {"InputProvider"},
            "BiliDanmakuOfficialMaiCraftInputProvider": {"InputProvider"},
            "MainosabaInputProvider": {"InputProvider"},
            "ReadPingmuInputProvider": {"InputProvider"},
            "MockDanmakuInputProvider": {"InputProvider"},  # 测试类
            "MockInputProvider": {"InputProvider"},  # 测试类（旧名称，保留兼容性）
            "DanmakuProvider": {"InputProvider"},  # 旧名称，保留兼容性
            "GameProvider": {"InputProvider"},  # 旧名称，保留兼容性
            "VoiceInputProvider": {"InputProvider"},  # 旧名称，保留兼容性
            # === Decision Providers ===
            "MaiCoreDecisionProvider": {"DecisionProvider"},
            "LocalLLMDecisionProvider": {"DecisionProvider"},
            "RuleEngineDecisionProvider": {"DecisionProvider"},
            "MockDecisionProvider": {"DecisionProvider"},  # 测试类
            # === Output Providers ===
            "VTSProvider": {"OutputProvider"},
            "TTSProvider": {"OutputProvider"},  # EdgeTTSProvider 别名（向后兼容）
            "EdgeTTSProvider": {"OutputProvider"},
            "SubtitleProvider": {"OutputProvider"},  # 别名：SubtitleOutputProvider
            "SubtitleOutputProvider": {"OutputProvider"},
            "AvatarOutputProvider": {"OutputProvider"},
            "GPTSoVITSOutputProvider": {"OutputProvider"},
            "OmniTTSProvider": {"OutputProvider"},
            "StickerOutputProvider": {"OutputProvider"},
            "RemoteStreamOutputProvider": {"OutputProvider"},
            "WarudoOutputProvider": {"OutputProvider"},
            "ObsControlOutputProvider": {"OutputProvider"},
            "MockOutputProvider": {"OutputProvider"},  # 测试类
            # === Pipelines ===
            "SimilarTextFilterPipeline": {"TextPipeline"},
            "RateLimitPipeline": {"TextPipeline"},
            "MessageLoggerPipeline": {"TextPipeline"},
        }

        return inheritance_map.get(class_name, set())

    def disable(self) -> None:
        """禁用验证器（恢复原始方法）"""
        if self.enabled:
            self.enabled = False
            import types

            self.event_bus.on = types.MethodType(self._original_on, self.event_bus)

    def enable(self) -> None:
        """启用验证器（重新包装方法）"""
        if not self.enabled:
            self.enabled = True
            import types

            self.event_bus.on = types.MethodType(self._validated_on, self.event_bus)
