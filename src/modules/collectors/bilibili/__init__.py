"""B 站采集器（v2 / Wave 5 迁移）

按 .omo/drafts/amaidesu-v2-migration.md §C：
- ``official/`` —— 官方版弹幕采集器（WebSocket API）
- ``legacy/`` —— 旧版弹幕采集器（轮询 API），与官方版并列保留

新代码请直接 ``from src.modules.collectors.bilibili.official import ...`` 或
``from src.modules.collectors.bilibili.legacy import ...``。
"""

from src.modules.collectors.bilibili.legacy import BiliDanmakuCollector
from src.modules.collectors.bilibili.official import BiliDanmakuOfficialCollector

__all__ = [
    "BiliDanmakuCollector",
    "BiliDanmakuOfficialCollector",
]
