# Bilibili 旧版弹幕采集器
# 旧版 WebSocket 作为备选采集器，与官方版并列保留（仍有用户使用）。
from .bili_danmaku_collector import BiliDanmakuCollector

__all__ = ["BiliDanmakuCollector"]
