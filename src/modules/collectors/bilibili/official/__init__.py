"""B 站官方弹幕采集器

- 继承 ``BaseCollector``（流型感知者，世界→系统入口，主动推事件）
- emit 语义域事件 ``room.message.*``（danmaku/gift/super_chat/enter），与
  ``modules/types/bili`` 的 BiliMessageType 一一对应
- 保留 ``collect()`` AsyncIterator 出口，供旧 InputCollectorManager 过渡期复用
"""

from .bili_danmaku_official_collector import BiliDanmakuOfficialCollector
from .client import BiliWebSocketClient, Proto

__all__ = [
    "BiliDanmakuOfficialCollector",
    "BiliWebSocketClient",
    "Proto",
]
