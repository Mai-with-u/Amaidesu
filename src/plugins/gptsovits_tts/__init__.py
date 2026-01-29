"""
GPTSoVITS TTS Plugin Package

导出GPTSoVITSPlugin类
"""

from .plugin import GPTSoVITSPlugin

__all__ = ["GPTSoVITSPlugin"]
plugin_entrypoint = GPTSoVITSPlugin
