"""[diagnostics] 段 Schema 默认值契约。

诊断正文是场次回看的事件侧事实源，默认不滚动删除——用户显式设置正整数
保留天数才启用清理；删除后对应场次的回看降级为仅消息明细。
"""

import pytest

from src.modules.config.core_schemas import DiagnosticsConfig


def test_default_keeps_diagnostics_forever() -> None:
    """默认 retention_days=0：诊断正文默认长期保留，不做滚动删除。"""
    config = DiagnosticsConfig()
    assert config.enabled is True
    assert config.retention_days == 0


def test_retention_days_rejects_negative() -> None:
    """负数保留天数非法（ge=0），0 与正整数合法。"""
    assert DiagnosticsConfig(retention_days=0).retention_days == 0
    assert DiagnosticsConfig(retention_days=7).retention_days == 7
    with pytest.raises(ValueError):
        DiagnosticsConfig(retention_days=-1)
