"""
TTS 管理器

提供 TTS 客户端管理功能：
- 多客户端管理
- 客户端生命周期管理
"""

from typing import Dict, Optional

from src.modules.logging import get_logger
from src.modules.tts.gptsovits_client import GPTSoVITSClient


class TTSManager:
    """
    TTS 客户端管理器

    管理多个 TTS 客户端实例，提供统一的访问接口。
    """

    _instance: Optional["TTSManager"] = None

    def __new__(cls) -> "TTSManager":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """初始化管理器"""
        if self._initialized:
            return

        self.logger = get_logger("TTSManager")
        self._clients: Dict[str, GPTSoVITSClient] = {}
        self._initialized = True
        self.logger.info("TTSManager 初始化完成")

    def get_client(
        self,
        name: str = "default",
        host: str = "127.0.0.1",
        port: int = 9880,
    ) -> GPTSoVITSClient:
        """
        获取或创建 TTS 客户端

        Args:
            name: 客户端名称
            host: API 服务器地址
            port: API 服务器端口

        Returns:
            GPTSoVITSClient 实例
        """
        if name not in self._clients:
            self._clients[name] = GPTSoVITSClient(host=host, port=port)
            self.logger.debug(f"创建 TTS 客户端: {name} -> {host}:{port}")
        return self._clients[name]

    def remove_client(self, name: str) -> None:
        """
        移除 TTS 客户端

        Args:
            name: 客户端名称
        """
        if name in self._clients:
            del self._clients[name]
            self.logger.debug(f"移除 TTS 客户端: {name}")

    def cleanup(self) -> None:
        """清理所有客户端"""
        self._clients.clear()
        self.logger.info("TTSManager 清理完成")

    def get_all_clients(self) -> Dict[str, GPTSoVITSClient]:
        """
        获取所有客户端

        Returns:
            客户端字典
        """
        return self._clients.copy()


# 全局实例
_tts_manager: Optional[TTSManager] = None


def get_tts_manager() -> TTSManager:
    """
    获取 TTS 管理器全局实例

    Returns:
        TTSManager 实例
    """
    global _tts_manager
    if _tts_manager is None:
        _tts_manager = TTSManager()
    return _tts_manager
