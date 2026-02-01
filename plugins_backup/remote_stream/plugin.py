#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Amaidesu Remote Stream Plugin - 远程流媒体插件（新架构）

通过WebSocket协议实现与边缘设备的音视频双向传输。
"""

from typing import Dict, Any, List

from src.utils.logger import get_logger
from .providers.remote_stream_provider import RemoteStreamProvider


class RemoteStreamPlugin:
    """远程流媒体插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("RemoteStreamPlugin")

        self.event_bus = None
        self._providers: List[Any] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含RemoteStreamProvider）
        """
        self.event_bus = event_bus

        # 创建 RemoteStreamProvider
        try:
            provider = RemoteStreamProvider(self.config)
            await provider.setup(event_bus)
            self._providers.append(provider)
            self.logger.info("RemoteStreamProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 RemoteStreamProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 RemoteStreamPlugin...")

        # 清理所有 Provider
        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()

        self.logger.info("RemoteStreamPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "RemoteStream",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "远程流媒体插件 - 音视频双向传输",
            "category": "hardware",
            "api_version": "2.0",
        }


plugin_entrypoint = RemoteStreamPlugin
