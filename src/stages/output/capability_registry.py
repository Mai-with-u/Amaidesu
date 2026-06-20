"""
Output Capability Registry - Output Handler 能力注册表

管理所有 Output Handler 的能力（支持的情感和动作），供决策系统查询。

使用方式：
    from src.stages.output.capability_registry import OutputCapabilityRegistry, HandlerCapability

    # 注册 Handler 能力
    registry.register(HandlerCapability(
        name="tts",
        emotions=["happy", "sad", "angry"],
        actions=["speak", "whisper"]
    ))

    # 查询 Handler 能力
    capability = registry.get("tts")
    if capability and "happy" in capability.emotions:
        pass
"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from src.modules.logging import get_logger


class HandlerCapability(BaseModel):
    """
    Handler 能力模型

    Attributes:
        name: Handler 名称（唯一标识符）
        emotions: 支持的情感列表
        actions: 支持的动作列表
    """

    name: str
    emotions: list[str]
    actions: list[str]


class OutputCapabilityRegistry:
    """
    Output Handler 能力注册表

    设计原则：
    1. 存储 Handler 的能力信息（支持的情感和动作）
    2. 供决策系统查询以选择合适的 Output Handler
    3. 不存储 Handler 实例，只存储能力描述

    使用方式：
        registry = OutputCapabilityRegistry()
        registry.register(HandlerCapability(
            name="tts",
            emotions=["happy", "sad"],
            actions=["speak"]
        ))

        all_caps = registry.get_all()
        cap = registry.get("tts")
    """

    _capabilities: Dict[str, HandlerCapability] = {}

    _logger = get_logger("OutputCapabilityRegistry")

    @classmethod
    def register(cls, capability: HandlerCapability) -> None:
        """
        注册 Handler 能力

        Args:
            capability: Handler 能力模型

        Note:
            如果同名 Handler 已注册，会覆盖原有能力
        """
        if capability.name in cls._capabilities:
            cls._logger.warning(
                f"Handler '{capability.name}' already registered, "
                f"overwriting with emotions={capability.emotions}, actions={capability.actions}"
            )

        cls._capabilities[capability.name] = capability
        cls._logger.debug(
            f"Registered capability for handler: {capability.name} "
            f"(emotions={capability.emotions}, actions={capability.actions})"
        )

    @classmethod
    def get_all(cls) -> List[HandlerCapability]:
        """获取所有已注册的 Handler 能力"""
        return list(cls._capabilities.values())

    @classmethod
    def get(cls, name: str) -> Optional[HandlerCapability]:
        """
        获取指定 Handler 的能力

        Args:
            name: Handler 名称

        Returns:
            Handler 能力，如果不存在则返回 None
        """
        return cls._capabilities.get(name)

    @classmethod
    def clear(cls) -> None:
        """清除所有注册的 Handler 能力（主要用于测试）"""
        cls._capabilities.clear()
        cls._logger.debug("Cleared all registered capabilities")


__all__ = ["OutputCapabilityRegistry", "HandlerCapability"]
