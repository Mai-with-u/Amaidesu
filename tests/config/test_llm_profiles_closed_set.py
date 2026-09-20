"""``[llm_profiles]`` 封闭集合测试

用途 profile 集合由 ``LLMProfilesConfig`` 的显式字段封闭：未知键在配置
加载期硬错（并指出未知键）；"缺"由字段缺省种子保证，无需显式必填清单。
"""

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.model_schemas import LLMProfilesConfig, ModelRootConfig
from src.modules.config.multi_file_loader import _ROOT_SCHEMAS, _validate_file


def _base_model_raw() -> dict:
    """构造能通过引用校验的最小 model.toml 原始数据"""
    return {
        "llm_providers": [{"name": "p1", "client_type": "openai", "base_url": "https://api.test/v1", "api_key": "k"}],
        "llm_models": [{"name": "m1", "model_identifier": "id-1", "api_provider": "p1"}],
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
            "replyer": {"model_list": ["m1"]},
            "summary": {"model_list": ["m1"]},
            "minecraft": {"model_list": ["m1"]},
            "vision": {"model_list": ["m1"]},
            "simulator": {"model_list": ["m1"]},
        },
    }


class TestClosedSet:
    def test_default_seeds_all_profiles(self) -> None:
        """缺省种子包含独立建造用途，设计预算不再影响游戏决策。"""
        profiles = LLMProfilesConfig()
        assert set(profiles.model_dump().keys()) == {
            "planner",
            "replyer",
            "summary",
            "minecraft",
            "minecraft_builder",
            "vision",
            "simulator",
        }

    def test_unknown_profile_rejected_on_direct_construction(self):
        """直接构造：未知用途键被 extra="forbid" 拒绝且错误中含键名"""
        raw = _base_model_raw()
        raw["llm_profiles"]["totally_unknown"] = {"model_list": ["m1"]}
        with pytest.raises(Exception, match="totally_unknown"):
            ModelRootConfig(**raw)

    def test_unknown_profile_hard_error_on_load(self):
        """加载期：未知用途 profile 硬错（ConfigValidationError）并指出未知键"""
        raw = _base_model_raw()
        raw["llm_profiles"]["helper_extra"] = {"model_list": ["m1"]}
        with pytest.raises(ConfigValidationError, match="helper_extra"):
            _validate_file("model.toml", raw)

    def test_validate_file_registered_for_model(self):
        """封闭集合校验挂在 model.toml 的加载路径上"""
        assert _ROOT_SCHEMAS["model.toml"] is ModelRootConfig

    def test_six_profiles_pass_load(self):
        """六成员齐全 + 全部引用已知模型 → 加载通过"""
        instance, _report = _validate_file("model.toml", _base_model_raw())
        assert set(instance.llm_profiles.model_dump().keys()) == set(LLMProfilesConfig.model_fields)
