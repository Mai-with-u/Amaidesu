"""
Bilibili 直播弹幕插件（官方WebSocket版）

基于 Bilibili 官方开放平台 WebSocket API 获取实时弹幕和其他直播事件。
结合了 selenium 版本的优秀架构设计，提供稳定可靠的弹幕获取服务。

主要功能：
- 实时获取弹幕消息
- 支持礼物、守护、醒目留言等事件
- 完善的消息缓存机制
- 优雅的关闭和错误处理
- 支持模板信息和上下文标签
"""

from .plugin import BiliDanmakuOfficialPlugin

__version__ = "1.0.0"
__author__ = "Amaidesu"
__description__ = "Bilibili 直播弹幕插件（官方WebSocket版）"

plugin_entrypoint = BiliDanmakuOfficialPlugin
