"""IndexTTS 引擎（本地常驻 HTTP 服务，自建 FastAPI 包装对接官方 Python API）。

- ``provider``：引擎本体（生命周期 / 合成播放 / 事件发布）
- ``client``：HTTP 流式客户端（引擎私有实现，不对外暴露）
"""

from .provider import IndexTTSProvider, create_index_tts_provider

__all__ = ["IndexTTSProvider", "create_index_tts_provider"]
