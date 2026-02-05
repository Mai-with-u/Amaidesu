"""
测试弃用警告

验证旧的配置方法会触发 DeprecationWarning
"""

import warnings
import pytest
from pathlib import Path
import sys

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.services.config.service import ConfigService


class TestConfigServiceDeprecationWarnings:
    """测试ConfigService的弃用警告"""

    @pytest.fixture
    def config_service(self, tmp_path):
        """创建测试用的ConfigService实例"""
        # 创建配置目录结构
        config_dir = tmp_path / "config_test"
        config_dir.mkdir()

        # 创建最小配置
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[general]
platform_id = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input"]

[providers.input.inputs.console_input]
type = "console_input"
enabled = true

[providers.output]
enabled = true
enabled_outputs = ["subtitle"]

[providers.output.outputs.subtitle]
type = "subtitle"
enabled = true
""")

        # 创建模板文件
        template_file = config_dir / "config-template.toml"
        template_file.write_text(config_file.read_text())

        service = ConfigService(base_dir=str(config_dir))
        service.initialize()

        return service

    def test_get_input_provider_config_deprecation_warning(self, config_service):
        """测试 get_input_provider_config 触发弃用警告"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # 调用已弃用的方法
            result = config_service.get_input_provider_config("console_input")

            # 验证警告被触发
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "get_input_provider_config() is deprecated" in str(w[0].message)
            assert "get_provider_config_with_defaults" in str(w[0].message)
            assert "console_input" in str(w[0].message)

    def test_get_provider_config_deprecation_warning(self, config_service):
        """测试 get_provider_config 触发弃用警告"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # 调用已弃用的方法
            result = config_service.get_provider_config("subtitle")

            # 验证警告被触发
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "get_provider_config() is deprecated" in str(w[0].message)
            assert "get_provider_config_with_defaults" in str(w[0].message)
            assert "subtitle" in str(w[0].message)

    def test_no_warning_for_new_method(self, config_service):
        """测试新方法不会触发弃用警告"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # 调用新方法（不应触发警告）
            result = config_service.get_provider_config_with_defaults(
                provider_name="console_input",
                provider_layer="input",
                schema_class=None,
            )

            # 验证没有 DeprecationWarning
            deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
            assert len(deprecation_warnings) == 0


class TestManagerDeprecationWarnings:
    """测试Manager的弃用警告（回退到旧配置加载方式时）"""

    @pytest.mark.asyncio
    async def test_input_provider_manager_deprecation_warning(self, tmp_path):
        """测试 InputProviderManager 在未传递 config_service 时触发弃用警告"""
        from src.domains.input.manager import InputProviderManager
        from src.core.event_bus import EventBus

        # 创建测试配置
        config = {
            "enabled": True,
            "enabled_inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                    "enabled": True,
                }
            }
        }

        manager = InputProviderManager(event_bus=EventBus())

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # 不传递 config_service（触发回退）
            providers = await manager.load_from_config(config, config_service=None)

            # 验证警告被触发
            deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
            assert len(deprecation_warnings) > 0
            assert "InputProviderManager: Using deprecated configuration loading" in str(deprecation_warnings[0].message)
            assert "config_service parameter" in str(deprecation_warnings[0].message)

    @pytest.mark.asyncio
    async def test_output_provider_manager_deprecation_warning(self, tmp_path):
        """测试 OutputProviderManager 在未传递 config_service 时触发弃用警告"""
        from src.domains.output.manager import OutputProviderManager

        # 创建测试配置
        config = {
            "enabled": True,
            "enabled_outputs": ["subtitle"],
            "outputs": {
                "subtitle": {
                    "type": "subtitle",
                    "enabled": True,
                }
            }
        }

        manager = OutputProviderManager()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # 不传递 config_service（触发回退）
            await manager.load_from_config(config, core=None, config_service=None)

            # 验证警告被触发
            deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
            assert len(deprecation_warnings) > 0
            assert "OutputProviderManager: Using deprecated configuration loading" in str(deprecation_warnings[0].message)
            assert "config_service parameter" in str(deprecation_warnings[0].message)
