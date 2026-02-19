"""
edge_tts Provider 模块占位符

用于兼容 ProviderRegistry.discover_and_register_providers 中的
模块路径 "src.domains.output.providers.edge_tts"。

实际的 EdgeTTSProvider 定义和注册发生在
src.domains.output.providers.audio 包内。
"""

from src.domains.output.providers.audio import EdgeTTSProvider  # noqa: F401

