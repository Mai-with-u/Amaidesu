"""
B站消息类型过滤配置

提供消息类型过滤功能。
"""

from typing import Any, Dict, Set

from src.modules.logging import get_logger

from .base import BiliMessageType


class BiliMessageTypeConfig:
    """
    B站消息类型配置

    管理哪些消息类型应该被处理。
    """

    # 默认配置：弹幕始终处理
    DEFAULT_CONFIG: Dict[str, bool] = {
        BiliMessageType.DANMAKU.value: True,
        BiliMessageType.ENTER.value: True,
        BiliMessageType.GIFT.value: True,
        BiliMessageType.GUARD.value: True,
        BiliMessageType.SUPER_CHAT.value: True,
    }

    # 配置键名映射
    CONFIG_KEY_MAPPING: Dict[str, str] = {
        BiliMessageType.ENTER.value: "handle_enter_messages",
        BiliMessageType.GIFT.value: "handle_gift_messages",
        BiliMessageType.GUARD.value: "handle_guard_messages",
        BiliMessageType.SUPER_CHAT.value: "handle_superchat_messages",
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化消息类型配置

        Args:
            config: Provider 配置字典
        """
        self.logger = get_logger("BiliMessageTypeConfig")
        self._config = self._build_config(config)

    def _build_config(self, provider_config: Dict[str, Any]) -> Dict[str, bool]:
        """构建消息类型配置"""
        config = self.DEFAULT_CONFIG.copy()

        for cmd, config_key in self.CONFIG_KEY_MAPPING.items():
            if config_key in provider_config:
                config[cmd] = provider_config[config_key]

        return config

    def should_handle(self, cmd: str) -> bool:
        """检查是否应该处理此消息类型"""
        result = self._config.get(cmd, False)
        if not result:
            self.logger.debug(f"消息类型 {cmd} 已被配置为不处理")
        return result

    def get_enabled_types(self) -> Set[str]:
        """获取所有启用的消息类型"""
        return {cmd for cmd, enabled in self._config.items() if enabled}
