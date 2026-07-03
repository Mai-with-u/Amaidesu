"""ConfigSchemaGenerator 测试套件

覆盖范围
--------
1. **Pydantic 类型映射**：bool→boolean、int→integer、float→number、str→string、
   Literal→select、list→array、dict→object、BaseModel→object
2. **约束提取**：ge→minValue、le→maxValue、gt→exclusiveMinValue、lt→exclusiveMaxValue、
   multiple_of→step、pattern→pattern、min_length→minLength、max_length→maxLength
3. **Optional/Union 拆解**：Union[T, None] 与 Optional[T] 等价
4. **嵌套 BaseModel** 自动递归展开
5. **json_schema_extra pass-through**：x-widget / x-icon / x-label / x-placeholder / sensitive / x-ui-type
6. **CoreConfig / ModelConfig 实际配置类的 schema 生成**（含 7 个 core 分组 + 4 个 model 分组）
7. **schema_registry 字段级契约**（与覆盖率门禁脚本对齐）

注：测试聚焦于 ``ConfigSchemaGenerator`` 的行为契约。
``schema_registry`` 的覆盖率由 ``scripts/check_schema_coverage.py`` 门禁脚本独立验证。
"""

from __future__ import annotations

from typing import List, Literal, Optional, Union

import pytest
from pydantic import BaseModel, ConfigDict, Field

from src.modules.config.core_schemas import (
    ContextConfig,
    CoreConfig,
    DashboardConfig,
    GeneralConfig,
    MaiCoreConfig,
    MetaConfig,
    PersonaConfig,
)
from src.modules.config.model_schemas import (
    FastLLMConfig,
    LLMConfig,
    LocalLLMConfig,
    ModelConfig,
    VLMConfig,
)
from src.modules.config.schema_generator import (
    ConfigSchemaGenerator,
    collect_all_fields,
    get_field_count,
)
from src.modules.config.schemas.logging import LoggingConfig
from src.modules.config.schema_registry import get_schema_registry


# ===========================================================================
# Test Models — 自定义模型用于驱动 unit 级别的覆盖
# ===========================================================================


class SimpleTypes(BaseModel):
    """覆盖所有基础类型的演示模型"""

    flag: bool = Field(default=False, description="布尔字段")
    count: int = Field(default=0, description="整数字段")
    ratio: float = Field(default=0.0, description="浮点字段")
    label: str = Field(default="", description="字符串字段")


class OptionalModel(BaseModel):
    """覆盖 Optional / Union 包装"""

    maybe_str: Optional[str] = Field(default=None, description="可选字符串")
    maybe_int: Union[int, None] = Field(default=None, description="可选整数")
    plain: int = Field(default=1, description="带默认值的整数")


class LiteralModel(BaseModel):
    """覆盖 Literal 类型（→ select）"""

    mode: Literal["fast", "slow", "auto"] = Field(default="auto", description="运行模式")
    fmt: Literal["jsonl", "text"] = Field(default="jsonl", description="输出格式")
    opt_mode: Optional[Literal["on", "off"]] = Field(default=None, description="可选开关")


class ConstrainedModel(BaseModel):
    """覆盖 Pydantic Field 约束"""

    big_int: int = Field(default=10, ge=1, le=100, description="1-100")
    positive: float = Field(default=1.0, gt=0.0, lt=10.0, description="> 0 < 10")
    step_val: int = Field(default=10, multiple_of=2, description="必须是 2 的倍数")
    coded: str = Field(default="abc", pattern=r"^[a-z]+$", description="只接受小写字母")
    short: str = Field(default="", min_length=2, max_length=8, description="2-8 字符")


class JsonExtraModel(BaseModel):
    """覆盖 json_schema_extra 的 pass-through"""

    api_key: str = Field(
        default="",
        description="API Key",
        json_schema_extra={"sensitive": True, "x-widget": "password"},
    )
    name: str = Field(
        default="foo",
        description="展示名",
        json_schema_extra={"x-label": "展示名", "x-icon": "user", "x-placeholder": "请输入名称"},
    )
    # 通过 x-ui-type 把 string 强制标记为 select（无 Literal）
    mode_via_extra: str = Field(
        default="on",
        description="通过 x-ui-type 标记为 select",
        json_schema_extra={"x-ui-type": "select", "x-options": ["on", "off", "auto"]},
    )


class JsonExtraNonBaseConfig(BaseModel):
    """使用 model_config 注入 json_schema_extra（Pydantic v2 推荐写法）"""

    model_config = ConfigDict(
        json_schema_extra={
            "x-category": "ui",
            "globalFlag": True,
        }
    )
    val: str = Field(default="x", description="示例")


class NestedModel(BaseModel):
    """覆盖嵌套 BaseModel"""

    name: str = Field(default="n", description="嵌套名")
    child: SimpleTypes = Field(default_factory=SimpleTypes, description="子结构")


class ListModel(BaseModel):
    """覆盖 list 类型"""

    tags: List[str] = Field(default_factory=list, description="字符串列表")
    nums: list[int] = Field(default_factory=lambda: [1, 2, 3], description="整数列表")


# ===========================================================================
# 类型映射
# ===========================================================================


class TestTypeMapping:
    """逐类型映射到 UI schema 类型字符串。"""

    def test_bool_to_boolean(self):
        schema = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        flag = next(f for f in schema["fields"] if f["name"] == "flag")
        assert flag["type"] == "boolean"

    def test_int_to_integer(self):
        schema = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        count = next(f for f in schema["fields"] if f["name"] == "count")
        assert count["type"] == "integer"

    def test_float_to_number(self):
        schema = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        ratio = next(f for f in schema["fields"] if f["name"] == "ratio")
        assert ratio["type"] == "number"

    def test_str_to_string(self):
        schema = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        label = next(f for f in schema["fields"] if f["name"] == "label")
        assert label["type"] == "string"

    def test_literal_to_select(self):
        schema = ConfigSchemaGenerator.generate_config_schema(LiteralModel)
        mode = next(f for f in schema["fields"] if f["name"] == "mode")
        assert mode["type"] == "select"

    def test_literal_options_present(self):
        schema = ConfigSchemaGenerator.generate_config_schema(LiteralModel)
        mode = next(f for f in schema["fields"] if f["name"] == "mode")
        assert set(mode["options"]) == {"fast", "slow", "auto"}

    def test_optional_literal_to_select(self):
        schema = ConfigSchemaGenerator.generate_config_schema(LiteralModel)
        opt_mode = next(f for f in schema["fields"] if f["name"] == "opt_mode")
        # Optional[Literal[...]] 也应该是 select
        assert opt_mode["type"] == "select"
        assert set(opt_mode["options"]) == {"on", "off"}

    def test_list_to_array(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ListModel)
        tags = next(f for f in schema["fields"] if f["name"] == "tags")
        assert tags["type"] == "array"
        # items.type 应为 string
        assert tags["items"]["type"] == "string"

    def test_basemodel_to_object(self):
        # 顶层 SimpleTypes 不是嵌套，单独测：构造一个包含 BaseModel 的字段
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        child = next(f for f in schema["fields"] if f["name"] == "child")
        assert child["type"] == "object"

    def test_x_ui_type_overrides_string_to_select(self):
        """json_schema_extra 中的 x-ui-type 可以覆盖默认类型映射（string→select）。"""
        schema = ConfigSchemaGenerator.generate_config_schema(JsonExtraModel)
        mode = next(f for f in schema["fields"] if f["name"] == "mode_via_extra")
        assert mode["type"] == "select"
        assert set(mode["options"]) == {"on", "off", "auto"}


# ===========================================================================
# Optional / Union 处理
# ===========================================================================


class TestOptionalHandling:
    """Optional[T] 与 Union[T, None] 应去包装后取内部类型。"""

    def test_optional_str_still_string(self):
        schema = ConfigSchemaGenerator.generate_config_schema(OptionalModel)
        maybe = next(f for f in schema["fields"] if f["name"] == "maybe_str")
        assert maybe["type"] == "string"

    def test_optional_int_still_integer(self):
        schema = ConfigSchemaGenerator.generate_config_schema(OptionalModel)
        maybe = next(f for f in schema["fields"] if f["name"] == "maybe_int")
        assert maybe["type"] == "integer"

    def test_optional_with_default_is_not_required(self):
        schema = ConfigSchemaGenerator.generate_config_schema(OptionalModel)
        maybe = next(f for f in schema["fields"] if f["name"] == "maybe_str")
        assert maybe["required"] is False

    def test_plain_field_has_default(self):
        schema = ConfigSchemaGenerator.generate_config_schema(OptionalModel)
        plain = next(f for f in schema["fields"] if f["name"] == "plain")
        assert plain["default"] == 1


# ===========================================================================
# 约束提取
# ===========================================================================


class TestConstraints:
    """Pydantic Field 约束 -> UI 字段。"""

    def test_ge_to_minvalue(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        big = next(f for f in schema["fields"] if f["name"] == "big_int")
        assert big["minValue"] == 1
        assert big["maxValue"] == 100

    def test_gt_to_exclusiveminvalue(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        pos = next(f for f in schema["fields"] if f["name"] == "positive")
        assert pos["exclusiveMinValue"] == 0.0
        assert pos["exclusiveMaxValue"] == 10.0

    def test_multiple_of_to_step(self):
        """multiple_of -> UI 中的 step (整数步长)。"""
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        step_field = next(f for f in schema["fields"] if f["name"] == "step_val")
        # 约束至少保留下来（通过 step / multipleOf 任一 key）
        assert "step" in step_field or "multipleOf" in step_field
        # 而且 step 应当是 2
        assert step_field.get("step") == 2 or step_field.get("multipleOf") == 2

    def test_pattern_retained(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        coded = next(f for f in schema["fields"] if f["name"] == "coded")
        assert coded["pattern"] == r"^[a-z]+$"

    def test_min_max_length(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        short = next(f for f in schema["fields"] if f["name"] == "short")
        assert short["minLength"] == 2
        assert short["maxLength"] == 8


# ===========================================================================
# json_schema_extra pass-through
# ===========================================================================


class TestJsonSchemaExtraPassThrough:
    """json_schema_extra 字典应原样合并到字段 schema。"""

    def test_sensitive_flag_passes_through(self):
        schema = ConfigSchemaGenerator.generate_config_schema(JsonExtraModel)
        key = next(f for f in schema["fields"] if f["name"] == "api_key")
        # API key 字段应标记为敏感
        assert key.get("sensitive") is True

    def test_x_widget_passes_through(self):
        schema = ConfigSchemaGenerator.generate_config_schema(JsonExtraModel)
        key = next(f for f in schema["fields"] if f["name"] == "api_key")
        assert key.get("x-widget") == "password"

    def test_x_label_passes_through(self):
        schema = ConfigSchemaGenerator.generate_config_schema(JsonExtraModel)
        name = next(f for f in schema["fields"] if f["name"] == "name")
        # json_schema_extra["x-label"] 应当原样出现在 schema 中
        assert name.get("x-label") == "展示名"

    def test_x_icon_and_placeholder(self):
        schema = ConfigSchemaGenerator.generate_config_schema(JsonExtraModel)
        name = next(f for f in schema["fields"] if f["name"] == "name")
        assert name.get("x-icon") == "user"
        assert name.get("x-placeholder") == "请输入名称"


# ===========================================================================
# 嵌套 BaseModel
# ===========================================================================


class TestNestedModels:
    """BaseModel 子类型应递归展开到 schema.nested 中。"""

    def test_nested_field_present(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        assert "nested" in schema
        assert "child" in schema["nested"]

    def test_nested_has_its_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        child_schema = schema["nested"]["child"]
        child_names = {f["name"] for f in child_schema["fields"]}
        assert {"flag", "count", "ratio", "label"}.issubset(child_names)

    def test_collect_all_fields_flattens(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        flat = collect_all_fields(schema)
        # 顶层字段
        flat_names = {f["name"] for f in flat}
        assert "name" in flat_names
        # 嵌套字段前缀
        prefixed = {f["key"] for f in flat}
        assert "child.flag" in prefixed
        assert "child.count" in prefixed
        assert "child.ratio" in prefixed
        assert "child.label" in prefixed

    def test_get_field_count_includes_nested(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        # 顶层 2 + 嵌套 4 = 6
        assert get_field_count(schema) == 6


# ===========================================================================
# 顶层 API：CoreConfig / ModelConfig
# ===========================================================================


class TestCoreConfigGeneration:
    """验证 CoreConfig 中所有 7 个 schema_registry 分组对应的配置类。"""

    def test_coreconfig_top_level_structure(self):
        schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
        assert schema["className"] == "CoreConfig"
        assert "fields" in schema
        assert "nested" in schema

    def test_coreconfig_contains_all_known_subgroups(self):
        schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
        nested_keys = set(schema["nested"].keys())
        for expected in [
            "meta",
            "general",
            "persona",
            "maicore",
            "context",
            "dashboard",
            "logging",
        ]:
            assert expected in nested_keys, f"CoreConfig missing nested subgroup: {expected}"

    def test_general_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(GeneralConfig)
        names = {f["name"] for f in schema["fields"]}
        assert "platform_id" in names
        platform = next(f for f in schema["fields"] if f["name"] == "platform_id")
        assert platform["type"] == "string"
        assert platform["default"] == "amaidesu"

    def test_persona_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(PersonaConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        # bot_name 为 string
        assert by_name["bot_name"]["type"] == "string"
        assert by_name["bot_name"]["default"] == "麦麦"
        # max_response_length / emotion_intensity 为 integer
        assert by_name["max_response_length"]["type"] == "integer"
        assert by_name["max_response_length"]["default"] == 50
        assert by_name["emotion_intensity"]["type"] == "integer"
        assert by_name["emotion_intensity"]["default"] == 7

    def test_maicore_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(MaiCoreConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["host"]["default"] == "127.0.0.1"
        assert by_name["port"]["type"] == "integer"
        assert by_name["port"]["default"] == 8000

    def test_context_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ContextConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["max_messages_per_session"]["type"] == "integer"
        assert by_name["max_sessions"]["type"] == "integer"
        assert by_name["session_timeout_seconds"]["type"] == "integer"
        assert by_name["enable_persistence"]["type"] == "boolean"

    def test_dashboard_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(DashboardConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["enabled"]["type"] == "boolean"
        assert by_name["host"]["default"] == "127.0.0.1"
        assert by_name["port"]["type"] == "integer"
        assert by_name["port"]["default"] == 60214
        # cors_origins 应为 array
        assert by_name["cors_origins"]["type"] == "array"

    def test_meta_config_present(self):
        schema = ConfigSchemaGenerator.generate_config_schema(MetaConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["version"]["default"] == "0.4.0"

    def test_logging_config_via_collect_all_fields(self):
        """通过 CoreConfig 间接生成 logging schema，验证 fields 铺平正确。"""
        schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
        flat = collect_all_fields(schema, prefix="core")
        # logging 字段
        logging_fields = {f["name"] for f in flat if f["key"].startswith("core.logging.")}
        assert "enabled" in logging_fields
        assert "format" in logging_fields
        assert "level" in logging_fields


class TestModelConfigGeneration:
    """验证 ModelConfig (llm, llm_fast, vlm, llm_local)。"""

    def test_modelconfig_contains_all_model_types(self):
        schema = ConfigSchemaGenerator.generate_config_schema(ModelConfig)
        nested_keys = set(schema["nested"].keys())
        for expected in ["llm", "llm_fast", "vlm", "llm_local"]:
            assert expected in nested_keys, f"ModelConfig missing: {expected}"

    def test_llm_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(LLMConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["client"]["type"] == "select"
        assert by_name["client"]["default"] == "openai"
        assert by_name["client"]["options"] == ["openai"]
        assert by_name["model"]["type"] == "string"
        assert by_name["model"]["default"] == "gpt-4"
        assert by_name["temperature"]["type"] == "number"
        assert by_name["temperature"]["default"] == 0.2
        assert by_name["max_tokens"]["type"] == "integer"
        assert by_name["api_key"]["default"] == ""

    def test_fast_llm_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(FastLLMConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["model"]["default"] == "gpt-3.5-turbo"

    def test_vlm_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(VLMConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["model"]["default"] == "gpt-4-vision-preview"
        assert by_name["temperature"]["default"] == 0.3

    def test_local_llm_config_fields(self):
        schema = ConfigSchemaGenerator.generate_config_schema(LocalLLMConfig)
        by_name = {f["name"]: f for f in schema["fields"]}
        assert by_name["model"]["default"] == "llama3"
        assert by_name["base_url"]["default"] == "http://localhost:11434/v1"
        assert by_name["api_key"]["default"] == "sk-dummy"


# ===========================================================================
# 顶层：7 个 core 分组 + 4 个 model 分组的端到端验证
# ===========================================================================


# registry group_key -> Pydantic 类
_GROUP_CLASS = {
    "general": GeneralConfig,
    "persona": PersonaConfig,
    "maicore": MaiCoreConfig,
    "context": ContextConfig,
    "dashboard": DashboardConfig,
    "logging": LoggingConfig,
    "llm": LLMConfig,
    "llm_fast": FastLLMConfig,
    "vlm": VLMConfig,
    "llm_local": LocalLLMConfig,
    "meta": MetaConfig,
}


class TestAllGroupsRoundTrip:
    """对每个 registry 分组对应的 Pydantic 类，验证 schema 结构完整可用。

    这与 ``scripts/check_schema_coverage.py`` 的覆盖率门禁等价 ——
    测试在 pytest 中运行，门禁脚本在 CI 中运行，二者互为保险。
    """

    @pytest.mark.parametrize(
        "group_key, py_cls",
        list(_GROUP_CLASS.items()),
        ids=list(_GROUP_CLASS.keys()),
    )
    def test_group_schema_has_valid_structure(self, group_key, py_cls):
        """生成的 schema 必须包含 className + fields，且 fields 列表非空（除 type 字段）。"""
        schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        assert schema["className"] == py_cls.__name__
        assert isinstance(schema.get("fields"), list)
        # 实际业务字段（不含 type 内部标识）
        business_fields = [f for f in schema["fields"] if f["name"] != "type"]
        assert len(business_fields) >= 1, f"{group_key} 没有任何业务字段"
        # 每条字段都必须有 name / type / required
        for f in business_fields:
            assert "name" in f
            assert "type" in f
            assert "required" in f
            assert f["type"] in {
                "string",
                "integer",
                "number",
                "boolean",
                "array",
                "object",
                "select",
            }

    @pytest.mark.parametrize(
        "group_key, py_cls",
        list(_GROUP_CLASS.items()),
        ids=list(_GROUP_CLASS.keys()),
    )
    def test_group_field_names_match_model_fields(self, group_key, py_cls):
        """generator 输出的字段名集合 == Pydantic model_fields 的字段名集合。"""
        schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        gen_names = {f["name"] for f in schema["fields"]}
        model_names = set(py_cls.model_fields.keys())
        assert gen_names == model_names, (
            f"[{group_key}] 字段名差异: "
            f"generator 多余={gen_names - model_names}, "
            f"generator 缺失={model_names - gen_names}"
        )


# ===========================================================================
# Edge cases & 错误处理
# ===========================================================================


class TestErrorHandling:
    """输入校验 / 异常路径。"""

    def test_non_basemodel_class_raises_type_error(self):
        class NotAModel:
            pass

        with pytest.raises(TypeError):
            ConfigSchemaGenerator.generate_config_schema(NotAModel)

    def test_generate_schema_alias(self):
        """generate_schema 应为 generate_config_schema 的别名（兼容 MaiBot 接口）。"""
        s1 = ConfigSchemaGenerator.generate_schema(SimpleTypes)
        s2 = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        # 应返回等价 schema
        assert s1["className"] == s2["className"]
        assert s1["fields"] == s2["fields"]

    def test_description_from_field(self):
        """description 应从 Field 的 description 中提取（而不是空）。"""
        schema = ConfigSchemaGenerator.generate_config_schema(ConstrainedModel)
        big = next(f for f in schema["fields"] if f["name"] == "big_int")
        assert big["description"] == "1-100"


class TestFlattenUtilities:
    """扁平工具函数。"""

    def test_collect_all_fields_no_prefix(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        flat = collect_all_fields(schema)
        # 顶层字段 key=field_name
        assert any(f["key"] == "name" for f in flat)
        # 嵌套字段 key=child.<name>
        assert any(f["key"] == "child.label" for f in flat)

    def test_collect_all_fields_with_prefix(self):
        schema = ConfigSchemaGenerator.generate_config_schema(NestedModel)
        flat = collect_all_fields(schema, prefix="root")
        # 全部带 root. 前缀
        assert all(f["key"].startswith("root.") for f in flat)
        assert any(f["key"] == "root.child.label" for f in flat)

    def test_get_field_count_simple(self):
        schema = ConfigSchemaGenerator.generate_config_schema(SimpleTypes)
        assert get_field_count(schema) == 4


class TestRegistrySmoke:
    """轻量级 smoke 测试：验证 registry 已初始化且分组齐全。

    完整覆盖率由 ``scripts/check_schema_coverage.py`` 验证；这里只做
    pytest 层级的 sanity check，确保 import 链路正确。
    """

    def test_registry_loads(self):
        registry = get_schema_registry()
        groups = registry.get_all_groups()
        assert len(groups) >= 7
        keys = {g.key for g in groups}
        assert "general" in keys
        assert "persona" in keys
        assert "llm" in keys

    def test_registry_field_lookup(self):
        registry = get_schema_registry()
        # spot check 几个核心字段
        f = registry.get_field("persona.bot_name")
        assert f is not None
        assert f.label == "VTuber 名字"
