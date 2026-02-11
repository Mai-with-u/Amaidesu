"""
Provider Schema 完整性测试

验证 Provider Schema 自管理架构的完整性和一致性：
1. 所有 Provider 都定义了 ConfigSchema
2. 所有 ConfigSchema 都正确注册到 ProviderRegistry
3. 配置验证逻辑正确工作
4. 三级配置合并机制正确
"""

from pathlib import Path

import pytest

import src.domains.decision.providers  # noqa: F401

# 触发 Provider 注册（必须在导入之前执行）
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.registry import ProviderRegistry


class TestProviderSchemaCompleteness:
    """测试 Provider Schema 完整性"""

    def test_all_providers_have_config_schema(self):
        """测试所有已注册的 Provider 都定义了 ConfigSchema

        架构要求：
        - 每个 Provider 都应定义 ConfigSchema 嵌套类
        - ConfigSchema 必须继承 BaseProviderConfig
        - ConfigSchema 在 Provider 注册时自动提取
        """

        # 获取所有已注册的 Provider
        registry_info = ProviderRegistry.get_registry_info()

        missing_schemas = []

        # 检查 Input Providers
        for name, info in registry_info["input_providers"].items():
            provider_class = ProviderRegistry._input_providers[name]
            if not hasattr(provider_class, "ConfigSchema"):
                missing_schemas.append(f"Input Provider '{name}' ({info['class']}) missing ConfigSchema")
            else:
                # 验证 ConfigSchema 继承 BaseProviderConfig
                schema_cls = provider_class.ConfigSchema
                if not issubclass(schema_cls, BaseProviderConfig):
                    missing_schemas.append(f"Input Provider '{name}' ConfigSchema does not inherit BaseProviderConfig")

        # 检查 Output Providers
        for name, info in registry_info["output_providers"].items():
            provider_class = ProviderRegistry._output_providers[name]
            if not hasattr(provider_class, "ConfigSchema"):
                missing_schemas.append(f"Output Provider '{name}' ({info['class']}) missing ConfigSchema")
            else:
                schema_cls = provider_class.ConfigSchema
                if not issubclass(schema_cls, BaseProviderConfig):
                    missing_schemas.append(f"Output Provider '{name}' ConfigSchema does not inherit BaseProviderConfig")

        # 检查 Decision Providers
        for name, info in registry_info["decision_providers"].items():
            provider_class = ProviderRegistry._decision_providers[name]
            if not hasattr(provider_class, "ConfigSchema"):
                missing_schemas.append(f"Decision Provider '{name}' ({info['class']}) missing ConfigSchema")
            else:
                schema_cls = provider_class.ConfigSchema
                if not issubclass(schema_cls, BaseProviderConfig):
                    missing_schemas.append(
                        f"Decision Provider '{name}' ConfigSchema does not inherit BaseProviderConfig"
                    )

        # 断言：所有 Provider 都应有 ConfigSchema
        assert len(missing_schemas) == 0, (
            f"Found {len(missing_schemas)} providers without valid ConfigSchema:\n"
            + "\n".join(f"  - {msg}" for msg in missing_schemas)
        )

    def test_all_config_schemas_registered(self):
        """测试所有 ConfigSchema 都已注册到 ProviderRegistry._config_schemas

        架构要求：
        - Provider 注册时自动提取 ConfigSchema
        - ConfigSchema 存储在 ProviderRegistry._config_schemas
        - 可以通过 get_config_schema() 获取
        """
        from src.modules.registry import ProviderRegistry

        # 获取所有已注册的 Provider
        registry_info = ProviderRegistry.get_registry_info()

        unregistered_schemas = []

        # 检查 Input Providers
        for name in registry_info["input_providers"].keys():
            provider_class = ProviderRegistry._input_providers[name]
            if hasattr(provider_class, "ConfigSchema"):
                # 检查是否在 _config_schemas 中
                if name not in ProviderRegistry._config_schemas:
                    unregistered_schemas.append(f"Input Provider '{name}' ConfigSchema not in _config_schemas")
                else:
                    # 验证 get_config_schema 能正确获取
                    schema = ProviderRegistry.get_config_schema(name)
                    if schema is None:
                        unregistered_schemas.append(f"Input Provider '{name}' get_config_schema() returned None")

        # 检查 Output Providers
        for name in registry_info["output_providers"].keys():
            provider_class = ProviderRegistry._output_providers[name]
            if hasattr(provider_class, "ConfigSchema"):
                if name not in ProviderRegistry._config_schemas:
                    unregistered_schemas.append(f"Output Provider '{name}' ConfigSchema not in _config_schemas")
                else:
                    schema = ProviderRegistry.get_config_schema(name)
                    if schema is None:
                        unregistered_schemas.append(f"Output Provider '{name}' get_config_schema() returned None")

        # 检查 Decision Providers
        for name in registry_info["decision_providers"].keys():
            provider_class = ProviderRegistry._decision_providers[name]
            if hasattr(provider_class, "ConfigSchema"):
                if name not in ProviderRegistry._config_schemas:
                    unregistered_schemas.append(f"Decision Provider '{name}' ConfigSchema not in _config_schemas")
                else:
                    schema = ProviderRegistry.get_config_schema(name)
                    if schema is None:
                        unregistered_schemas.append(f"Decision Provider '{name}' get_config_schema() returned None")

        # 断言：所有 ConfigSchema 都应已注册
        assert len(unregistered_schemas) == 0, (
            f"Found {len(unregistered_schemas)} ConfigSchemas not properly registered:\n"
            + "\n".join(f"  - {msg}" for msg in unregistered_schemas)
        )

    def test_config_schema_registration_consistency(self):
        """测试 ConfigSchema 注册的一致性

        验证：
        - _config_schemas 中的 Schema 类与 Provider.ConfigSchema 一致
        - Schema 名称正确对应 Provider 名称
        """
        from src.modules.registry import ProviderRegistry

        inconsistencies = []

        # 检查所有已注册的 ConfigSchema
        for provider_name, schema_class in ProviderRegistry._config_schemas.items():
            # 获取对应的 Provider 类
            provider_class = None
            if provider_name in ProviderRegistry._input_providers:
                provider_class = ProviderRegistry._input_providers[provider_name]
            elif provider_name in ProviderRegistry._output_providers:
                provider_class = ProviderRegistry._output_providers[provider_name]
            elif provider_name in ProviderRegistry._decision_providers:
                provider_class = ProviderRegistry._decision_providers[provider_name]
            else:
                inconsistencies.append(
                    f"ConfigSchema '{provider_name}' registered but Provider not found in any registry"
                )
                continue

            # 验证 Schema 类与 Provider.ConfigSchema 一致
            if hasattr(provider_class, "ConfigSchema"):
                if provider_class.ConfigSchema != schema_class:
                    inconsistencies.append(
                        f"Provider '{provider_name}' ConfigSchema mismatch: "
                        f"registered={schema_class}, actual={provider_class.ConfigSchema}"
                    )

        assert len(inconsistencies) == 0, (
            f"Found {len(inconsistencies)} ConfigSchema registration inconsistencies:\n"
            + "\n".join(f"  - {msg}" for msg in inconsistencies)
        )


class TestConfigValidation:
    """测试配置验证逻辑"""

    def test_valid_config_passes_validation(self):
        """测试有效配置能通过验证"""
        from src.domains.decision.providers.maicore.maicore_decision_provider import (
            MaiCoreDecisionProvider,
        )
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.audio import EdgeTTSProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 测试 ConsoleInput (自管理 Schema)
        schema = ConsoleInputProvider.ConfigSchema
        config = schema(type="console_input", user_id="test_user")
        assert config.type == "console_input"
        assert config.user_id == "test_user"

        # 测试 Subtitle (自管理 Schema)
        schema = SubtitleOutputProvider.ConfigSchema
        config = schema(window_width=800, window_height=100)
        assert config.window_width == 800
        assert config.window_height == 100

        # 测试 EdgeTTS (自管理 Schema)
        schema = EdgeTTSProvider.ConfigSchema
        config = schema(voice="zh-CN-XiaoxiaoNeural")
        assert config.type == "edge_tts"
        assert config.voice == "zh-CN-XiaoxiaoNeural"

        # 测试 MaiCore (自管理 Schema)
        schema = MaiCoreDecisionProvider.ConfigSchema
        config = schema(type="maicore", host="localhost", port=8000)
        assert config.type == "maicore"
        assert config.host == "localhost"
        assert config.port == 8000

    def test_invalid_config_rejected(self):
        """测试无效配置会被拒绝"""
        from pydantic import ValidationError

        from src.domains.input.providers.console_input import ConsoleInputProvider

        # 测试类型错误
        with pytest.raises(ValidationError):
            ConsoleInputProvider.ConfigSchema(type=123)  # type 应该是字符串

        # 测试额外字段（应被忽略，不报错，因为 extra='ignore'）
        # 这不应该抛出异常
        config = ConsoleInputProvider.ConfigSchema(type="console_input", unknown_field="value")
        assert config.type == "console_input"

    def test_schema_default_values(self):
        """测试 Schema 默认值正确"""
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 测试 ConsoleInput 默认值
        schema = ConsoleInputProvider.ConfigSchema
        config = schema()
        assert config.type == "console_input"
        assert config.user_id == "console_user"  # 实际默认值
        assert config.user_nickname == "控制台"

        # 测试 Subtitle 默认值
        schema = SubtitleOutputProvider.ConfigSchema
        config = schema()
        assert config.window_width == 800  # 实际默认值
        assert config.window_height == 100  # 实际默认值


class TestConfigMergeMechanism:
    """测试三级配置合并机制"""

    def test_get_default_dict(self):
        """测试 get_default_dict 返回完整默认配置"""
        from src.domains.input.providers.console_input import ConsoleInputProvider

        schema = ConsoleInputProvider.ConfigSchema
        default_dict = schema.get_default_dict()

        # 验证包含所有字段
        assert "type" in default_dict
        assert "user_id" in default_dict
        assert "user_nickname" in default_dict

        # 验证默认值
        assert default_dict["type"] == "console_input"
        assert default_dict["user_id"] == "console_user"  # 实际默认值
        assert default_dict["user_nickname"] == "控制台"  # 实际默认值

    def test_schema_field_types(self):
        """测试 Schema 字段类型正确"""
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 测试 ConsoleInput 字段类型
        schema = ConsoleInputProvider.ConfigSchema
        assert "type" in schema.model_fields
        assert "user_id" in schema.model_fields

        # 测试 Subtitle 字段类型
        schema = SubtitleOutputProvider.ConfigSchema
        assert "window_width" in schema.model_fields
        assert "window_height" in schema.model_fields

    def test_config_override_priority(self):
        """测试配置优先级：用户配置 > 默认配置"""
        from src.domains.input.providers.console_input import ConsoleInputProvider

        # 默认配置
        default_config = ConsoleInputProvider.ConfigSchema.get_default_dict()
        assert default_config["user_id"] == "console_user"  # 实际默认值

        # 用户配置覆盖
        user_config = ConsoleInputProvider.ConfigSchema(user_id="custom_user")
        assert user_config.user_id == "custom_user"

        # 部分覆盖（其他字段使用默认值）
        partial_config = ConsoleInputProvider.ConfigSchema(user_id="partial_user")
        assert partial_config.user_id == "partial_user"
        assert partial_config.type == "console_input"  # 使用默认值


class TestCriticalProviders:
    """测试关键 Provider 的 Schema 完整性"""

    def test_console_input_schema_complete(self):
        """测试 ConsoleInputProvider Schema 完整"""
        from src.domains.input.providers.console_input import ConsoleInputProvider

        # 验证 ConfigSchema 存在
        assert hasattr(ConsoleInputProvider, "ConfigSchema")

        # 验证继承 BaseProviderConfig
        assert issubclass(ConsoleInputProvider.ConfigSchema, BaseProviderConfig)

        # 验证注册到 ProviderRegistry
        schema = ProviderRegistry.get_config_schema("console_input")
        assert schema is not None
        assert schema == ConsoleInputProvider.ConfigSchema

        # 验证必需字段
        assert "type" in ConsoleInputProvider.ConfigSchema.model_fields

    def test_subtitle_schema_complete(self):
        """测试 SubtitleOutputProvider Schema 完整"""
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 验证 ConfigSchema 存在
        assert hasattr(SubtitleOutputProvider, "ConfigSchema")

        # 验证继承 BaseProviderConfig
        assert issubclass(SubtitleOutputProvider.ConfigSchema, BaseProviderConfig)

        # 验证注册
        schema = ProviderRegistry.get_config_schema("subtitle")
        assert schema is not None
        assert schema == SubtitleOutputProvider.ConfigSchema

        # 验证字段
        assert "window_width" in SubtitleOutputProvider.ConfigSchema.model_fields
        assert "window_height" in SubtitleOutputProvider.ConfigSchema.model_fields

    def test_tts_schema_complete(self):
        """测试 EdgeTTSProvider Schema 完整"""
        from src.domains.output.providers.audio import EdgeTTSProvider

        # 验证 ConfigSchema 存在
        assert hasattr(EdgeTTSProvider, "ConfigSchema")

        # 验证继承 BaseProviderConfig
        assert issubclass(EdgeTTSProvider.ConfigSchema, BaseProviderConfig)

        # 验证注册
        schema = ProviderRegistry.get_config_schema("edge_tts")
        assert schema is not None
        assert schema == EdgeTTSProvider.ConfigSchema

        # 验证字段
        assert "voice" in EdgeTTSProvider.ConfigSchema.model_fields
        assert "type" in EdgeTTSProvider.ConfigSchema.model_fields

    def test_vts_schema_complete(self):
        """测试 VTSProvider Schema 完整"""
        from src.domains.output.providers.avatar.vts.vts_provider import VTSProvider

        # 验证 ConfigSchema 存在
        assert hasattr(VTSProvider, "ConfigSchema")

        # 验证继承 BaseProviderConfig
        assert issubclass(VTSProvider.ConfigSchema, BaseProviderConfig)

        # 验证注册
        schema = ProviderRegistry.get_config_schema("vts")
        assert schema is not None
        assert schema == VTSProvider.ConfigSchema

    def test_maicore_schema_complete(self):
        """测试 MaiCoreDecisionProvider Schema 完整"""
        from src.domains.decision.providers.maicore.maicore_decision_provider import (
            MaiCoreDecisionProvider,
        )

        # 验证 ConfigSchema 存在
        assert hasattr(MaiCoreDecisionProvider, "ConfigSchema")

        # 验证继承 BaseProviderConfig
        assert issubclass(MaiCoreDecisionProvider.ConfigSchema, BaseProviderConfig)

        # 验证注册
        schema = ProviderRegistry.get_config_schema("maicore")
        assert schema is not None
        assert schema == MaiCoreDecisionProvider.ConfigSchema

        # 验证字段
        assert "type" in MaiCoreDecisionProvider.ConfigSchema.model_fields
        assert "host" in MaiCoreDecisionProvider.ConfigSchema.model_fields
        assert "port" in MaiCoreDecisionProvider.ConfigSchema.model_fields


class TestSchemaTypeSafety:
    """测试 Schema 类型安全"""

    def test_schema_strict_typed(self):
        """测试 Schema 使用严格类型注解"""
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 验证字段有类型注解
        console_fields = ConsoleInputProvider.ConfigSchema.model_fields
        for field_name, field_info in console_fields.items():
            assert field_info.annotation is not None, (
                f"ConsoleInputProvider.ConfigSchema.{field_name} missing type annotation"
            )

        subtitle_fields = SubtitleOutputProvider.ConfigSchema.model_fields
        for field_name, field_info in subtitle_fields.items():
            assert field_info.annotation is not None, (
                f"SubtitleOutputProvider.ConfigSchema.{field_name} missing type annotation"
            )

    def test_schema_field_descriptions(self):
        """测试 Schema 字段有描述信息"""
        from src.domains.input.providers.console_input import ConsoleInputProvider
        from src.domains.output.providers.subtitle import SubtitleOutputProvider

        # 验证关键字段有 description
        console_fields = ConsoleInputProvider.ConfigSchema.model_fields
        # 注意：type 字段可能没有 description（因为是 Literal 类型）
        assert console_fields["user_id"].description, "user_id field missing description"
        assert console_fields["user_nickname"].description, "user_nickname field missing description"

        subtitle_fields = SubtitleOutputProvider.ConfigSchema.model_fields
        assert subtitle_fields["window_width"].description, "window_width field missing description"
        assert subtitle_fields["window_height"].description, "window_height field missing description"


class TestSchemaIntegration:
    """测试 Schema 与配置系统集成"""

    def test_schema_generates_valid_toml(self):
        """测试 Schema 能生成有效的 TOML 配置"""
        import tempfile

        from src.domains.input.providers.console_input import ConsoleInputProvider

        # 生成临时 TOML 文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            temp_path = f.name

        try:
            # 生成 TOML
            ConsoleInputProvider.ConfigSchema.generate_toml(temp_path, "console_input")

            # 验证文件存在
            assert Path(temp_path).exists()

            # 验证内容
            content = Path(temp_path).read_text(encoding="utf-8")
            assert "[console_input]" in content
            assert 'type = "console_input"' in content

        finally:
            # 清理临时文件
            Path(temp_path).unlink(missing_ok=True)

    def test_all_critical_schemas_generatable(self):
        """测试所有关键 Provider Schema 都能生成 TOML"""
        import tempfile

        critical_providers = [
            ("console_input", "input"),
            ("subtitle", "output"),
            ("edge_tts", "output"),
            ("vts", "output"),
            ("maicore", "decision"),
        ]

        temp_files = []

        try:
            for provider_name, _domain in critical_providers:
                # 获取 Schema
                schema = ProviderRegistry.get_config_schema(provider_name)
                assert schema is not None, f"Provider '{provider_name}' schema not found"

                # 生成临时 TOML
                with tempfile.NamedTemporaryFile(mode="w", suffix=f".{provider_name}.toml", delete=False) as f:
                    temp_path = f.name
                    temp_files.append(temp_path)

                # 生成 TOML
                schema.generate_toml(temp_path, provider_name)

                # 验证文件存在且非空
                assert Path(temp_path).exists(), f"Failed to generate TOML for '{provider_name}'"
                content = Path(temp_path).read_text(encoding="utf-8")
                assert len(content) > 0, f"Generated TOML for '{provider_name}' is empty"

        finally:
            # 清理所有临时文件
            for temp_path in temp_files:
                Path(temp_path).unlink(missing_ok=True)
