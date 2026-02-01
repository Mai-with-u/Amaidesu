"""
Rendering Providers - 渲染输出Provider实现

包含各种 OutputProvider 的具体实现：
- SubtitleOutputProvider: 字幕输出Provider
- TTSOutputProvider: TTS语音输出Provider
- VTSOutputProvider: VTS虚拟形象Provider
"""

from .subtitle_provider import SubtitleOutputProvider

__all__ = ["SubtitleOutputProvider"]
