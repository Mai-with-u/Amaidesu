"""
回归测试：验证历史重构遗留的崩溃路径已修复且 API 契约保持。

- BUG2: schemas.__all__ 包含的名称必须实际定义
- 核心: ConfigService 配置节查询签名（phase 参数）
"""

import inspect


def test_all_exports_are_defined():
    """BUG2: __all__ 中的所有名称必须实际定义"""
    from src.modules.config import schemas

    assert "list_all_providers" not in schemas.__all__
    for name in schemas.__all__:
        assert hasattr(schemas, name), f"'{name}' in __all__ but not defined"


def test_get_config_with_defaults_uses_phase_parameter():
    """核心: get_config_with_defaults 签名应使用 phase 而非 layer"""
    from src.modules.config.service import ConfigService

    sig = inspect.signature(ConfigService.get_config_with_defaults)
    assert "phase" in sig.parameters
    assert "layer" not in sig.parameters


def test_load_global_overrides_uses_phase_parameter():
    """核心: load_global_overrides 签名应使用 phase 而非 config_section"""
    from src.modules.config.service import ConfigService

    sig = inspect.signature(ConfigService.load_global_overrides)
    params = list(sig.parameters.keys())
    assert "phase" in params
