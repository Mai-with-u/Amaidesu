"""
bilibili 采集器注册 + 基本 import 测试（Wave 5）

覆盖：
- 官方版 BiliDanmakuOfficialCollector 继承 BaseCollector
- 旧版 BiliDanmakuCollector 继承 BaseCollector
- 双方 class name 正确且可独立构造
- 事件 emit 路径配置项（emit_semantic_events）存在
"""

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.bilibili import (
    BiliDanmakuCollector,
    BiliDanmakuOfficialCollector,
)


def test_bili_official_inherits_base_collector() -> None:
    """BiliDanmakuOfficialCollector 继承 BaseCollector（v2 框架）"""
    assert issubclass(BiliDanmakuOfficialCollector, BaseCollector)


def test_bili_legacy_inherits_base_collector() -> None:
    """BiliDanmakuCollector（旧版）继承 BaseCollector（v2 框架）"""
    assert issubclass(BiliDanmakuCollector, BaseCollector)


def test_bili_official_metadata() -> None:
    """官方版元数据（name/description）正确"""
    assert BiliDanmakuOfficialCollector.name == "bili_danmaku_official"
    assert "Bilibili" in BiliDanmakuOfficialCollector.description


def test_bili_legacy_metadata() -> None:
    """旧版元数据（name/description）正确"""
    assert BiliDanmakuCollector.name == "bili_danmaku"
    assert "Bilibili" in BiliDanmakuCollector.description


def test_bili_official_has_emit_semantic_events_config() -> None:
    """官方版 ConfigSchema 含 emit_semantic_events 配置（v2 语义事件开关）"""
    cfg = BiliDanmakuOfficialCollector.ConfigSchema(
        id_code="x",
        app_id="y",
        access_key="z",
        access_key_secret="w",
    )
    assert cfg.emit_semantic_events is True


def test_bili_legacy_has_emit_semantic_events_config() -> None:
    """旧版 ConfigSchema 含 emit_semantic_events 配置"""
    cfg = BiliDanmakuCollector.ConfigSchema(room_id=12345)
    assert cfg.emit_semantic_events is True
