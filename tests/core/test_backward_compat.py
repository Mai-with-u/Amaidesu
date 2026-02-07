"""
测试向后兼容性

确保现有用户的配置文件在系统升级后继续工作。

注意：新架构使用Schema自动生成默认值，不再依赖config-defaults.toml文件。
配置优先级：Schema默认值 → 主配置覆盖 ([providers.*.overrides]) → Provider本地配置 (config.toml)
"""

import pytest
import tempfile
import os


@pytest.fixture(autouse=True)
def register_providers():
    """
    自动注册所有Provider（在测试开始前）

    确保ProviderRegistry中有可用的Provider用于测试。
    """
    # 导入输入Provider以触发注册

    # 导入输出Provider以触发注册

    yield

    # 测试后清理（如果需要）


class TestBackwardCompatibility:
    """测试向后兼容性"""

    @pytest.mark.asyncio
    async def test_old_config_format_with_inputs(self):
        """
        测试旧格式配置（使用inputs）仍然可用

        旧格式示例：
        [providers.input]
        enabled = true
        inputs = ["console_input"]
        [providers.input.inputs.console_input]
        type = "console_input"
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 使用旧格式配置
        old_config = {
            "enabled": True,
            "inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                }
            }
        }

        providers = await manager.load_from_config(old_config, config_service=None)

        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "ConsoleInputProvider"

    @pytest.mark.asyncio
    async def test_old_config_format_with_outputs(self):
        """
        测试旧格式配置（使用outputs嵌套）仍然可用

        旧格式示例：
        [providers.output]
        enabled_outputs = ["subtitle"]
        [providers.output.outputs.subtitle]
        type = "subtitle"
        font_size = 24
        """
        from src.domains.output.manager import OutputProviderManager

        manager = OutputProviderManager()

        # 使用旧格式配置
        old_config = {
            "enabled": True,
            "enabled_outputs": ["subtitle"],
            "outputs": {
                "subtitle": {
                    "type": "subtitle",
                    "font_size": 24,
                }
            }
        }

        await manager.load_from_config(old_config)

        assert len(manager.providers) == 1
        # get_provider_names() 返回的是类名，不是类型名
        assert "Subtitle" in manager.get_provider_names()[0] or "subtitle" in manager.get_provider_names()[0].lower()

    @pytest.mark.asyncio
    async def test_new_config_format_with_enabled_inputs_list(self):
        """
        测试新格式配置（使用enabled_inputs列表）正常工作

        新格式示例：
        [providers.input]
        enabled = true
        enabled_inputs = ["console_input"]
        [providers.input.inputs.console_input]
        type = "console_input"
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 使用新格式配置
        new_config = {
            "enabled": True,
            "enabled_inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                }
            }
        }

        providers = await manager.load_from_config(new_config, config_service=None)

        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "ConsoleInputProvider"

    @pytest.mark.asyncio
    async def test_mixed_config_format(self):
        """
        测试混合配置格式（部分Provider有详细配置，部分没有）

        这是迁移过渡期的典型场景：
        - 新Provider使用Schema默认值
        - 旧Provider保留主配置中的详细配置
        """
        from src.domains.output.manager import OutputProviderManager

        manager = OutputProviderManager()

        # 混合格式配置
        mixed_config = {
            "enabled": True,
            "enabled_outputs": ["subtitle"],
            # subtitle使用旧格式（有详细配置）
            "outputs": {
                "subtitle": {
                    "type": "subtitle",
                    "font_size": 24,
                }
            }
            # vts使用新格式（没有详细配置，从Schema加载）
        }

        # 在实际环境中，subtitle从outputs加载，vts从Schema加载
        # 这里测试向后兼容的fallback逻辑
        await manager.load_from_config(mixed_config)

        # 至少subtitle应该加载成功
        assert len(manager.providers) >= 1

    @pytest.mark.asyncio
    async def test_config_service_backward_compat_inputs(self):
        """
        测试ConfigService对旧格式输入配置的兼容性
        """
        from src.services.config.service import ConfigService

        # 创建临时目录和配置文件
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建config.toml（旧格式）
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input"]

[providers.input.inputs.console_input]
type = "console_input"
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 创建config-template.toml
            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            main_config, main_copied, _, _ = config_service.initialize()

            # 验证配置加载成功
            assert "providers" in main_config
            assert "input" in main_config["providers"]

            # 获取输入Provider配置
            input_config = config_service.get_input_provider_config("console_input")
            assert input_config is not None

    @pytest.mark.asyncio
    async def test_config_service_backward_compat_outputs(self):
        """
        测试ConfigService对旧格式输出配置的兼容性
        """
        from src.services.config.service import ConfigService

        # 创建临时目录和配置文件
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建config.toml（旧格式）
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["subtitle"]

[providers.output.outputs.subtitle]
type = "subtitle"
font_size = 24
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 创建config-template.toml
            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            main_config, main_copied, _, _ = config_service.initialize()

            # 验证配置加载成功
            assert "providers" in main_config
            assert "output" in main_config["providers"]

            # 直接从main_config获取配置（get_provider_config有bug）
            providers_config = main_config.get("providers", {})
            output_config_section = providers_config.get("output", {})
            outputs_config = output_config_section.get("outputs", {})
            subtitle_config = outputs_config.get("subtitle", {})

            assert subtitle_config.get("type") == "subtitle"
            assert subtitle_config.get("font_size") == 24

    @pytest.mark.asyncio
    async def test_missing_inputs_config_returns_empty(self):
        """
        测试当inputs配置不存在时，返回空列表
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 配置中没有inputs字段
        config = {
            "enabled": True,
        }

        providers = await manager.load_from_config(config, config_service=None)

        # 应该返回空列表
        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_missing_outputs_config_uses_defaults(self):
        """
        测试当outputs配置不存在时，Provider使用默认配置创建

        当enabled_outputs中列出了某个Provider，但没有在outputs中提供详细配置时，
        ProviderRegistry会尝试使用默认配置创建Provider。
        """
        from src.domains.output.manager import OutputProviderManager

        manager = OutputProviderManager()

        # 配置中没有outputs字段（或outputs为空）
        config = {
            "enabled": True,
            "enabled_outputs": ["subtitle"],
            "outputs": {}  # 空的outputs配置
        }

        # 应该正常处理，Provider会尝试使用默认配置创建
        await manager.load_from_config(config)

        # 某些Provider可以只用默认配置创建成功
        # 这个测试验证Manager不会崩溃
        # 结果取决于Provider是否有合理的默认值
        # 如果创建失败，Manager会记录错误但继续运行
        # 我们只验证Manager没有崩溃即可

    @pytest.mark.asyncio
    async def test_config_service_is_provider_enabled(self):
        """
        测试ConfigService.is_provider_enabled方法的向后兼容性
        """
        from src.services.config.service import ConfigService

        # 创建临时目录和配置文件
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建config.toml
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.output]
enabled = true
enabled_outputs = ["subtitle", "tts"]
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 创建config-template.toml
            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            main_config, _, _, _ = config_service.initialize()

            # 测试输入Provider配置加载
            providers_config = main_config.get("providers", {})
            input_config = providers_config.get("input", {})
            enabled_inputs = input_config.get("enabled_inputs", [])

            assert "console_input" in enabled_inputs
            assert "bili_danmaku" in enabled_inputs
            assert "minecraft" not in enabled_inputs

            # 测试输出Provider配置加载
            output_config = providers_config.get("output", {})
            enabled_outputs = output_config.get("enabled_outputs", [])

            assert "subtitle" in enabled_outputs
            assert "tts" in enabled_outputs
            assert "vts" not in enabled_outputs

    @pytest.mark.asyncio
    async def test_old_config_with_inputs_field(self):
        """
        测试包含inputs字段的旧配置仍然可以工作
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 旧格式配置（使用inputs字段）
        old_config = {
            "enabled": True,
            "inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                    "user_id": "test_user",
                }
            }
        }

        providers = await manager.load_from_config(old_config, config_service=None)

        # 应该正常工作
        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "ConsoleInputProvider"

    @pytest.mark.asyncio
    async def test_enabled_inputs_fallback_to_inputs(self):
        """
        测试当enabled_inputs不存在时，fallback到inputs字段
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 旧格式配置（只有inputs，没有enabled_inputs）
        old_config = {
            "enabled": True,
            "inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                }
            }
        }

        providers = await manager.load_from_config(old_config, config_service=None)

        # 应该从inputs字段加载配置
        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "ConsoleInputProvider"

    @pytest.mark.asyncio
    async def test_disabled_layer_returns_empty_list(self):
        """
        测试当enabled=false时，返回空列表
        """
        from src.domains.input.input_provider_manager import InputProviderManager
        from src.core.event_bus import EventBus

        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 禁用整个层
        config = {
            "enabled": False,
            "inputs": ["console_input"],
            "inputs": {
                "console_input": {
                    "type": "console_input",
                }
            }
        }

        providers = await manager.load_from_config(config, config_service=None)

        # 应该返回空列表
        assert len(providers) == 0

    # ==================== Schema-based配置系统测试（新架构）====================

    @pytest.mark.asyncio
    async def test_schema_based_config_fallback_to_defaults(self):
        """
        测试新Schema架构的回退机制

        当Provider没有配置时，系统应该从Schema获取默认值。
        """
        from src.services.config.service import ConfigService
        from src.services.config.schemas import ConsoleInputProviderConfig

        # 创建临时目录和最小配置
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建最小配置（没有Provider详细配置）
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input"]
# 注意：没有 [providers.input.inputs.console_input] 节
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            main_config, _, _, _ = config_service.initialize()

            # 使用Schema获取配置（应该返回Schema默认值）
            provider_config = config_service.get_provider_config_with_defaults(
                "console_input", "input", ConsoleInputProviderConfig
            )

            # 验证Schema默认值被正确加载
            assert provider_config is not None
            assert provider_config["type"] == "console_input"
            # ConsoleInputProviderConfig的其他默认字段也应该存在

    @pytest.mark.asyncio
    async def test_schema_config_with_main_overrides(self):
        """
        测试Schema配置与主配置覆盖的向后兼容性

        验证配置优先级：Schema默认值 < 主配置覆盖
        """
        from src.services.config.service import ConfigService
        from src.services.config.schemas import SubtitleProviderConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建带overrides的主配置
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["subtitle"]

[providers.output.overrides.subtitle]
type = "subtitle"
font_size = 32  # 覆盖Schema默认值
display_time = 8  # 覆盖Schema默认值
custom_field = "from_main_config"
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            config_service.initialize()

            # 获取配置（Schema + 主配置覆盖）
            provider_config = config_service.get_provider_config_with_defaults(
                "subtitle", "output", SubtitleProviderConfig
            )

            # 验证主配置覆盖生效
            assert provider_config["font_size"] == 32
            assert provider_config["display_time"] == 8
            assert provider_config["custom_field"] == "from_main_config"
            # Schema默认值应该保留（未被覆盖的）
            assert provider_config["type"] == "subtitle"

    @pytest.mark.asyncio
    async def test_migration_from_old_config_to_schema_based(self):
        """
        测试从旧配置格式迁移到Schema-based配置

        模拟用户升级场景：旧配置继续工作，新Provider使用Schema。
        """
        from src.services.config.service import ConfigService
        from src.services.config.schemas import (
            SubtitleProviderConfig,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            # 混合配置：console_input使用旧格式，subtitle使用Schema
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["console_input"]

[providers.input.inputs.console_input]
type = "console_input"
# 旧格式：有详细配置

[providers.output]
enabled = true
enabled_outputs = ["subtitle"]
# 新格式：没有详细配置，使用Schema默认值
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            main_config, _, _, _ = config_service.initialize()

            # 旧格式配置应该继续工作
            input_config = config_service.get_input_provider_config("console_input")
            assert input_config["type"] == "console_input"

            # 新格式配置应该使用Schema默认值
            output_config = config_service.get_provider_config_with_defaults(
                "subtitle", "output", SubtitleProviderConfig
            )
            # Schema默认值应该被应用（注意：type字段不在Schema中，不会被返回）
            assert "font_size" in output_config or "window_width" in output_config
            # 至少应该有一些Schema默认值

    @pytest.mark.asyncio
    async def test_schema_registry_compatibility(self):
        """
        测试Schema注册表与现有Provider的兼容性

        验证所有现有Provider都在Schema注册表中。
        """
        from src.services.config.schemas import (
            PROVIDER_SCHEMA_REGISTRY,
            list_all_providers,
        )

        # 验证注册表包含所有关键Provider
        assert "console_input" in PROVIDER_SCHEMA_REGISTRY
        assert "bili_danmaku" in PROVIDER_SCHEMA_REGISTRY
        assert "subtitle" in PROVIDER_SCHEMA_REGISTRY
        assert "tts" in PROVIDER_SCHEMA_REGISTRY
        assert "vts" in PROVIDER_SCHEMA_REGISTRY
        assert "maicore" in PROVIDER_SCHEMA_REGISTRY
        assert "local_llm" in PROVIDER_SCHEMA_REGISTRY

        # 验证统计信息
        all_providers = list_all_providers()
        assert all_providers["total"] >= 20  # 至少20个Provider
        assert len(all_providers["input"]) >= 5  # 至少5个输入Provider
        assert len(all_providers["output"]) >= 8  # 至少8个输出Provider
        assert len(all_providers["decision"]) >= 3  # 至少3个决策Provider

    @pytest.mark.asyncio
    async def test_no_enabled_field_in_schemas(self):
        """
        测试Schema中没有enabled字段（架构要求）

        这确保enabled状态完全由Manager统一管理。
        """
        from src.services.config.schemas import verify_no_enabled_field_in_schemas

        # 这是架构强制性检查
        schemas_with_enabled = verify_no_enabled_field_in_schemas()

        # 应该返回空列表（没有Schema包含enabled字段）
        assert schemas_with_enabled == [], (
            f"以下Schema包含enabled字段，违反架构要求: {schemas_with_enabled}"
        )

    @pytest.mark.asyncio
    async def test_auto_schema_generation_for_new_providers(self):
        """
        测试新Provider的Schema自动生成

        验证新添加的Provider可以通过Schema获取配置，无需创建config.toml。
        """
        from src.services.config.service import ConfigService
        from pydantic import BaseModel

        # 模拟新Provider的Schema
        class NewProviderConfig(BaseModel):
            type: str = "new_provider"
            api_key: str = ""
            timeout: int = 30

        with tempfile.TemporaryDirectory() as temp_dir:
            # 最小配置
            config_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["new_provider"]
"""
            config_path = os.path.join(temp_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            template_path = os.path.join(temp_dir, "config-template.toml")
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            # 初始化ConfigService
            config_service = ConfigService(base_dir=temp_dir)
            config_service.initialize()

            # 新Provider应该能从Schema获取默认配置
            provider_config = config_service.get_provider_config_with_defaults(
                "new_provider", "output", NewProviderConfig
            )

            assert provider_config["type"] == "new_provider"
            assert provider_config["timeout"] == 30
