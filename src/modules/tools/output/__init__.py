"""Output 域的"残留"工具子包入口

经过 avatar / studio / vision 域迁移后，``src/modules/tools/output/`` 仅保留
未迁移到对应域的子包（当前为 ``remote_stream/``）。avatar 域的 vts / warudo / vrchat
位于 ``src/modules/avatar/``，studio 域的 obs 位于 ``src/modules/studio/obs/``。
本文件仅作为该残留子包的转发入口，避免下游 ``from src.modules.tools.output import ...``
历史用法静默失效。

注：TTS 与字幕均已提升为基础设施。TTS 由 ``src/modules/tts/`` 自治装配；
字幕由 ``src/modules/subtitle/build_subtitle_infrastructure`` 自治装配。两者
均不通过 ``ToolRegistry`` 注册为可调用工具，而是配置驱动的语音/字幕组件。
"""

from src.modules.tools.output.remote_stream import (
    AudioConfig,
    ImageConfig,
    MessageType,
    RemoteStreamTypes,
    StreamMessage,
)

__all__ = [
    # RemoteStream
    "MessageType",
    "StreamMessage",
    "AudioConfig",
    "ImageConfig",
    "RemoteStreamTypes",
]
