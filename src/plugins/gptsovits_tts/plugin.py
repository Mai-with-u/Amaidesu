"""
GPT-SoVITS TTS Plugin - 新架构版本

职责:
- 使用GPT-SoVITS引擎进行文本转语音
- 流式TTS和音频播放
- 集成text_cleanup、vts_lip_sync、subtitle_service等服务

迁移说明:
- 从旧BasePlugin架构迁移到新Plugin协议
- 不继承任何基类，通过event_bus和config依赖注入
- TTS功能封装在GPTSoVITSOutputProvider中
"""

import os
from typing import Dict, Any, List

# --- Amaidesu Core Imports ---
from src.utils.logger import get_logger

# --- Provider Import ---
from src.plugins.gptsovits_tts.providers.gptsovits_tts_provider import GPTSoVITSOutputProvider

# --- Configuration Imports ---
import tomllib

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        print("依赖缺失: 请运行 'pip install toml' 来加载 TTS 插件配置。", file=sys.stderr)
        tomllib = None


# --- Plugin Configuration Loading ---
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_PLUGIN_DIR, "config.toml")


def get_default_config() -> Dict[str, Any]:
    """获取默认配置"""
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = tomllib.load(f)
        return config
    except FileNotFoundError:
        print(f"配置文件不存在: {_CONFIG_FILE}")
        return {}


class GPTSoVITSPlugin:
    """
    GPT-SoVITS TTS插件（新架构版本）

    新架构特点:
    - 不继承BasePlugin
    - 实现Plugin协议
    - 返回Provider列表（GPTSoVITSOutputProvider）
    - 通过event_bus和config依赖注入
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化GPT-SoVITS Plugin

        Args:
            config: 插件配置（合并全局配置和插件配置）
        """
        self.config = config
        self.logger = get_logger("GPTSoVITSPlugin")
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._providers: List[Any] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("GPTSoVITSPlugin 在配置中已禁用。")
            return

        # GPT-SoVITS API配置检查
        host = self.config.get("host")
        port = self.config.get("port")
        if not host or not port:
            self.logger.error("GPT-SoVITS API配置不完整：缺少host或port")
            self.enabled = False
            return

        # 验证配置完整性
        ref_audio_path = self.config.get("ref_audio_path", "")
        prompt_text = self.config.get("prompt_text", "")
        if not ref_audio_path or not prompt_text:
            self.logger.warning("参考音频配置不完整：建议设置ref_audio_path和prompt_text")

        self.logger.info(f"GPT-SoVITS Plugin配置: host={host}, port={port}")

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含GPTSoVITSOutputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            self.logger.warning("GPTSoVITSPlugin 未启用，不创建Provider。")
            return []

        # 创建 GPTSoVITSOutputProvider
        try:
            provider = GPTSoVITSOutputProvider(self.config)
            self._providers.append(provider)
            self.logger.info("GPTSoVITSOutputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 GPTSoVITSOutputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info(f"开始清理 {self.__class__.__name__}...")

        # 清理所有 Provider
        for provider in self._providers:
            try:
                if hasattr(provider, "cleanup"):
                    await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()

        self.logger.info(f"{self.__class__.__name__} 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "GPTSoVITSPlugin",
            "version": "2.0.0",
            "author": "MaiBot Team",
            "description": "GPT-SoVITS TTS插件（新Plugin架构）",
            "category": "output",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = GPTSoVITSPlugin
