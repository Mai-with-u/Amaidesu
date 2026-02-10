"""
测试Provider Schema注册表和enabled字段检测

This module tests:
1. Schema registry completeness (all providers registered to ProviderRegistry)
2. No 'enabled' field in any schema (architecture requirement)
3. Schema validation functionality

注意：所有Provider已100%迁移到自管理Schema架构（commit 8af0e6e）
集中式PROVIDER_SCHEMA_REGISTRY保留为空仅用于向后兼容
"""

import pytest

import src.domains.decision.providers  # noqa: F401

# 触发 Provider 注册（必须在导入之前执行）
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401
from src.modules.config.schemas import (
    PROVIDER_SCHEMA_REGISTRY,
    get_provider_schema,
    list_all_providers,
    validate_provider_config,
    verify_no_enabled_field_in_schemas,
)
from src.modules.registry import ProviderRegistry


class TestSchemaRegistry:
    """测试Schema注册表"""

    def test_schema_registry_contains_all_providers(self):
        """测试注册表包含全部Provider（100%迁移到自管理Schema架构）"""
        # 需要先导入provider模块，ProviderRegistry才会注册它们

        providers = list_all_providers()

        # 验证集中式注册表为空（所有Provider已迁移）
        assert len(PROVIDER_SCHEMA_REGISTRY) == 0, "Centralized registry should be empty after 100% migration"

        # 验证list_all_providers从ProviderRegistry获取数据
        assert providers["total"] > 0, "Should have providers in ProviderRegistry"

        # 验证分类
        assert isinstance(providers["input"], list)
        assert isinstance(providers["decision"], list)
        assert isinstance(providers["output"], list)

        # 验证关键Provider在ProviderRegistry中（不在集中式注册表）
        assert "console_input" not in PROVIDER_SCHEMA_REGISTRY
        assert "mock_danmaku" not in PROVIDER_SCHEMA_REGISTRY
        assert "subtitle" not in PROVIDER_SCHEMA_REGISTRY
        assert "vts" not in PROVIDER_SCHEMA_REGISTRY
        assert "edge_tts" not in PROVIDER_SCHEMA_REGISTRY
        assert "bili_danmaku" not in PROVIDER_SCHEMA_REGISTRY
        assert "maicore" not in PROVIDER_SCHEMA_REGISTRY

        # 但它们应该在ProviderRegistry中
        assert ProviderRegistry.get_config_schema("console_input") is not None
        assert ProviderRegistry.get_config_schema("mock_danmaku") is not None
        assert ProviderRegistry.get_config_schema("bili_danmaku") is not None
        assert ProviderRegistry.get_config_schema("maicore") is not None
        assert ProviderRegistry.get_config_schema("subtitle") is not None
        assert ProviderRegistry.get_config_schema("vts") is not None
        assert ProviderRegistry.get_config_schema("edge_tts") is not None

    def test_input_providers_registry(self):
        """测试输入Provider注册完整（100%迁移到自管理Schema）"""

        input_providers = [
            "console_input",
            "bili_danmaku",
            "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            "mock_danmaku",
            "read_pingmu",
            "mainosaba",
        ]

        for provider in input_providers:
            # 验证不在集中式注册表
            assert provider not in PROVIDER_SCHEMA_REGISTRY, f"{provider} should not be in centralized registry"
            # 验证在ProviderRegistry中
            schema = ProviderRegistry.get_config_schema(provider)
            assert schema is not None, f"Input provider '{provider}' not in ProviderRegistry"

    def test_decision_providers_registry(self):
        """测试决策Provider注册完整（100%迁移到自管理Schema）"""

        decision_providers = [
            "maicore",
            "llm",
            "maicraft",
        ]

        for provider in decision_providers:
            # 验证不在集中式注册表
            assert provider not in PROVIDER_SCHEMA_REGISTRY, f"{provider} should not be in centralized registry"
            # 验证在ProviderRegistry中
            schema = ProviderRegistry.get_config_schema(provider)
            assert schema is not None, f"Decision provider '{provider}' not in ProviderRegistry"

    def test_output_providers_registry(self):
        """测试输出Provider注册完整（100%迁移到自管理Schema）"""

        output_providers = [
            "subtitle",
            "vts",
            "edge_tts",
            "sticker",
            "warudo",
            "obs_control",
            "gptsovits",
            "omni_tts",
            "vrchat",
            "remote_stream",
        ]

        for provider in output_providers:
            # 验证不在集中式注册表
            assert provider not in PROVIDER_SCHEMA_REGISTRY, f"{provider} should not be in centralized registry"
            # 验证在ProviderRegistry中
            schema = ProviderRegistry.get_config_schema(provider)
            assert schema is not None, f"Output provider '{provider}' not in ProviderRegistry"


class TestNoEnabledField:
    """测试Schema中不包含enabled字段（架构要求）"""

    def test_no_enabled_field_in_any_schema(self):
        """测试所有Schema都不包含enabled字段

        架构要求：
        - Provider的enabled状态由Manager统一管理
        - Schema中不应包含enabled字段
        - 此测试确保架构约束不被违反
        """
        violations = verify_no_enabled_field_in_schemas()

        # 应该没有任何违规
        assert len(violations) == 0, (
            f"Found {len(violations)} schemas with 'enabled' field:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nArchitecture violation: Provider enabled state should be managed by Manager, not in Schema."
        )

    def test_specific_schemas_no_enabled(self):
        """测试特定Schema不包含enabled字段（100%迁移到自管理Schema）"""
        from src.domains.decision.providers.maicore.maicore_decision_provider import MaiCoreDecisionProvider
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.audio import EdgeTTSProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 检查几个关键schema（使用自管理Schema的Provider）
        schemas_to_check = [
            ConsoleInputProvider.ConfigSchema,
            MaiCoreDecisionProvider.ConfigSchema,
            SubtitleOutputProvider.ConfigSchema,
            EdgeTTSProvider.ConfigSchema,
        ]

        for schema_class in schemas_to_check:
            # Pydantic v2
            if hasattr(schema_class, "model_fields"):
                assert "enabled" not in schema_class.model_fields, (
                    f"{schema_class.__name__} should not have 'enabled' field"
                )
            # Pydantic v1
            elif hasattr(schema_class, "__fields__"):
                assert "enabled" not in schema_class.__fields__, (
                    f"{schema_class.__name__} should not have 'enabled' field"
                )


class TestSchemaHelperFunctions:
    """测试Schema辅助函数"""

    def test_get_provider_schema_valid(self):
        """测试获取存在的Provider Schema（100%迁移到ProviderRegistry）"""
        # 需要先导入provider模块

        # 测试从ProviderRegistry获取自管理Schema
        schema = get_provider_schema("console_input")
        assert schema is not None

        schema = get_provider_schema("bili_danmaku")
        assert schema is not None

        schema = get_provider_schema("maicore")
        assert schema is not None

        schema = get_provider_schema("subtitle")
        assert schema is not None

    def test_get_provider_schema_invalid(self):
        """测试获取不存在的Provider Schema抛出异常"""
        with pytest.raises(KeyError, match="未注册的Provider类型"):
            get_provider_schema("nonexistent_provider")

    def test_validate_provider_config_valid(self):
        """测试验证有效的Provider配置（100%迁移到自管理Schema）"""
        # 导入provider模块

        # 测试console_input配置验证
        config = {"type": "console_input", "user_id": "test_user"}
        validated = validate_provider_config("console_input", config)
        assert validated.type == "console_input"
        assert validated.user_id == "test_user"

        # 测试bili_danmaku配置验证
        config = {"type": "bili_danmaku", "room_id": 12345}
        validated = validate_provider_config("bili_danmaku", config)
        assert validated.type == "bili_danmaku"
        assert validated.room_id == 12345

        # 测试maicore配置验证
        config = {"type": "maicore", "host": "localhost", "port": 8000}
        validated = validate_provider_config("maicore", config)
        assert validated.type == "maicore"
        assert validated.host == "localhost"
        assert validated.port == 8000

    def test_validate_provider_config_invalid(self):
        """测试验证无效的Provider配置抛出异常"""
        # 导入provider模块

        # 无效的配置（缺少必需字段）
        with pytest.raises(Exception):  # ValidationError from pydantic
            validate_provider_config("bili_danmaku", {})  # type and room_id are required

    def test_list_all_providers_structure(self):
        """测试list_all_providers返回结构正确"""
        providers = list_all_providers()

        # 验证返回结构
        assert isinstance(providers, dict)
        assert "input" in providers
        assert "decision" in providers
        assert "output" in providers
        assert "total" in providers

        # 验证值类型
        assert isinstance(providers["input"], list)
        assert isinstance(providers["decision"], list)
        assert isinstance(providers["output"], list)
        assert isinstance(providers["total"], int)


class TestSchemaValidation:
    """测试Schema验证功能"""

    def test_console_input_schema_validation(self):
        """测试ConsoleInput Schema验证（自管理Schema）"""
        from src.domains.input.providers.console_input import ConsoleInputProvider

        # 有效配置
        config = ConsoleInputProvider.ConfigSchema(type="console_input")
        assert config.type == "console_input"

        # 带额外字段
        config = ConsoleInputProvider.ConfigSchema(type="console_input", user_id="test")
        assert config.user_id == "test"

    def test_maicore_schema_validation(self):
        """测试MaiCore Schema验证（自管理Schema）"""
        from src.domains.decision.providers.maicore.maicore_decision_provider import (
            MaiCoreDecisionProvider,
        )

        config = MaiCoreDecisionProvider.ConfigSchema(
            type="maicore",
            host="localhost",
            port=8000,
        )
        assert config.type == "maicore"
        assert config.host == "localhost"
        assert config.port == 8000

    def test_subtitle_schema_validation(self):
        """测试Subtitle Schema验证（自管理Schema）"""
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        config = SubtitleOutputProvider.ConfigSchema(
            window_width=800,
            window_height=100,
        )
        assert config.window_width == 800
        assert config.window_height == 100

    def test_tts_schema_validation(self):
        """测试TTS Schema验证（自管理Schema）"""
        from src.domains.output.providers.audio import EdgeTTSProvider

        config = EdgeTTSProvider.ConfigSchema(
            voice="zh-CN-XiaoxiaoNeural",
        )
        assert config.type == "edge_tts"
        assert config.voice == "zh-CN-XiaoxiaoNeural"


class TestRegistryConsistency:
    """测试注册表一致性"""

    def test_input_decision_providers_have_type_field(self):
        """测试输入和决策Provider的type字段与注册表键一致（100%迁移到ProviderRegistry）"""
        from pydantic import ValidationError

        # 检查所有input和decision providers（都应该有type字段）
        providers_with_type = [
            "console_input",
            "bili_danmaku",
            "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            "mock_danmaku",
            "read_pingmu",
            "mainosaba",
            "maicore",
            "llm",
            "maicraft",
        ]

        for provider_type in providers_with_type:
            schema_class = ProviderRegistry.get_config_schema(provider_type)
            assert schema_class is not None, f"{provider_type} not found in ProviderRegistry"

            try:
                # 尝试创建实例（有些schema有必需字段）
                instance = schema_class()
            except ValidationError:
                # 如果有必需字段，使用model_fields验证type字段存在
                assert "type" in schema_class.model_fields, f"{schema_class.__name__} missing 'type' field"

                # 验证type字段的默认值
                type_field = schema_class.model_fields["type"]
                assert type_field.default == provider_type, (
                    f"Type mismatch: registry key '{provider_type}' != schema default '{type_field.default}'"
                )
            else:
                # 无必需字段，直接验证
                assert hasattr(instance, "type"), f"{schema_class.__name__} missing 'type' field"
                assert instance.type == provider_type, (
                    f"Type mismatch: registry key '{provider_type}' != schema field '{instance.type}'"
                )

    def test_registry_is_complete(self):
        """测试注册表包含所有必要的Provider类型（100%迁移到ProviderRegistry）"""
        # 导入所有provider模块

        # 验证关键provider在ProviderRegistry中
        critical_providers = [
            # Input
            "console_input",
            "bili_danmaku",
            "mock_danmaku",
            # Decision
            "maicore",
            "llm",
            "maicraft",
            # Output
            "subtitle",
            "vts",
            "edge_tts",
            "gptsovits",
        ]

        for provider in critical_providers:
            schema = ProviderRegistry.get_config_schema(provider)
            assert schema is not None, f"Critical provider '{provider}' not in ProviderRegistry"

    def test_output_providers_in_map(self):
        """测试输出Provider在OUTPUT_PROVIDER_CONFIG_MAP中（100%迁移后此映射已废弃）"""
        from src.modules.config.schemas import OUTPUT_PROVIDER_CONFIG_MAP

        # 所有output provider已迁移到自管理Schema
        # OUTPUT_PROVIDER_CONFIG_MAP保留为空仅用于向后兼容
        assert len(OUTPUT_PROVIDER_CONFIG_MAP) == 0, "OUTPUT_PROVIDER_CONFIG_MAP should be empty after 100% migration"

        # 验证所有output providers在ProviderRegistry中

        output_providers = [
            "subtitle",
            "vts",
            "edge_tts",
            "sticker",
            "warudo",
            "obs_control",
            "gptsovits",
            "omni_tts",
            "vrchat",
            "remote_stream",
        ]

        for provider in output_providers:
            schema = ProviderRegistry.get_config_schema(provider)
            assert schema is not None, f"Output provider '{provider}' not in ProviderRegistry"
