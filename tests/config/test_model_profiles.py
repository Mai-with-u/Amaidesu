"""model.toml 的 profile 与引用身份字段：用途集合封闭、引用名空白规范化、建造用途沿用游戏模型"""

import pytest
from pydantic import ValidationError

from src.modules.config.errors import ConfigValidationError
from src.modules.config.model_schemas import (
    LLMModelConfig,
    LLMProfileConfig,
    LLMProfilesConfig,
    LLMProviderConfig,
    ModelRootConfig,
)
from src.modules.config.multi_file_loader import (
    _ROOT_SCHEMAS,
    _validate_file,
    generate_default_configs,
    validate_config_updates,
)


def _base_model_raw() -> dict:
    """能通过引用校验的最小 model.toml 原始数据。"""
    return {
        "llm_providers": [{"name": "p1", "client_type": "openai", "base_url": "https://api.test/v1", "api_key": "k"}],
        "llm_models": [{"name": "m1", "model_identifier": "id-1", "api_provider": "p1"}],
        "llm_profiles": {
            name: {"model_list": ["m1"]}
            for name in ("planner", "replyer", "summary", "minecraft", "vision", "simulator")
        },
    }


def test_profile_set_is_closed_and_unknown_keys_fail_with_the_key_name() -> None:
    assert set(LLMProfilesConfig().model_dump()) == {  # 缺省种子含独立建造用途，设计预算不再影响游戏决策
        "planner",
        "replyer",
        "summary",
        "minecraft",
        "minecraft_builder",
        "vision",
        "simulator",
        "tts",
    }
    assert _ROOT_SCHEMAS["model.toml"] is ModelRootConfig  # 封闭集合校验挂在 model.toml 加载路径上
    instance, _ = _validate_file("model.toml", _base_model_raw())
    assert set(instance.llm_profiles.model_dump()) == set(LLMProfilesConfig.model_fields)  # tts 缺省沿用决策模型引用

    raw = _base_model_raw()
    raw["llm_profiles"]["totally_unknown"] = {"model_list": ["m1"]}
    with pytest.raises(ValidationError, match="totally_unknown"):  # extra=forbid
        ModelRootConfig(**raw)
    with pytest.raises(ConfigValidationError, match="totally_unknown"):  # 加载期硬错并指出未知键
        _validate_file("model.toml", raw)


def test_builder_profile_follows_the_minecraft_models_unless_chosen_explicitly() -> None:
    inherited = LLMProfilesConfig(minecraft={"model_list": ["custom-game"], "temperature": 0.1})
    assert inherited.minecraft_builder.model_list == ["custom-game"]  # 用户没有 default 模型时仍引用已有游戏模型
    assert inherited.minecraft_builder.temperature != inherited.minecraft.temperature

    explicit = LLMProfilesConfig(
        minecraft={"model_list": ["game"]}, minecraft_builder={"model_list": ["designer"], "temperature": 0.9}
    )
    assert (explicit.minecraft_builder.model_list, explicit.minecraft_builder.temperature) == (["designer"], 0.9)


def test_reference_identity_fields_are_stripped_but_typing_and_references_still_validate(tmp_path) -> None:
    """provider 名/模型名/api_provider/model_list 相互引用；WebUI 手输混入首尾空格会让校验报"在可用列表里却判未找到"。"""
    assert LLMProviderConfig(name="  dashscope  ").name == "dashscope"
    model = LLMModelConfig(name=" qwen-flash ", api_provider=" p1 ")
    assert (model.name, model.api_provider) == ("qwen-flash", "p1")
    assert LLMProfileConfig(model_list=[" m1 ", "m2\t"]).model_list == ["m1", "m2"]
    with pytest.raises(ValidationError):  # 非字符串原样穿透，仍由类型校验拒绝
        LLMModelConfig(api_provider=123)
    with pytest.raises(ValidationError, match="未在 llm_providers 中找到"):  # 规范化不弱化引用校验
        ModelRootConfig(
            llm_providers=[{"name": "p1", "client_type": "openai", "base_url": "https://api.test/v1"}],
            llm_models=[{"name": "m1", "model_identifier": "id-1", "api_provider": "nowhere"}],
        )

    generate_default_configs(tmp_path)  # 回归：同批新增 provider 与引用它的 model，任一侧带首尾空格均可落盘
    validate_config_updates(
        tmp_path,
        "model.toml",
        {
            "llm_providers": [
                {"name": "deepseek", "client_type": "openai", "base_url": "https://api.test/v1", "api_key": "k"},
                {"name": "dashscope ", "client_type": "openai", "base_url": "https://example/v1", "api_key": "k2"},
            ],
            "llm_models": [
                {"name": "default", "model_identifier": "id-1", "api_provider": "deepseek"},
                {
                    "name": "qwen3.8-omni-flash",
                    "model_identifier": "qwen3.8-omni-flash",
                    "api_provider": " dashscope",
                    "visual": True,
                },
            ],
        },
    )
