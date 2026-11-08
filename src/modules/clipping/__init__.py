"""直播自动切片：AI 提议值得切片的时段，运营在 WebUI 微调后调用本机 ffmpeg 切出。

- ``config_schema.py``：``[clipping]`` 配置 Schema（infra.toml）
- ``recording_matcher.py``：录播姬文件名解析与场次时间窗匹配
- ``selector.py``：AI 选段（基于本场事件时间线，非流式 LLM 调用）
- ``cutter.py``：ffmpeg 子进程切割
- ``service.py``：编排（下播自动选段、WebUI 触发的选段与切割、后台任务登记）
"""

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.service import ClippingService

__all__ = ["ClippingConfigSchema", "ClippingService"]
