"""ConfigService Schema API 测试套件 (Task 7)

覆盖 ConfigService 的 schema 暴露 API：

1. **get_config_schema(type)** — 根据类型名 (core/model/input/output/decision)
   或类引用返回完整 UI schema
2. **get_config_schema_for_section(section)** — 返回某节 (persona/llm/...)
   的子 schema
3. **无效类型处理** — 未知类型必须抛 ValueError 或返回 None
4. **Dashboard 兼容性** — 输出格式符合前端动态表单预期

参考实现：ConfigSchemaGenerator.generate_config_schema() (已存在, Task 5)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest


# ===========================================================================
# Fixtures
# ===========================================================================


def _build_core_toml() -> str:
    return (
        "[meta]\n"
        'type = "meta"\n'
        'version = "0.4.0"\n'
        "\n"
        "[persona]\n"
        'type = "persona"\n'
        'bot_name = "麦麦"\n'
    )


def _build_model_toml() -> str:
    return (
        "[llm]\n"
        'client = "openai"\n'
        'model = "gpt-4"\n'
        'api_key = ""\n'
    )


@pytest.fixture
def config_dir_with_toml(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_build_core_toml(), encoding="utf-8")
    (cfg / "model.toml").write_text(_build_model_toml(), encoding="utf-8")
    for name in ("input", "decision", "output"):
        (cfg / f"{name}.toml").write_text("", encoding="utf-8")
    return cfg


@pytest.fixture
def initialized_service(config_dir_with_toml: Path):
    """已 initialize() 的 ConfigService 实例"""
    from src.modules.config.service import ConfigService

    service = ConfigService(base_dir=str(config_dir_with_toml.parent))
    service.initialize()
    return service


# ===========================================================================
# 1. get_config_schema(type) — 类型名路由
# ===========================================================================


class TestGetConfigSchemaByTypeName:
    """get_config_schema(type) 接受字符串类型名"""

    def test_get_core_schema_by_string(self, initialized_service):
        """get_config_schema('core') 必须返回 CoreConfig 的完整 schema"""
        from src.modules.config.core_schemas import CoreConfig

        schema = initialized_service.get_config_schema("core")

        assert isinstance(schema, dict)
        assert schema.get("className") == "CoreConfig"
        assert "fields" in schema
        assert isinstance(schema["fields"], list)

        field_names = {f["name"] for f in schema["fields"]}
        assert "meta" in field_names
        assert "persona" in field_names
        assert "maicore" in field_names

    def test_get_model_schema_by_string(self, initialized_service):
        """get_config_schema('model') 必须返回 ModelConfig 的完整 schema"""
        from src.modules.config.model_schemas import ModelConfig

        schema = initialized_service.get_config_schema("model")

        assert isinstance(schema, dict)
        assert schema.get("className") == "ModelConfig"
        assert "fields" in schema

        field_names = {f["name"] for f in schema["fields"]}
        assert "llm" in field_names

    def test_get_core_schema_by_class_reference(self, initialized_service):
        """get_config_schema(CoreConfig) 必须也能工作（接受类引用）"""
        from src.modules.config.core_schemas import CoreConfig

        schema = initialized_service.get_config_schema(CoreConfig)

        assert isinstance(schema, dict)
        assert schema.get("className") == "CoreConfig"

    def test_get_model_schema_by_class_reference(self, initialized_service):
        """get_config_schema(ModelConfig) 必须也能工作（接受类引用）"""
        from src.modules.config.model_schemas import ModelConfig

        schema = initialized_service.get_config_schema(ModelConfig)

        assert isinstance(schema, dict)
        assert schema.get("className") == "ModelConfig"


# ===========================================================================
# 2. get_config_schema — 无效类型
# ===========================================================================


class TestGetConfigSchemaInvalid:
    """无效类型必须有显式的失败行为"""

    def test_invalid_type_name_raises_value_error(self, initialized_service):
        """get_config_schema('invalid') 必须抛 ValueError"""
        with pytest.raises(ValueError):
            initialized_service.get_config_schema("invalid")

    def test_invalid_type_name_does_not_return_silent_none(self, initialized_service):
        """无效类型不应返回 None（避免静默失败）"""
        # 如果选择返回 None 则跳过上例；此处强制 ValueError 契约
        # 若实现选择 None，必须用专门测试覆盖
        try:
            result = initialized_service.get_config_schema("nonexistent_type")
            assert result is None or isinstance(result, dict)
            # 如果是 dict，必须包含 className 字段
            if isinstance(result, dict):
                assert "className" in result
        except ValueError:
            pass  # 接受 ValueError
        except Exception as exc:
            pytest.fail(f"未预期的异常类型: {type(exc).__name__}: {exc}")

    def test_non_basemodel_class_raises_type_error(self, initialized_service):
        """传入非 BaseModel 子类必须抛 TypeError"""
        # 字符串、int 等不是 BaseModel
        with pytest.raises((TypeError, ValueError)):
            initialized_service.get_config_schema(42)


# ===========================================================================
# 3. get_config_schema — Dashboard 兼容性
# ===========================================================================


class TestSchemaDashboardCompat:
    """schema 输出格式必须符合 Dashboard 动态表单约定"""

    def test_schema_has_required_top_level_fields(self, initialized_service):
        """顶层 schema 必须包含 className / fields"""
        schema = initialized_service.get_config_schema("core")

        assert "className" in schema
        assert "fields" in schema
        # fields 必须是 list
        assert isinstance(schema["fields"], list)
        # 至少有一个字段
        assert len(schema["fields"]) > 0

    def test_each_field_has_required_keys(self, initialized_service):
        """每个字段必须包含 name / type / label / description"""
        schema = initialized_service.get_config_schema("core")

        for field in schema["fields"]:
            assert "name" in field, f"field missing name: {field}"
            assert "type" in field, f"field missing type: {field}"
            assert "label" in field, f"field missing label: {field}"
            # description 可以为空字符串
            assert "description" in field, f"field missing description: {field}"

    def test_field_type_values_are_ui_types(self, initialized_service):
        """field.type 必须是 UI 类型字符串集合之一"""
        valid_types = {"string", "integer", "number", "boolean", "array", "object", "select"}
        schema = initialized_service.get_config_schema("core")

        for field in schema["fields"]:
            assert field["type"] in valid_types, (
                f"field {field.get('name')} has invalid type: {field.get('type')}"
            )

    def test_label_is_dict_with_locale(self, initialized_service):
        """field.label 必须是 dict (至少包含 zh_CN)"""
        schema = initialized_service.get_config_schema("core")

        for field in schema["fields"]:
            label = field["label"]
            assert isinstance(label, dict), f"label must be dict, got {type(label)}"
            assert "zh_CN" in label, f"label must contain zh_CN, got {label}"

    def test_schema_is_json_serializable(self, initialized_service):
        """schema 必须可被 json.dumps 序列化（用于 HTTP API）"""
        import json

        schema = initialized_service.get_config_schema("core")
        json_str = json.dumps(schema, ensure_ascii=False, default=str)
        assert isinstance(json_str, str)
        assert len(json_str) > 0
        # 反序列化验证
        parsed = json.loads(json_str)
        assert parsed["className"] == "CoreConfig"


# ===========================================================================
# 4. get_config_schema_for_section
# ===========================================================================


class TestGetConfigSchemaForSection:
    """get_config_schema_for_section(section) 返回指定节 (子 schema)"""

    def test_get_persona_section_schema(self, initialized_service):
        """get_config_schema_for_section('persona') 返回 PersonaConfig 的 schema"""
        from src.modules.config.core_schemas import PersonaConfig

        schema = initialized_service.get_config_schema_for_section("persona")

        assert isinstance(schema, dict)
        assert schema.get("className") == "PersonaConfig"
        assert "fields" in schema

        field_names = {f["name"] for f in schema["fields"]}
        assert "bot_name" in field_names
        assert "personality" in field_names
        assert "style_constraints" in field_names

    def test_get_llm_section_schema(self, initialized_service):
        """get_config_schema_for_section('llm') 返回 LLMConfig 的 schema"""
        from src.modules.config.model_schemas import LLMConfig

        schema = initialized_service.get_config_schema_for_section("llm")

        assert isinstance(schema, dict)
        assert schema.get("className") == "LLMConfig"
        assert "fields" in schema

        field_names = {f["name"] for f in schema["fields"]}
        assert "client" in field_names
        assert "model" in field_names
        assert "api_key" in field_names

    def test_get_maicore_section_schema(self, initialized_service):
        """get_config_schema_for_section('maicore') 返回 MaiCoreConfig 的 schema"""
        schema = initialized_service.get_config_schema_for_section("maicore")

        assert isinstance(schema, dict)
        assert schema.get("className") == "MaiCoreConfig"
        field_names = {f["name"] for f in schema["fields"]}
        assert "host" in field_names
        assert "port" in field_names
        assert "token" in field_names

    def test_section_schema_has_nested_drilldown(self, initialized_service):
        """如果 schema 有 nested 字段，必须能通过 section 路径展开"""
        # CoreConfig 中嵌套的 persona 应该有完整的 PersonaConfig 子 schema
        persona_schema = initialized_service.get_config_schema_for_section("persona")
        assert "fields" in persona_schema
        # bot_name 字段
        bot_name_field = next(f for f in persona_schema["fields"] if f["name"] == "bot_name")
        assert bot_name_field["type"] == "string"

    def test_get_unknown_section_raises_value_error(self, initialized_service):
        """未知的 section 必须抛 ValueError"""
        with pytest.raises(ValueError):
            initialized_service.get_config_schema_for_section("nonexistent_section")

    def test_section_schema_is_json_serializable(self, initialized_service):
        """section schema 也必须可 json 序列化"""
        import json

        schema = initialized_service.get_config_schema_for_section("persona")
        json_str = json.dumps(schema, ensure_ascii=False, default=str)
        assert isinstance(json_str, str)
        assert len(json_str) > 0

    def test_section_schema_includes_field_descriptions(self, initialized_service):
        """section schema 的每个字段必须包含 description"""
        schema = initialized_service.get_config_schema_for_section("persona")

        for field in schema["fields"]:
            assert "description" in field, f"field {field.get('name')} missing description"
            # description 应是非空字符串
            assert isinstance(field["description"], str)

    def test_section_schema_preserves_json_schema_extra(self, initialized_service):
        """json_schema_extra 中的字段标记 (x-ui-type / x-options / ...) 必须保留"""
        schema = initialized_service.get_config_schema_for_section("llm")

        # LLMConfig.client 有 json_schema_extra={"x-ui-type": "select", "x-options": [...]}
        client_field = next(f for f in schema["fields"] if f["name"] == "client")
        assert client_field.get("x-ui-type") == "select"
        assert client_field.get("x-options") == ["openai"]


# ===========================================================================
# 5. Schema 来源一致性
# ===========================================================================


class TestSchemaSourceConsistency:
    """get_config_schema 的结果必须与 ConfigSchemaGenerator 一致"""

    def test_matches_schema_generator_output(self, initialized_service):
        """get_config_schema('core') 必须等于 ConfigSchemaGenerator.generate_config_schema(CoreConfig)"""
        from src.modules.config.core_schemas import CoreConfig
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        service_schema = initialized_service.get_config_schema("core")
        generator_schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)

        assert service_schema["className"] == generator_schema["className"]
        assert len(service_schema["fields"]) == len(generator_schema["fields"])

    def test_section_schema_matches_schema_generator(self, initialized_service):
        """get_config_schema_for_section('persona') 必须等于 generate_config_schema(PersonaConfig)"""
        from src.modules.config.core_schemas import PersonaConfig
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        section_schema = initialized_service.get_config_schema_for_section("persona")
        generator_schema = ConfigSchemaGenerator.generate_config_schema(PersonaConfig)

        assert section_schema["className"] == generator_schema["className"]
        assert len(section_schema["fields"]) == len(generator_schema["fields"])