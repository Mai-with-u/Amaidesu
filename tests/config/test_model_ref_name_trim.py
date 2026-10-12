"""模型配置引用身份字段的空白规范化测试

provider 名 / 模型名 / api_provider / profile.model_list 是相互引用的
身份字段，经 WebUI 手输混入首尾空格后，引用校验会报出"目标名在可用
列表里却判未找到"的自相矛盾错误；Schema 层统一剥离首尾空白消除该类
失败，同批新增 provider + model 的写路径因此不再被空白差击穿。
"""

from typing import Any

import pytest
from pydantic import ValidationError

from src.modules.config.model_schemas import LLMModelConfig, LLMProfileConfig, LLMProviderConfig
from src.modules.config.multi_file_loader import validate_config_updates


def _make_tmp_config_dir(tmp_path: Any) -> Any:
    """生成一份默认七文件配置目录，返回其路径"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    return tmp_path


class TestRefNameStrip:
    def test_provider_name_stripped(self) -> None:
        provider = LLMProviderConfig(name="  dashscope  ")
        assert provider.name == "dashscope"

    def test_model_ref_fields_stripped(self) -> None:
        model = LLMModelConfig(name=" qwen-flash ", api_provider=" p1 ")
        assert model.name == "qwen-flash"
        assert model.api_provider == "p1"

    def test_model_list_items_stripped(self) -> None:
        profile = LLMProfileConfig(model_list=[" m1 ", "m2\t"])
        assert profile.model_list == ["m1", "m2"]

    def test_non_string_passthrough_rejected(self) -> None:
        """非字符串输入原样穿透规范化，仍由类型校验拒绝（规范化不改判类型）"""
        with pytest.raises(ValidationError):
            LLMModelConfig(api_provider=123)

    def test_unknown_provider_still_rejected(self) -> None:
        """规范化不弱化校验：引用不存在的 provider 仍在根模型校验硬错"""
        from src.modules.config.model_schemas import ModelRootConfig

        with pytest.raises(ValidationError, match="未在 llm_providers 中找到"):
            ModelRootConfig(
                llm_providers=[{"name": "p1", "client_type": "openai", "base_url": "https://api.test/v1"}],
                llm_models=[{"name": "m1", "model_identifier": "id-1", "api_provider": "nowhere"}],
            )

    def test_same_batch_provider_and_model_with_spaces(self, tmp_path: Any) -> None:
        """回归：同批新增 provider 与引用它的 model，任一侧名字带首尾空格均可落盘。

        此前该场景在写路径预校验即被拒（报错里"可用列表含该名却判未找到"），
        用户表现为"无法同时添加提供商和模型"。
        """
        config_dir = _make_tmp_config_dir(tmp_path)
        updates = {
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
        }
        validate_config_updates(config_dir, "model.toml", updates)
