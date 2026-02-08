"""
测试Provider Schema注册表和enabled字段检测

This module tests:
1. Schema registry completeness (all 21 providers registered)
2. No 'enabled' field in any schema (architecture requirement)
3. Schema validation functionality
"""

import pytest
from src.services.config.schemas import (
    PROVIDER_SCHEMA_REGISTRY,
    list_all_providers,
    verify_no_enabled_field_in_schemas,
    get_provider_schema,
    validate_provider_config,
)


class TestSchemaRegistry:
    """测试Schema注册表"""

    def test_schema_registry_contains_all_providers(self):
        """测试注册表包含全部21个Provider（其中5个已迁移到自管理Schema）"""
        providers = list_all_providers()

        # 验证总数 (7 input + 4 decision + 10 output = 21)
        # 注意：console_input, mock_danmaku, subtitle, vts, tts 已迁移到自管理Schema，不在集中式注册表中
        # 但list_all_providers()统计的是集中式注册表，所以总数应该减少
        expected_total = 21 - 5  # 16个仍在集中式注册表中
        assert providers["total"] == expected_total, f"Expected {expected_total} providers in centralized registry, got {providers['total']}"

        # 验证分类数量
        assert len(providers["input"]) == 7 - 2, f"Expected 5 input providers, got {len(providers['input'])}"  # console_input, mock_danmaku已迁移
        assert len(providers["decision"]) == 4, f"Expected 4 decision providers, got {len(providers['decision'])}"
        assert len(providers["output"]) == 10 - 3, f"Expected 7 output providers, got {len(providers['output'])}"  # subtitle, vts, tts已迁移

        # 验证已迁移的provider不在集中式注册表中
        assert "console_input" not in PROVIDER_SCHEMA_REGISTRY, "console_input should be in ProviderRegistry, not centralized registry"
        assert "mock_danmaku" not in PROVIDER_SCHEMA_REGISTRY, "mock_danmaku should be in ProviderRegistry, not centralized registry"
        assert "subtitle" not in PROVIDER_SCHEMA_REGISTRY, "subtitle should be in ProviderRegistry, not centralized registry"
        assert "vts" not in PROVIDER_SCHEMA_REGISTRY, "vts should be in ProviderRegistry, not centralized registry"
        assert "tts" not in PROVIDER_SCHEMA_REGISTRY, "tts should be in ProviderRegistry, not centralized registry"

        # 验证其他provider仍在集中式注册表中
        assert "bili_danmaku" in PROVIDER_SCHEMA_REGISTRY
        assert "maicore" in PROVIDER_SCHEMA_REGISTRY
        # RemoteStream现在是一个output provider
        assert "remote_stream" in PROVIDER_SCHEMA_REGISTRY

    def test_input_providers_registry(self):
        """测试输入Provider注册完整（console_input, mock_danmaku已迁移到自管理Schema）"""
        input_providers = [
            # "console_input",  # 已迁移到自管理Schema
            "bili_danmaku",
            "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            # "mock_danmaku",  # 已迁移到自管理Schema
            "read_pingmu",
            "mainosaba",
        ]

        for provider in input_providers:
            assert provider in PROVIDER_SCHEMA_REGISTRY, f"Input provider '{provider}' not in registry"

    def test_decision_providers_registry(self):
        """测试决策Provider注册完整"""
        decision_providers = [
            "maicore",
            "local_llm",
            "rule_engine",
            "mock",
        ]

        for provider in decision_providers:
            assert provider in PROVIDER_SCHEMA_REGISTRY, f"Decision provider '{provider}' not in registry"

    def test_output_providers_registry(self):
        """测试输出Provider注册完整（subtitle, vts, tts已迁移到自管理Schema）"""
        output_providers = [
            # "subtitle",  # 已迁移到自管理Schema
            # "vts",  # 已迁移到自管理Schema
            # "tts",  # 已迁移到自管理Schema
            "sticker",
            "warudo",
            "obs_control",
            "gptsovits",
            "omni_tts",
            "avatar",
            "remote_stream",
        ]

        for provider in output_providers:
            assert provider in PROVIDER_SCHEMA_REGISTRY, f"Output provider '{provider}' not in registry"


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
        """测试特定Schema不包含enabled字段"""
        from src.services.config.schemas import (
            MaiCoreDecisionProviderConfig,
        )
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider
        from src.domains.output.providers.tts import TTSProvider

        # 检查几个关键schema（使用自管理Schema的Provider）
        schemas_to_check = [
            ConsoleInputProvider.ConfigSchema,
            MaiCoreDecisionProviderConfig,
            SubtitleOutputProvider.ConfigSchema,
            TTSProvider.ConfigSchema,
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
        """测试获取存在的Provider Schema（从ProviderRegistry获取自管理Schema）"""
        # 测试从集中式注册表获取
        schema = get_provider_schema("bili_danmaku")
        assert schema is not None
        assert schema.__name__ == "BiliDanmakuProviderConfig"

        # 测试从ProviderRegistry获取自管理Schema
        # 注意：需要先导入provider模块，ProviderRegistry才会注册schema
        from src.core.provider_registry import ProviderRegistry
        from src.domains.input.providers import console_input
        schema = ProviderRegistry.get_config_schema("console_input")
        assert schema is not None
        assert schema.__name__ == "ConfigSchema"

    def test_get_provider_schema_invalid(self):
        """测试获取不存在的Provider Schema抛出异常"""
        with pytest.raises(KeyError, match="未注册的Provider类型"):
            get_provider_schema("nonexistent_provider")

    def test_validate_provider_config_valid(self):
        """测试验证有效的Provider配置"""
        # 测试集中式Schema（未迁移的Provider）
        config = {"room_id": 12345}
        validated = validate_provider_config("bili_danmaku", config)
        assert validated.room_id == 12345

        # 测试自管理Schema（已迁移的Provider）
        from src.core.provider_registry import ProviderRegistry
        from src.domains.input.providers import console_input
        schema = ProviderRegistry.get_config_schema("console_input")
        config = {"type": "console_input", "user_id": "test_user"}
        validated = schema(**config)
        assert validated.type == "console_input"
        assert validated.user_id == "test_user"

    def test_validate_provider_config_invalid(self):
        """测试验证无效的Provider配置抛出异常"""
        # 无效的配置（缺少必需字段）
        with pytest.raises(Exception):  # ValidationError from pydantic
            validate_provider_config("bili_danmaku", {})  # room_id is required

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
        """测试MaiCore Schema验证"""
        from src.services.config.schemas import MaiCoreDecisionProviderConfig

        config = MaiCoreDecisionProviderConfig(
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
        from src.domains.output.providers.tts import TTSProvider

        config = TTSProvider.ConfigSchema(
            engine="edge",
            voice="zh-CN-XiaoxiaoNeural",
        )
        assert config.engine == "edge"
        assert config.voice == "zh-CN-XiaoxiaoNeural"


class TestRegistryConsistency:
    """测试注册表一致性"""

    def test_input_decision_providers_have_type_field(self):
        """测试输入和决策Provider的type字段与注册表键一致"""
        from src.services.config.schemas import PROVIDER_SCHEMA_REGISTRY
        from pydantic import ValidationError

        # 只检查仍在集中式注册表的input和decision providers（它们应该有type字段）
        # 已迁移到自管理Schema的provider不在此测试范围
        providers_with_type = [
            # "console_input",  # 已迁移到自管理Schema
            "bili_danmaku", "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            # "mock_danmaku",  # 已迁移到自管理Schema
            "read_pingmu", "mainosaba", "remote_stream",
            "maicore", "local_llm", "rule_engine", "mock",
        ]

        for provider_type in providers_with_type:
            schema_class = PROVIDER_SCHEMA_REGISTRY[provider_type]
            try:
                # 尝试创建实例（有些schema有必需字段）
                instance = schema_class()
            except ValidationError:
                # 如果有必需字段，使用model_fields验证type字段存在
                assert "type" in schema_class.model_fields, f"{schema_class.__name__} missing 'type' field"

                # 验证type字段的默认值
                type_field = schema_class.model_fields["type"]
                assert type_field.default == provider_type, (
                    f"Type mismatch: registry key '{provider_type}' != "
                    f"schema default '{type_field.default}'"
                )
            else:
                # 无必需字段，直接验证
                assert hasattr(instance, "type"), f"{schema_class.__name__} missing 'type' field"
                assert instance.type == provider_type, (
                    f"Type mismatch: registry key '{provider_type}' != "
                    f"schema field '{instance.type}'"
                )

    def test_registry_is_complete(self):
        """测试注册表包含所有必要的Provider类型（已迁移的除外）"""
        from src.services.config.schemas import PROVIDER_SCHEMA_REGISTRY
        from src.core.provider_registry import ProviderRegistry

        # 需要先导入已迁移的provider模块，ProviderRegistry才会有它们的schema
        from src.domains.input.providers import console_input, mock_danmaku
        from src.domains.output.providers import subtitle, vts, tts

        # 验证关键provider存在（包括已迁移到自管理Schema的）
        critical_providers = [
            # Input
            "console_input", "bili_danmaku", "mock_danmaku",
            # Decision
            "maicore", "local_llm", "rule_engine",
            # Output
            "subtitle", "vts", "tts", "gptsovits",
        ]

        for provider in critical_providers:
            # 检查是否在集中式注册表或ProviderRegistry中
            in_central = provider in PROVIDER_SCHEMA_REGISTRY
            in_provider_registry = ProviderRegistry.get_config_schema(provider) is not None
            assert in_central or in_provider_registry, (
                f"Critical provider '{provider}' not in centralized registry or ProviderRegistry"
            )

    def test_output_providers_in_map(self):
        """测试输出Provider在OUTPUT_PROVIDER_CONFIG_MAP中（已迁移的除外）"""
        from src.services.config.schemas import OUTPUT_PROVIDER_CONFIG_MAP

        # 已迁移到自管理Schema的provider不在此映射中
        expected_output_providers = [
            # "subtitle",  # 已迁移到自管理Schema
            # "vts",  # 已迁移到自管理Schema
            # "tts",  # 已迁移到自管理Schema
            "sticker", "warudo",
            "obs_control", "gptsovits", "omni_tts", "avatar", "remote_stream",
        ]

        for provider in expected_output_providers:
            assert provider in OUTPUT_PROVIDER_CONFIG_MAP, f"Output provider '{provider}' not in OUTPUT_PROVIDER_CONFIG_MAP"
