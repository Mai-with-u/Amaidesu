"""开播接口的 TTS 装配校验测试：配置启用而引擎缺席 → 409 拦下。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from src.modules.dashboard.api.sessions import _ensure_tts_assembled


class FakeConfigService:
    """配置服务替身：拍平视图的 tts 段按需返回（守卫读 get_section）。"""

    def __init__(self, tts: Dict[str, Any]) -> None:
        self._tts = tts

    def get_section(self, section: str, default: Any = None) -> Dict[str, Any]:
        return self._tts if section == "tts" else (default if default is not None else {})


class FakeServer:
    """DashboardServer 替身：只带校验读的成员。"""

    def __init__(self, config_service: Any, tts_engine: Optional[Any]) -> None:
        self.config_service = config_service
        self.tts_engine = tts_engine


def test_open_blocked_when_tts_enabled_but_engine_missing() -> None:
    server = FakeServer(FakeConfigService({"enabled": True, "provider": "gptsovits"}), None)
    with pytest.raises(HTTPException) as exc_info:
        _ensure_tts_assembled(server)
    assert exc_info.value.status_code == 409
    assert "重启" in str(exc_info.value.detail)


def test_open_allowed_when_engine_assembled() -> None:
    server = FakeServer(FakeConfigService({"enabled": True}), object())
    _ensure_tts_assembled(server)  # 不抛即通过


def test_open_allowed_when_tts_disabled() -> None:
    server = FakeServer(FakeConfigService({"enabled": False}), None)
    _ensure_tts_assembled(server)  # 配置上就没开，不拦


def test_open_allowed_when_config_service_missing() -> None:
    server = FakeServer(None, None)
    _ensure_tts_assembled(server)  # 极简启动无配置服务，校验不拦（TTS 语义缺席）
