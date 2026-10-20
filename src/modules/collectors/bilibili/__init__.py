"""B 站采集器

- ``official/`` —— 官方版弹幕采集器（WebSocket API）
- ``legacy/`` —— 旧版弹幕采集器（轮询 API），与官方版并列保留
- ``laplace/`` —— LAPLACE Event Bridge 弹幕采集器（LAPLACE Chat 的 WebSocket 事件桥；client 角色连桥接服务器）

新代码请直接 ``from src.modules.collectors.bilibili.official import ...``、
``from src.modules.collectors.bilibili.legacy import ...`` 或
``from src.modules.collectors.bilibili.laplace import ...``。
"""

from src.modules.collectors.bilibili.laplace import BiliDanmakuLaplaceCollector
from src.modules.collectors.bilibili.legacy import BiliDanmakuCollector
from src.modules.collectors.bilibili.official import BiliDanmakuOfficialCollector

__all__ = [
    "BiliDanmakuCollector",
    "BiliDanmakuLaplaceCollector",
    "BiliDanmakuOfficialCollector",
]
