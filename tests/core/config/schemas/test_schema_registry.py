"""
测试Provider Schema注册表和enabled字段检测

This module tests:
1. Schema registry completeness (all 22 providers registered)
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
        """测试注册表包含全部22个Provider"""
        providers = list_all_providers()

        # 验证总数
        assert providers["total"] == 22, f"Expected 22 providers, got {providers['total']}"

        # 验证分类数量
        assert len(providers["input"]) == 8, f"Expected 8 input providers, got {len(providers['input'])}"
        assert len(providers["decision"]) == 4, f"Expected 4 decision providers, got {len(providers['decision'])}"
        assert len(providers["output"]) == 10, f"Expected 10 output providers, got {len(providers['output'])}"

        # 验证特定provider存在
        assert "console_input" in PROVIDER_SCHEMA_REGISTRY
        assert "bili_danmaku" in PROVIDER_SCHEMA_REGISTRY
        assert "maicore" in PROVIDER_SCHEMA_REGISTRY
        assert "subtitle" in PROVIDER_SCHEMA_REGISTRY
        assert "tts" in PROVIDER_SCHEMA_REGISTRY

    def test_input_providers_registry(self):
        """测试输入Provider注册完整"""
        input_providers = [
            "console_input",
            "bili_danmaku",
            "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            "mock_danmaku",
            "read_pingmu",
            "mainosaba",
            "remote_stream",
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
        """测试输出Provider注册完整"""
        output_providers = [
            "subtitle",
            "vts",
            "tts",
            "sticker",
            "warudo",
            "obs_control",
            "gptsovits",
            "omni_tts",
            "avatar",
            "remote_stream_output",
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
            ConsoleInputProviderConfig,
            MaiCoreDecisionProviderConfig,
            SubtitleProviderConfig,
            TTSProviderConfig,
        )

        # 检查几个关键schema
        schemas_to_check = [
            ConsoleInputProviderConfig,
            MaiCoreDecisionProviderConfig,
            SubtitleProviderConfig,
            TTSProviderConfig,
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
        """测试获取存在的Provider Schema"""
        schema = get_provider_schema("console_input")
        assert schema is not None
        assert schema.__name__ == "ConsoleInputProviderConfig"

    def test_get_provider_schema_invalid(self):
        """测试获取不存在的Provider Schema抛出异常"""
        with pytest.raises(KeyError, match="未注册的Provider类型"):
            get_provider_schema("nonexistent_provider")

    def test_validate_provider_config_valid(self):
        """测试验证有效的Provider配置"""
        config = {"type": "console_input", "user_id": "test_user"}
        validated = validate_provider_config("console_input", config)

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
        """测试ConsoleInput Schema验证"""
        from src.services.config.schemas import ConsoleInputProviderConfig

        # 有效配置
        config = ConsoleInputProviderConfig(type="console_input")
        assert config.type == "console_input"

        # 带额外字段
        config = ConsoleInputProviderConfig(type="console_input", user_id="test")
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
        """测试Subtitle Schema验证"""
        from src.services.config.schemas import SubtitleProviderConfig

        config = SubtitleProviderConfig(
            window_width=800,
            window_height=100,
        )
        assert config.window_width == 800
        assert config.window_height == 100

    def test_tts_schema_validation(self):
        """测试TTS Schema验证"""
        from src.services.config.schemas import TTSProviderConfig

        config = TTSProviderConfig(
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

        # 只检查input和decision providers（它们应该有type字段）
        providers_with_type = [
            "console_input", "bili_danmaku", "bili_danmaku_official",
            "bili_danmaku_official_maicraft", "mock_danmaku",
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
        """测试注册表包含所有必要的Provider类型"""
        from src.services.config.schemas import PROVIDER_SCHEMA_REGISTRY

        # 验证关键provider存在
        critical_providers = [
            # Input
            "console_input", "bili_danmaku",
            # Decision
            "maicore", "local_llm", "rule_engine",
            # Output
            "subtitle", "vts", "tts", "gptsovits",
        ]

        for provider in critical_providers:
            assert provider in PROVIDER_SCHEMA_REGISTRY, f"Critical provider '{provider}' not in registry"

    def test_output_providers_in_map(self):
        """测试输出Provider在OUTPUT_PROVIDER_CONFIG_MAP中"""
        from src.services.config.schemas import OUTPUT_PROVIDER_CONFIG_MAP

        expected_output_providers = [
            "subtitle", "vts", "tts", "sticker", "warudo",
            "obs_control", "gptsovits", "omni_tts", "avatar", "remote_stream",
        ]

        for provider in expected_output_providers:
            assert provider in OUTPUT_PROVIDER_CONFIG_MAP, f"Output provider '{provider}' not in OUTPUT_PROVIDER_CONFIG_MAP"
