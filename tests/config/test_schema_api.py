"""配置 Schema 生成器（ConfigSchemaGenerator）对六个根 Schema 的输出契约：结构、字段形状、自描述协议、readonly 与控件标记透传

消费方：dashboard 分组构建（写接口拒绝与展示共用同一信号）。
"""

from __future__ import annotations

import json

import pytest

from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.model_schemas import LLMProfileConfig, ModelRootConfig
from src.modules.config.schema_generator import ConfigSchemaGenerator
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.tools_schemas import ToolsRootConfig

ALL_ROOTS = [
    AgentsRootConfig,
    CollectorsRootConfig,
    ToolsRootConfig,
    ModelRootConfig,
    StorageRootConfig,
    InfraRootConfig,
]
UI_TYPES = {"string", "integer", "number", "boolean", "array", "object", "select"}


def _generate(cls) -> dict:
    return ConfigSchemaGenerator.generate_config_schema(cls)


def _field(schema: dict, name: str) -> dict:
    return next(f for f in schema["fields"] if f["name"] == name)


@pytest.mark.parametrize("cls", ALL_ROOTS, ids=lambda c: c.__name__)
def test_every_root_generates_fields_with_required_shape(cls) -> None:
    schema = _generate(cls)
    assert schema["className"] == cls.__name__ and schema["fields"]
    for field in schema["fields"]:
        assert {"name", "type", "label", "description"} <= set(field), f"{cls.__name__} 字段形状缺项: {field}"
        assert field["type"] in UI_TYPES, f"{cls.__name__}.{field['name']} 非法 UI 类型: {field['type']}"
        assert isinstance(field["label"], dict) and "zh_CN" in field["label"]
    assert cls.__section_label__


def test_self_described_file_names_cover_the_six_files() -> None:
    assert {cls.__file_name__ for cls in ALL_ROOTS} == {
        "agents.toml",
        "collectors.toml",
        "tools.toml",
        "model.toml",
        "storage.toml",
        "infra.toml",
    }


def test_model_root_is_three_layers_json_serializable_and_drilled_down() -> None:
    schema = _generate(ModelRootConfig)
    assert (
        json.loads(json.dumps(schema, ensure_ascii=False, default=str))["className"] == "ModelRootConfig"
    )  # 供 HTTP API 序列化
    assert {"llm_providers", "llm_models", "llm_profiles"} <= {f["name"] for f in schema["fields"]}
    assert {"llm_providers", "llm_profiles"} <= set(schema["nested"])  # 列表元素与封闭集合容器均展开为具型子模型

    profile_fields = {f["name"] for f in _generate(LLMProfileConfig)["fields"]}
    assert {"model_list", "selection_strategy", "slow_threshold_ms", "temperature"} <= profile_fields
    assert (
        not {"history_token_budget", "provider", "model"} & profile_fields
    )  # 三层结构契约：角色级 provider/model 已废除


def test_readonly_marker_reaches_generated_fields() -> None:
    from src.modules.avatar.lipsync import LipSyncConfig
    from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
    from src.modules.config.file_meta import FileMetaConfig

    assert _field(_generate(FileMetaConfig), "version").get("readonly") is True  # 写接口拒绝的信号源
    for cls in (LipSyncConfig, VRChatProvider.ConfigSchema):  # 常量型字段在 WebUI 只读呈现
        assert _field(_generate(cls), "type").get("readonly") is True, f"{cls.__name__}.type 缺 readonly 标记"


def test_widget_markers_options_precision_and_bounds_reach_generated_fields() -> None:
    from src.agents.text_adv.config import TextAdvConfig
    from src.modules.avatar.lipsync import LipSyncConfig
    from src.modules.config.avatar_schemas import PLATFORM_NAMES, AvatarRootConfig
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.component_catalog import ensure_component_registry
    from src.modules.config.registry import COLLECTOR_SCHEMAS

    ensure_component_registry()
    assert _field(_generate(CollectorsRootConfig), "enabled").get("options") == sorted(
        COLLECTOR_SCHEMAS
    )  # 候选名单来自注册表单点
    avatar_platform = _generate(AvatarRootConfig)["nested"]["platform"]
    assert _field(avatar_platform, "enabled").get("options") == list(PLATFORM_NAMES)  # 封闭候选池

    region = _field(_generate(TextAdvConfig), "region")  # 定长四元组，元素为整数
    assert (region.get("x-ui-widget"), region.get("x-ui-tuple-length"), region.get("items")) == (
        "fixed-tuple",
        4,
        {"type": "integer"},
    )
    assert (
        _field(_generate(LipSyncConfig), "min_mouth_delta").get("x-ui-precision") == 3
    )  # 低于两位小数分辨率的字段声明精度

    dashboard = {f["name"]: f for f in _generate(DashboardConfig)["fields"]}
    for name in ("port", "vite_dev_port"):
        assert (dashboard[name].get("minValue"), dashboard[name].get("maxValue")) == (1, 65535)


def test_tts_provider_options_injected_from_assembly() -> None:
    """TTS 引擎候选 = tts 装配表键集（读侧单点注入，定义处不手抄）。"""
    from src.modules.config.infra_schemas import InfraRootConfig
    from src.modules.tts.assembly import TTS_PROVIDER_NAMES

    tts_schema = ConfigSchemaGenerator.generate_config_schema(InfraRootConfig)["nested"]["tts"]
    provider = next(f for f in tts_schema["fields"] if f["name"] == "provider")
    assert provider.get("options") == list(TTS_PROVIDER_NAMES)
    assert set(provider["options"]) == {"edge_tts", "gptsovits", "voicebox", "omni_tts"}
