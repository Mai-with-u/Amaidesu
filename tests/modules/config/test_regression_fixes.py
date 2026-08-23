"""
回归测试：验证重构遗留的崩溃路径已修复。

覆盖以下之前因目录重命名（domains→stages, providers→collectors/deciders/handlers）
导致的运行时崩溃：
- BUG1: schemas/__init__.py import 断链
- BUG2: __all__ 包含不存在的函数
- BUG3: generator.py 路径错误
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


def test_config_service_reads_collectors_format_v2(tmp_path):
    """v2.0.0：[collectors] 迁移后归并到 [tools.perception.config]。

    旧 [collectors] 段不再作为顶层段保留——由 load_config_dir 的
    CrossFileMigration 把数据并入 tools.toml 的 perception.config。
    阶段查询 (phase='input') 走 _PHASE_SECTION 兼容路径，从工具包配置中查找。
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[collectors]\nenabled = ["test_collector"]\n\n'
        "[collectors.test_collector]\npriority = 100\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    agents_section = cs.get_section("tools", {})
    perception_cfg = agents_section.get("perception") or {}
    config = perception_cfg.get("config", {}).get("test_collector") or {}
    assert config.get("priority") == 100, f"expected priority=100, got {config}"


def test_config_service_reads_handlers_format_v2(tmp_path):
    """v2.0.0：[handlers] 迁移后归并到 [tools.output.config]。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[handlers]\nenabled = ["test_handler"]\n\n'
        "[handlers.test_handler]\nvoice_id = \"test\"\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    tools_section = cs.get_section("tools", {})
    output_cfg = tools_section.get("output") or {}
    config = output_cfg.get("config", {}).get("test_handler") or {}
    assert config.get("voice_id") == "test", f"expected voice_id=test, got {config}"


def test_config_service_reads_deciders_format_v2(tmp_path):
    """v2.0.0：[deciders] 迁移后归并到 [agents] 段（保留 enabled 列表）。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[deciders]\nenabled = ["test_decider"]\n\n'
        "[deciders.test_decider]\nmodel = \"gpt\"\n"
    )

    from src.modules.config.service import ConfigService

    cs = ConfigService(base_dir=str(tmp_path))
    cs.initialize()

    agents_section = cs.get_section("agents", {})
    assert agents_section.get("enabled") == ["test_decider"]


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
