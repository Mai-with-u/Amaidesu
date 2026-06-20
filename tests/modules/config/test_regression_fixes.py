"""
回归测试：验证重构遗留的崩溃路径已修复。

覆盖以下之前因目录重命名（domains→stages, providers→collectors/deciders/handlers）
导致的运行时崩溃：
- BUG1: schemas/__init__.py import 断链
- BUG2: __all__ 包含不存在的函数
- BUG3: generator.py 路径错误
- BUG4: version_manager.py 路径错误
- 核心: ConfigService 配置节名从 [providers.*] 改为 [collectors/deciders/handlers]
- 核心: get_config_with_defaults 参数从 layer 改为 phase
"""

import os
import inspect
import tempfile
import shutil

import pytest


def test_get_config_schema_raises_keyerror_not_importerror():
    """BUG1: schemas/__init__.py 不应有 import 断链，未注册类型应抛 KeyError"""
    from src.modules.config.schemas import get_config_schema

    with pytest.raises(KeyError):
        get_config_schema("nonexistent_type", "input")


def test_all_exports_are_defined():
    """BUG2: __all__ 中的所有名称必须实际定义"""
    from src.modules.config import schemas

    assert "list_all_providers" not in schemas.__all__
    for name in schemas.__all__:
        assert hasattr(schemas, name), f"'{name}' in __all__ but not defined"


def test_ensure_component_config_path_uses_stages():
    """BUG3: generator.py 路径应指向 src/stages/ 而非 src/domains/"""
    from src.modules.config.schemas.generator import ensure_component_config

    try:
        ensure_component_config(name="nonexistent", phase="input")
    except FileNotFoundError as e:
        error_msg = str(e)
        assert "stages" in error_msg, f"路径应包含 'stages': {error_msg}"
        assert "collectors" in error_msg, f"路径应包含 'collectors': {error_msg}"
        assert "domains" not in error_msg, f"路径不应包含 'domains': {error_msg}"
    except ValueError:
        pass


def test_ensure_component_config_output_phase():
    """BUG3 续: output 阶段路径应包含 handlers"""
    from src.modules.config.schemas.generator import ensure_component_config

    try:
        ensure_component_config(name="nonexistent", phase="output")
    except FileNotFoundError as e:
        assert "handlers" in str(e)
    except ValueError:
        pass


def test_version_manager_scan_uses_stages_path(tmp_path):
    """BUG4: version_manager 应扫描 src/stages/ 而非 src/domains/"""
    component_dir = tmp_path / "src" / "stages" / "input" / "collectors" / "test_component"
    component_dir.mkdir(parents=True)
    (component_dir / "config.toml").write_text('[meta]\nversion = "1.0"\n')

    from src.modules.config.version_manager import ConfigVersionManager

    vm = ConfigVersionManager(base_dir=str(tmp_path))
    vm.scan_configs()

    assert len(vm._configs) == 1
    assert "input.test_component" in vm._configs


def test_config_service_reads_collectors_format(tmp_path):
    """核心: ConfigService 应读取 [collectors] 而非 [providers.input]"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[collectors]\nenabled = ["test_collector"]\n\n'
        "[collectors.test_collector]\npriority = 100\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    assert cs.get_section("collectors").get("enabled") == ["test_collector"]
    config = cs.get_config_with_defaults("test_collector", "input")
    assert config.get("priority") == 100


def test_config_service_reads_handlers_format(tmp_path):
    """核心: ConfigService 应读取 [handlers] 而非 [providers.output]"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[handlers]\nenabled = ["test_handler"]\n\n'
        "[handlers.test_handler]\nvoice_id = \"test\"\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    assert cs.get_section("handlers").get("enabled") == ["test_handler"]
    config = cs.get_config_with_defaults("test_handler", "output")
    assert config.get("voice_id") == "test"


def test_config_service_reads_deciders_format(tmp_path):
    """核心: ConfigService 应读取 [deciders] 而非 [providers.decision]"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[deciders]\nactive = "test_decider"\n\n'
        "[deciders.test_decider]\nmodel = \"gpt\"\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    assert cs.get_section("deciders").get("active") == "test_decider"
    config = cs.get_config_with_defaults("test_decider", "decision")
    assert config.get("model") == "gpt"


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
