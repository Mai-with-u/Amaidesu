"""GPT-SoVITS 引擎（本地声音克隆服务，对接 api_v2）。

- ``provider``：引擎本体（生命周期 / 合成播放 / 事件发布）
- ``client``：api_v2 HTTP 客户端（引擎私有实现，不对外暴露）
"""

from .provider import GPTSoVITSProvider, create_gptsovits_provider

__all__ = ["GPTSoVITSProvider", "create_gptsovits_provider"]
