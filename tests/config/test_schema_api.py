"""配置 Schema 生成器测试套件

覆盖 ``ConfigSchemaGenerator`` 对六文件根 Schema 的输出契约：

1. **输出格式** — className / fields / nested 结构符合前端动态表单预期
2. **字段形状** — name / type / label / description 必备，type 为 UI 类型集合
3. **六根覆盖** — 六个根 Schema 全部可生成，自描述协议（文件名/显示名）就位
4. **readonly 透传** — json_schema_extra 的 readonly 标记进入生成结果

消费方：dashboard/api/config.py 的分组构建（写接口拒绝与展示共用同一信号）。
"""

from __future__ import annotations

import json

import pytest

from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.model_schemas import ModelRootConfig
from src.modules.config.schema_generator import ConfigSchemaGenerator
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.tools_schemas import ToolsRootConfig

_SIX_ROOTS = [
    AgentsRootConfig,
    CollectorsRootConfig,
    ToolsRootConfig,
    ModelRootConfig,
    StorageRootConfig,
    InfraRootConfig,
]


def _generate(cls) -> dict:
    return ConfigSchemaGenerator.generate_config_schema(cls)


# ===========================================================================
# 1. 输出格式
# ===========================================================================


class TestSchemaFormat:
    """生成结果必须包含 className / fields 顶层结构"""

    def test_schema_has_required_top_level_fields(self):
        schema = _generate(InfraRootConfig)

        assert schema["className"] == "InfraRootConfig"
        assert isinstance(schema["fields"], list)
        assert len(schema["fields"]) > 0

    def test_schema_is_json_serializable(self):
        """schema 必须可被 json.dumps 序列化（用于 HTTP API）"""
        schema = _generate(ModelRootConfig)
        json_str = json.dumps(schema, ensure_ascii=False, default=str)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["className"] == "ModelRootConfig"

    def test_nested_drilldown_present(self):
        """嵌套 BaseConfig 字段展开进 nested（如 llm_providers 列表的元素类型）；
        dict[str, 子模型] 容器字段不展开（生成器契约：自由键容器按 object 下发）"""
        schema = _generate(ModelRootConfig)
        assert "nested" in schema
        assert "llm_providers" in schema["nested"]
        assert "llm_profiles" not in schema["nested"]


# ===========================================================================
# 2. 字段形状
# ===========================================================================


class TestFieldShape:
    """每个字段必须携带 name / type / label / description，type 为 UI 类型"""

    def test_each_field_has_required_keys(self):
        schema = _generate(InfraRootConfig)
        for field in schema["fields"]:
            assert "name" in field, f"field missing name: {field}"
            assert "type" in field, f"field missing type: {field}"
            assert "label" in field, f"field missing label: {field}"
            assert "description" in field, f"field missing description: {field}"

    def test_field_type_values_are_ui_types(self):
        valid_types = {"string", "integer", "number", "boolean", "array", "object", "select"}
        for cls in _SIX_ROOTS:
            schema = _generate(cls)
            for field in schema["fields"]:
                assert field["type"] in valid_types, (
                    f"{cls.__name__}.{field.get('name')} 非法 UI 类型: {field.get('type')}"
                )

    def test_label_is_dict_with_locale(self):
        schema = _generate(ToolsRootConfig)
        for field in schema["fields"]:
            label = field["label"]
            assert isinstance(label, dict), f"label must be dict, got {type(label)}"
            assert "zh_CN" in label, f"label must contain zh_CN, got {label}"


# ===========================================================================
# 3. 六根覆盖与自描述协议
# ===========================================================================


class TestSixRootSchemas:
    """六个根 Schema 全部可生成，自描述协议与加载器索引一致"""

    def test_all_six_roots_generate(self):
        for cls in _SIX_ROOTS:
            schema = _generate(cls)
            assert schema["className"] == cls.__name__

    def test_self_described_file_names(self):
        """__file_name__ 声明覆盖六文件，且与类一一对应"""
        declared = {cls.__file_name__: cls for cls in _SIX_ROOTS}
        assert set(declared) == {
            "agents.toml",
            "collectors.toml",
            "tools.toml",
            "model.toml",
            "storage.toml",
            "infra.toml",
        }
        for _fname, cls in declared.items():
            assert cls.__section_label__, f"{cls.__name__} 缺少 __section_label__"

    def test_model_root_has_three_layers(self):
        """model.toml 三层结构字段在生成 schema 中齐备"""
        schema = _generate(ModelRootConfig)
        field_names = {f["name"] for f in schema["fields"]}
        assert {"llm_providers", "llm_models", "llm_profiles"} <= field_names

    def test_llm_profile_fields_three_layer_contract(self):
        """LLMProfileConfig 字段为三层结构契约（provider/model 角色级字段已废除）"""
        from src.modules.config.model_schemas import LLMProfileConfig

        profile_schema = _generate(LLMProfileConfig)
        field_names = {f["name"] for f in profile_schema["fields"]}
        assert {
            "model_list",
            "selection_strategy",
            "hard_timeout_ms",
            "slow_threshold_ms",
            "temperature",
            "max_tokens",
        } <= field_names
        assert "provider" not in field_names
        assert "model" not in field_names


# ===========================================================================
# 4. readonly 透传
# ===========================================================================


class TestReadonlyPassthrough:
    def test_meta_version_marked_readonly(self):
        """FileMetaConfig.version 的 readonly 标记进入生成结果（T21 写接口拒绝的信号源）"""
        from src.modules.config.file_meta import FileMetaConfig

        schema = _generate(FileMetaConfig)
        version_field = next(f for f in schema["fields"] if f["name"] == "version")
        assert version_field.get("readonly") is True


# ===========================================================================
# 参数化冒烟：六根逐个生成不抛
# ===========================================================================


@pytest.mark.parametrize("cls", _SIX_ROOTS, ids=lambda c: c.__name__)
def test_root_schema_generation_smoke(cls):
    schema = _generate(cls)
    assert schema["className"] == cls.__name__
    assert isinstance(schema["fields"], list)
