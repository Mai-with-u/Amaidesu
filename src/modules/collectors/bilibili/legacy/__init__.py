# Bilibili 旧版弹幕采集器（v2 / Wave 5 迁移）
# 迁移自 src/stages/input/collectors/bili_danmaku/
# 与官方版并列保留（用户拍板 2026-08-23：还有人用，旧版 WebSocket 作为备选采集器）。
# 文档：.omo/drafts/amaidesu-v2-migration.md §C
from .bili_danmaku_collector import BiliDanmakuCollector

__all__ = ["BiliDanmakuCollector"]
