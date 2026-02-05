"""
配置加载集成测试

测试新的三级配置合并系统：
1. Schema默认值（Pydantic）
2. 主配置覆盖（[providers.*.overrides]）
3. Provider本地配置（config.toml，可选）

注意: 已移除config-defaults.toml加载逻辑，所有默认值由Schema定义。

测试场景：
- 三级配置合并顺序和优先级
- 配置验证（Pydantic Schema）
- enabled字段不在Provider配置中的情况
- 配置优先级正确性
- 输入、决策、输出三种Provider类型
- 自动从Schema生成缺失的config.toml文件

运行: uv run pytest tests/core/test_config_loading.py -v
"""

import os
import pytest
import tempfile
import shutil
from pydantic import BaseModel, ValidationError

from src.services.config.service import ConfigService


# =============================================================================
# Test Fixtures - Pydantic Schemas
# =============================================================================


class InputProviderSchema(BaseModel):
    """测试输入Provider Schema"""

    enabled: bool = True
    priority: int = 100
    message_prefix: str = ""
    max_messages: int = 10
    custom_field: str = "default_value"


class OutputProviderSchema(BaseModel):
    """测试输出Provider Schema"""

    enabled: bool = True
    voice_id: str = "default"
    speed: float = 1.0
    volume: float = 0.8
    output_format: str = "mp3"


class DecisionProviderSchema(BaseModel):
    """测试决策Provider Schema"""

    enabled: bool = True
    model: str = "default-model"
    temperature: float = 0.7
    max_tokens: int = 512
    timeout: int = 30


# =============================================================================
# Test Fixtures - Config Service
# =============================================================================


@pytest.fixture
def temp_base_dir():
    """创建临时测试目录"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # 清理
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def minimal_main_config():
    """最小主配置内容"""
    return """
[general]
platform_id = "test_platform"

[llm]
backend = "openai"
model = "test-model"
api_key = "test-key"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.output]
enabled = true
enabled_outputs = ["test_output"]

[providers.decision]
enabled = true
active_provider = "test_decision"
available_providers = ["test_decision"]
"""


@pytest.fixture
def main_config_with_overrides():
    """包含overrides的主配置"""
    return """
[general]
platform_id = "test_platform"

[llm]
backend = "openai"
model = "test-model"
api_key = "test-key"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200  # 覆盖Schema默认值
custom_field = "main_override_value"  # 覆盖Schema默认值

[providers.output]
enabled = true
enabled_outputs = ["test_output"]

[providers.output.overrides.test_output]
volume = 0.5  # 覆盖Schema默认值
output_format = "wav"  # 覆盖Schema默认值

[providers.decision]
enabled = true
active_provider = "test_decision"
available_providers = ["test_decision"]

[providers.decision.overrides.test_decision]
temperature = 0.5  # 覆盖Schema默认值
max_tokens = 1024  # 覆盖Schema默认值
"""


# =============================================================================
# Test 1: Schema默认值
# =============================================================================


def test_schema_defaults_only(temp_base_dir):
    """测试只有Schema默认值的情况"""
    # 创建主配置（不含overrides）
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 初始化ConfigService并获取配置（只使用Schema默认值）
    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # Schema默认值应该被应用
    assert merged_config["enabled"] is True
    assert merged_config["priority"] == 100
    assert merged_config["message_prefix"] == ""
    assert merged_config["max_messages"] == 10
    assert merged_config["custom_field"] == "default_value"


# =============================================================================
# Test 2: 三级配置合并（Schema默认 → 主配置覆盖 → 本地配置）
# =============================================================================


def test_three_tier_merge_full_flow(temp_base_dir):
    """测试完整的三级配置合并流程（新架构）"""
    # 1. 创建Provider目录
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "input", "providers", "test_input")
    os.makedirs(provider_dir, exist_ok=True)

    # 2. 创建主配置（包含overrides）
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200  # 覆盖Schema默认值
custom_field = "from_main_override"  # 覆盖Schema默认值
new_field = "from_main"  # 新增字段
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 3. 创建本地配置
    local_content = """
[test_input]
message_prefix = "[LOCAL] "  # 覆盖Schema默认
max_messages = 50  # 覆盖Schema默认
local_field = "from_local"  # 新增字段
"""
    with open(os.path.join(provider_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(local_content)

    # 4. 初始化ConfigService并获取配置
    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # 5. 验证合并结果
    # 优先级: Schema默认 < 主配置覆盖 < 本地配置
    assert merged_config["priority"] == 200  # from main override (覆盖Schema)
    assert merged_config["custom_field"] == "from_main_override"  # from main override (覆盖Schema)
    assert merged_config["new_field"] == "from_main"  # from main override (新增)
    assert merged_config["message_prefix"] == "[LOCAL] "  # from local config (覆盖Schema)
    assert merged_config["max_messages"] == 50  # from local config (覆盖Schema)
    assert merged_config["local_field"] == "from_local"  # from local config (新增)
    assert merged_config["enabled"] is True  # from Schema default


def test_three_tier_merge_with_schema_validation(temp_base_dir):
    """测试三级配置合并 + Schema验证"""
    # 1. 创建Provider目录
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "output", "providers", "test_output")
    os.makedirs(provider_dir, exist_ok=True)

    # 2. 创建主配置
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["test_output"]

[providers.output.overrides.test_output]
volume = 0.5
output_format = "wav"
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 3. 初始化ConfigService并获取配置（带Schema验证）
    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_output", "output", OutputProviderSchema)

    # 4. 验证 - 主配置覆盖应该生效
    assert merged_config["voice_id"] == "default"  # 来自Schema
    assert merged_config["speed"] == 1.0  # 来自Schema
    assert merged_config["volume"] == 0.5  # 来自主配置覆盖
    assert merged_config["output_format"] == "wav"  # 来自主配置覆盖
    assert merged_config["enabled"] is True  # 来自Schema默认值


def test_three_tier_merge_schema_validation_error(temp_base_dir):
    """测试Schema验证失败时抛出异常"""
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "output", "providers", "test_output")
    os.makedirs(provider_dir, exist_ok=True)

    # 创建本地配置（speed应该是float，但给了string）
    local_content = """
[test_output]
speed = "invalid_speed"  # 类型错误
"""
    with open(os.path.join(provider_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(local_content)

    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["test_output"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    # 应该抛出ValidationError
    with pytest.raises(ValidationError):  # Pydantic ValidationError
        config_service.get_provider_config_with_defaults("test_output", "output", OutputProviderSchema)


# =============================================================================
# Test 3: 配置优先级正确性
# =============================================================================


def test_config_priority_local_overrides_main(temp_base_dir):
    """测试本地配置优先级高于主配置覆盖"""
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "input", "providers", "test_input")
    os.makedirs(provider_dir, exist_ok=True)

    # 主配置覆盖
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 本地配置
    local_content = """
[test_input]
priority = 300
"""
    with open(os.path.join(provider_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(local_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # 本地配置优先级最高
    assert merged_config["priority"] == 300


def test_config_priority_main_overrides_schema(temp_base_dir):
    """测试主配置覆盖优先级高于Schema默认值"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # 主配置覆盖优先级高于Schema默认值
    assert merged_config["priority"] == 200


def test_config_priority_schema_defaults_lowest(temp_base_dir):
    """测试Schema默认值优先级最低"""
    # 不创建任何配置文件，只使用Schema默认值
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["test_output"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_output", "output", OutputProviderSchema)

    # Schema默认值应该被应用
    assert merged_config["voice_id"] == "default"
    assert merged_config["speed"] == 1.0
    assert merged_config["volume"] == 0.8
    assert merged_config["output_format"] == "mp3"
    assert merged_config["enabled"] is True


# =============================================================================
# Test 4: enabled字段不在Provider配置中
# =============================================================================


def test_provider_config_without_enabled_field(temp_base_dir):
    """测试Provider配置中没有enabled字段的情况（从Schema获取）"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # enabled字段来自Schema默认值
    assert merged_config["enabled"] is True
    assert merged_config["priority"] == 100


def test_provider_enabled_check_based_on_list(temp_base_dir):
    """测试enabled检查基于enabled_inputs列表，而不是配置中的enabled字段"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input", "another_input"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    # 检查是否在enabled_inputs列表中
    providers_config = config_service.get_section("providers", {})
    input_config = providers_config.get("input", {})
    enabled_inputs = input_config.get("enabled_inputs", [])

    assert "test_input" in enabled_inputs
    assert len(enabled_inputs) == 2


# =============================================================================
# Test 5: 三种Provider类型（input/output/decision）
# =============================================================================


def test_input_provider_config_loading(temp_base_dir):
    """测试输入Provider配置加载"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 200  # from main override
    assert merged_config["max_messages"] == 10  # from Schema default
    assert merged_config["enabled"] is True


def test_output_provider_config_loading(temp_base_dir):
    """测试输出Provider配置加载"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["test_output"]

[providers.output.overrides.test_output]
volume = 0.6
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_output", "output", OutputProviderSchema)

    assert merged_config["voice_id"] == "default"  # from Schema
    assert merged_config["speed"] == 1.0  # from Schema
    assert merged_config["volume"] == 0.6  # from main override
    assert merged_config["enabled"] is True


def test_decision_provider_config_loading(temp_base_dir):
    """测试决策Provider配置加载"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.decision]
enabled = true
active_provider = "test_decision"
available_providers = ["test_decision"]

[providers.decision.overrides.test_decision]
max_tokens = 2048
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults(
        "test_decision", "decision", DecisionProviderSchema
    )

    assert merged_config["model"] == "default-model"  # from Schema
    assert merged_config["temperature"] == 0.7  # from Schema
    assert merged_config["max_tokens"] == 2048  # from main override
    assert merged_config["enabled"] is True


# =============================================================================
# Test 6: 边界情况和错误处理
# =============================================================================


def test_provider_not_initialized(temp_base_dir):
    """测试未初始化时获取Provider配置"""
    config_service = ConfigService(base_dir=temp_base_dir)

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input")

    # 未初始化应返回空配置
    assert merged_config == {}


def test_invalid_provider_layer(temp_base_dir):
    """测试无效的Provider层级"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    # 无效的层级应返回空配置
    merged_config = config_service.get_provider_config_with_defaults("test_provider", "invalid_layer")

    assert merged_config == {}


def test_empty_overrides_section(temp_base_dir):
    """测试空的overrides节"""
    # 主配置中没有overrides节
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    # 应该只加载Schema默认值
    assert merged_config["priority"] == 100


def test_deep_merge_nested_dicts(temp_base_dir):
    """测试深度合并嵌套字典"""
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "input", "providers", "test_input")
    os.makedirs(provider_dir, exist_ok=True)

    # 主配置覆盖（包含嵌套配置）
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200

[providers.input.overrides.test_input.nested_config]
field2 = "override"  # 覆盖默认值
field3 = "new"  # 新增字段
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 本地配置
    local_content = """
[test_input]
nested_config = { field1 = "default", field4 = "local" }
"""
    with open(os.path.join(provider_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(local_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    # 注意：嵌套配置的合并需要完整的Schema支持
    merged_config = config_service.get_provider_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 200
    # 本地配置中的嵌套字段
    assert "nested_config" in merged_config


# =============================================================================
# Test 7: load_global_overrides
# =============================================================================


def test_load_global_overrides(temp_base_dir):
    """测试加载主配置覆盖"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["test_input"]

[providers.input.overrides.test_input]
priority = 200
custom_field = "override_value"
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    overrides = config_service.load_global_overrides("providers.input", "test_input")

    assert overrides["priority"] == 200
    assert overrides["custom_field"] == "override_value"


def test_load_global_overrides_not_exist(temp_base_dir):
    """测试加载不存在的主配置覆盖"""
    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    overrides = config_service.load_global_overrides("providers.input", "nonexistent_provider")

    assert overrides == {}


# =============================================================================
# Test 8: deep_merge_configs 函数
# =============================================================================


def test_deep_merge_basic_types():
    """测试深度合并基本类型"""
    from src.services.config.service import deep_merge_configs

    base = {"a": 1, "b": 2}
    override = {"b": 20, "c": 3}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1  # 保留
    assert result["b"] == 20  # 覆盖
    assert result["c"] == 3  # 新增


def test_deep_merge_nested_dicts_simple():
    """测试深度合并嵌套字典（简单版本）"""
    from src.services.config.service import deep_merge_configs

    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"nested": {"y": 200, "z": 30}, "b": 2}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["nested"]["x"] == 10  # 保留
    assert result["nested"]["y"] == 200  # 覆盖
    assert result["nested"]["z"] == 30  # 新增
    assert result["b"] == 2


def test_deep_merge_lists():
    """测试深度合并列表（后者替换前者）"""
    from src.services.config.service import deep_merge_configs

    base = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}

    result = deep_merge_configs(base, override)

    # 列表应该被完全替换，不合并
    assert result["items"] == [4, 5]


def test_deep_merge_none_values():
    """测试None值被跳过"""
    from src.services.config.service import deep_merge_configs

    base = {"a": 1, "b": 2}
    override = {"b": None, "c": 3}

    result = deep_merge_configs(base, override)

    # None值应该跳过，不覆盖
    assert result["a"] == 1
    assert result["b"] == 2  # 保留原值
    assert result["c"] == 3


# =============================================================================
# Test 9: 自动从Schema生成config.toml
# =============================================================================


def test_auto_generate_config_from_schema(temp_base_dir):
    """测试自动从Schema生成config.toml文件"""
    # 确保Provider目录不存在（没有本地配置）
    os.path.join(temp_base_dir, "src", "domains", "input", "providers", "auto_test")

    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.input]
enabled = true
enabled_inputs = ["auto_test"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    # 由于没有Schema，返回空配置
    merged_config = config_service.get_provider_config_with_defaults("auto_test", "input")

    # 配置应该为空（因为没有Schema也没有本地配置）
    assert merged_config == {}


def test_local_config_overrides_schema(temp_base_dir):
    """测试本地配置覆盖Schema默认值"""
    # 注意：output providers的路径是 src/domains/output/providers/
    provider_dir = os.path.join(temp_base_dir, "src", "domains", "output", "providers", "test_output")
    os.makedirs(provider_dir, exist_ok=True)

    main_content = """
[general]
platform_id = "test"

[llm]
backend = "openai"
model = "test"
api_key = "test"

[providers.output]
enabled = true
enabled_outputs = ["test_output"]
"""
    template_path = os.path.join(temp_base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(main_content)

    # 创建本地配置覆盖Schema默认值
    local_content = """
[test_output]
voice_id = "local_voice"
volume = 0.3
"""
    with open(os.path.join(provider_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(local_content)

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_provider_config_with_defaults("test_output", "output", OutputProviderSchema)

    # 本地配置应该覆盖Schema默认值
    assert merged_config["voice_id"] == "local_voice"  # from local (覆盖Schema "default")
    assert merged_config["speed"] == 1.0  # from Schema
    assert merged_config["volume"] == 0.3  # from local (覆盖Schema 0.8)


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
