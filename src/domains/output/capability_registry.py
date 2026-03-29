"""
Output Capability Registry - Output Provider 能力注册表

管理所有 Output Provider 的能力（支持的情感和动作），供决策系统查询。

使用方式：
    from src.domains.output.capability_registry import OutputCapabilityRegistry, ProviderCapability

    # 注册 Provider 能力
    registry.register(ProviderCapability(
        provider_name="tts",
        emotions=["happy", "sad", "angry"],
        actions=["speak", "whisper"]
    ))

    # 查询 Provider 能力
    capability = registry.get("tts")
    if capability and "happy" in capability.emotions:
        # 使用 TTS 的 happy 情感
        pass
"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from src.modules.logging import get_logger


class ProviderCapability(BaseModel):
    """
    Provider 能力模型

    Attributes:
        provider_name: Provider 名称（唯一标识符）
        emotions: 支持的情感列表
        actions: 支持的动作列表
    """

    provider_name: str
    emotions: list[str]
    actions: list[str]


class OutputCapabilityRegistry:
    """
    Output Provider 能力注册表

    设计原则：
    1. 存储 Provider 的能力信息（支持的情感和动作）
    2. 供决策系统查询以选择合适的 Output Provider
    3. 不存储 Provider 实例，只存储能力描述

    使用方式：
        registry = OutputCapabilityRegistry()
        registry.register(ProviderCapability(
            provider_name="tts",
            emotions=["happy", "sad"],
            actions=["speak"]
        ))

        # 查询所有能力
        all_caps = registry.get_all()

        # 查询特定 Provider
        cap = registry.get("tts")
    """

    # 类级别的注册表
    _capabilities: Dict[str, ProviderCapability] = {}

    _logger = get_logger("OutputCapabilityRegistry")

    @classmethod
    def register(cls, capability: ProviderCapability) -> None:
        """
        注册 Provider 能力

        Args:
            capability: Provider 能力模型

        Note:
            如果同名 Provider 已注册，会覆盖原有能力
        """
        if capability.provider_name in cls._capabilities:
            cls._logger.warning(
                f"Provider '{capability.provider_name}' already registered, "
                f"overwriting with emotions={capability.emotions}, actions={capability.actions}"
            )

        cls._capabilities[capability.provider_name] = capability
        cls._logger.debug(
            f"Registered capability for provider: {capability.provider_name} "
            f"(emotions={capability.emotions}, actions={capability.actions})"
        )

    @classmethod
    def get_all(cls) -> List[ProviderCapability]:
        """
        获取所有已注册的 Provider 能力

        Returns:
            所有 Provider 能力的列表
        """
        return list(cls._capabilities.values())

    @classmethod
    def get(cls, provider_name: str) -> Optional[ProviderCapability]:
        """
        获取指定 Provider 的能力

        Args:
            provider_name: Provider 名称

        Returns:
            Provider 能力，如果不存在则返回 None
        """
        return cls._capabilities.get(provider_name)

    @classmethod
    def clear(cls) -> None:
        """清除所有注册的 Provider 能力（主要用于测试）"""
        cls._capabilities.clear()
        cls._logger.debug("Cleared all registered capabilities")


# 便捷导入
__all__ = ["OutputCapabilityRegistry", "ProviderCapability"]
