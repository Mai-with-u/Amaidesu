"""
配置格式迁移测试

测试配置从旧格式 [providers.*] 到新格式 [collectors]/[deciders]/[handlers] 的迁移。

迁移规则:
- [providers.input] → [collectors]
- [providers.decision] → [deciders]
- [providers.output] → [handlers]
- "maicore" decider 别名 → "maibot"

运行: uv run pytest tests/test_config_migration.py -v
"""

import warnings
import tempfile
import shutil
from pathlib import Path

import pytest

from src.modules.config.service import ConfigService


@pytest.fixture
def temp_base_dir():
    """创建临时测试目录"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config_service(temp_base_dir):
    """创建配置服务实例"""
    return ConfigService(base_dir=temp_base_dir)


class TestConfigMigration:
    """配置格式迁移测试"""

    def test_new_format_loaded_directly(self, config_service):
        """新格式配置应直接加载，不经过迁移"""
        config = {
            "collectors": {"enabled": ["console_input"]},
            "deciders": {"active": "maibot", "available": ["maibot"]},
            "handlers": {"enabled": ["subtitle"]},
        }

        migrated = config_service._migrate_config_format(config)

        assert "collectors" in migrated
        assert "deciders" in migrated
        assert "handlers" in migrated
        assert migrated["collectors"]["enabled"] == ["console_input"]
        assert migrated["deciders"]["active"] == "maibot"
        assert migrated["handlers"]["enabled"] == ["subtitle"]

    def test_old_format_migrated_to_new_format(self, config_service):
        """旧格式配置应正确迁移到新格式"""
        config = {
            "providers": {
                "input": {
                    "enabled_inputs": ["console_input", "bili_danmaku"],
                },
                "decision": {
                    "active_provider": "maicore",
                    "available_deciders": ["maicore", "llm"],
                },
                "output": {
                    "enabled_outputs": ["subtitle", "vts"],
                    "concurrent_rendering": True,
                },
            }
        }

        migrated = config_service._migrate_config_format(config)

        assert "collectors" in migrated
        assert "deciders" in migrated
        assert "handlers" in migrated

        assert migrated["collectors"]["enabled"] == ["console_input", "bili_danmaku"]
        assert migrated["deciders"]["active"] == "maibot"
        assert migrated["deciders"]["available"] == ["maibot", "llm"]
        assert migrated["handlers"]["enabled"] == ["subtitle", "vts"]

    def test_maicore_alias_migration(self, config_service):
        """maicore 别名应正确迁移到 maibot"""
        config = {
            "providers": {
                "decision": {
                    "active_provider": "maicore",
                    "available_deciders": ["maicore", "maicraft"],
                },
            }
        }

        migrated = config_service._migrate_config_format(config)

        assert migrated["deciders"]["active"] == "maibot"
        assert "maibot" in migrated["deciders"]["available"]
        assert "maicraft" in migrated["deciders"]["available"]
        assert "maicore" not in migrated["deciders"]["available"]

    def test_decider_config_migration(self, config_service):
        """Decider 特定配置应正确迁移"""
        config = {
            "providers": {
                "decision": {
                    "active_provider": "maicore",
                    "maicore": {
                        "host": "127.0.0.1",
                        "port": 8000,
                    },
                    "llm": {
                        "model": "gpt-4",
                    },
                },
            }
        }

        migrated = config_service._migrate_config_format(config)

        assert "maibot" in migrated["deciders"]
        assert migrated["deciders"]["maibot"]["host"] == "127.0.0.1"
        assert migrated["deciders"]["maibot"]["port"] == 8000
        assert "llm" in migrated["deciders"]
        assert migrated["deciders"]["llm"]["model"] == "gpt-4"

    def test_old_and_new_format_prefers_new(self, config_service):
        """同时存在新旧格式时，优先使用新格式"""
        config = {
            "providers": {
                "input": {
                    "enabled_inputs": ["old_input"],
                },
            },
            "collectors": {
                "enabled": ["new_input"],
            },
        }

        migrated = config_service._migrate_config_format(config)

        assert migrated["collectors"]["enabled"] == ["new_input"]
        assert "providers" not in migrated

    def test_deprecation_warning_for_old_format(self, config_service):
        """旧格式迁移时应发出 DeprecationWarning"""
        config = {
            "providers": {
                "input": {
                    "enabled_inputs": ["console_input"],
                },
            }
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config_service._migrate_config_format(config)

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "配置格式已变更" in str(w[0].message)

    def test_no_warning_for_new_format(self, config_service):
        """新格式配置不应发出 DeprecationWarning"""
        config = {
            "collectors": {"enabled": ["console_input"]},
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config_service._migrate_config_format(config)

            assert len(w) == 0

    def test_empty_config_returns_as_is(self, config_service):
        """空配置应直接返回"""
        config = {}

        migrated = config_service._migrate_config_format(config)

        assert migrated == {}

    def test_collector_config_preserved(self, config_service):
        """Collector 特定配置应保留"""
        config = {
            "providers": {
                "input": {
                    "enabled_inputs": ["console_input"],
                    "some_custom_field": "value",
                },
            }
        }

        migrated = config_service._migrate_config_format(config)

        assert migrated["collectors"]["some_custom_field"] == "value"

    def test_handler_metadata_preserved(self, config_service):
        """Handler 元数据配置应保留"""
        config = {
            "providers": {
                "output": {
                    "enabled_outputs": ["subtitle"],
                    "concurrent_rendering": False,
                    "error_handling": "stop",
                    "render_timeout": 30.0,
                },
            }
        }

        migrated = config_service._migrate_config_format(config)

        assert migrated["handlers"]["concurrent_rendering"] is False
        assert migrated["handlers"]["error_handling"] == "stop"
        assert migrated["handlers"]["render_timeout"] == 30.0


class TestConfigServiceWithMigration:
    """ConfigService 集成测试（带迁移）"""

    def test_initialize_with_old_format_emits_warning(self, temp_base_dir):
        """使用旧格式配置初始化时应发出警告"""
        config_path = Path(temp_base_dir) / "config.toml"
        config_path.write_text(
            """
[general]
platform_id = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input"]

[providers.output]
enabled = true
enabled_outputs = ["subtitle"]

[providers.decision]
enabled = true
active_provider = "maicore"
"""
        )

        service = ConfigService(base_dir=temp_base_dir)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            service.initialize()

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1

    def test_initialize_with_new_format_no_warning(self, temp_base_dir):
        """使用新格式配置初始化时不应发出警告"""
        config_path = Path(temp_base_dir) / "config.toml"
        config_path.write_text(
            """
[general]
platform_id = "test"

[collectors]
enabled = ["console_input"]

[handlers]
enabled = ["subtitle"]

[deciders]
active = "maibot"
available = ["maibot"]
"""
        )

        service = ConfigService(base_dir=temp_base_dir)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            service.initialize()

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

    def test_get_section_with_new_format(self, temp_base_dir):
        """新格式配置 get_section 应正常工作"""
        config_path = Path(temp_base_dir) / "config.toml"
        config_path.write_text(
            """
[general]
platform_id = "test"

[collectors]
enabled = ["console_input"]
"""
        )

        service = ConfigService(base_dir=temp_base_dir)
        service.initialize()

        collectors = service.get_section("collectors")
        assert collectors["enabled"] == ["console_input"]