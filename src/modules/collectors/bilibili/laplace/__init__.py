"""LAPLACE Event Bridge 采集器

LAPLACE Chat（https://laplace.live/chat）是浏览器中的 B 站直播聊天面板，
把弹幕、SC、礼物分栏显示，并提供 OBS 覆盖层、TTS、翻译等能力。
LAPLACE Event Bridge（https://laplace.live/chat/event-bridge）是它的
WebSocket 事件桥：把 LAPLACE Chat 收到的实时 B 站直播事件转给本地客户端。

- ``client.py``：以 client 角色连接桥接服务器（如官方 ``leb-server``）的 WebSocket 客户端
- ``events.py``：LAPLACE 事件模型与 ``room.message.*`` 载荷映射
- ``bili_danmaku_laplace_collector.py``：采集器装配与生命周期
"""

from src.modules.collectors.bilibili.laplace.bili_danmaku_laplace_collector import (
    BiliDanmakuLaplaceCollector,
)

__all__ = ["BiliDanmakuLaplaceCollector"]
