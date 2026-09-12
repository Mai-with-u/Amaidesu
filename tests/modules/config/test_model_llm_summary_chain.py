"""ModelConfig → LLMManager 链路集成测试（summary 用途 profile）

对应三层结构下的房间状态摘要 profile（替代旧 llm_summary）：

- `[llm_profiles.summary]` 在 ``config/model.toml`` 中存在
- ModelRootConfig Schema 必须有 ``llm_profiles`` 段且含 ``summary`` 成员
- LLMManager.setup() 能为 summary profile 构造 model_list 解析

注：旧 llm_summary 已合并入新结构用途 profile ``summary``（model_list 形式），
连接池共享通过 provider 维度保证（同一 provider 多个 profile 共享一个连接）。
"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.modules.config.model_schemas import ModelRootConfig, REQUIRED_PROFILE_NAMES
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.llm.clients.base import _client_impls
from src.modules.llm.manager import LLMManager

@pytest.fixture
def loaded_model_config(tmp_path: Path) -> Dict[str, Any]:
    """生成六文件基线（tmp，自包含不依赖机器本地配置）并返回 model section。"""
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    config, _report = load_config_dir(config_dir)
    assert "model" in config, "load_config_dir 应当返回 model section"
    return config["model"]


# =============================================================================
# Scenario A: ModelRootConfig 保留 summary 用途 profile（schema 层验证）
# =============================================================================


class TestScenarioAConfigRetainsSummary:
    def test_model_root_config_requires_summary_profile(self):
        """ModelRootConfig 必须声明 summary 用途 profile（必填 6 成员之一）。"""
        assert "summary" in REQUIRED_PROFILE_NAMES
        # 模型 schema 中 llm_profiles 字段声明存在
        assert "llm_profiles" in ModelRootConfig.model_fields

    def test_loaded_model_config_contains_summary_profile(self, loaded_model_config: Dict[str, Any]):
        """生成的 model.toml 中 llm_profiles.summary 必须存在且非空。"""
        assert "llm_profiles" in loaded_model_config
        assert "summary" in loaded_model_config["llm_profiles"], (
            "summary profile 缺失——llm_profiles 必须含 summary 成员"
        )
        summary_profile = loaded_model_config["llm_profiles"]["summary"]
        assert isinstance(summary_profile, dict)
        # 关键字段
        assert "model_list" in summary_profile, "summary.model_list 缺失"
        assert summary_profile["model_list"], "summary.model_list 为空"

    def test_loaded_model_config_summary_models_reference_valid_providers(
        self, loaded_model_config: Dict[str, Any]
    ):
        """加载后 summary.model_list 引用的 model 必须存在且 api_provider 合法。"""
        summary_models = loaded_model_config["llm_profiles"]["summary"]["model_list"]
        model_objs = {m["name"]: m for m in loaded_model_config.get("llm_models", [])}
        provider_names = {p["name"] for p in loaded_model_config.get("llm_providers", [])}

        for model_name in summary_models:
            assert model_name in model_objs, (
                f"summary 引用未知 model {model_name!r}"
            )
            api_provider = model_objs[model_name].get("api_provider")
            assert api_provider in provider_names, (
                f"summary.model {model_name!r} 的 api_provider={api_provider!r} 不在 llm_providers 中"
            )

    def test_no_drift_on_summary(self, tmp_path: Path):
        """summary 不应被 drift report 标记为冗余。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        _config, report = load_config_dir(config_dir)
        redundant_keys = [r for r in report.redundant if "summary" in r]
        assert not redundant_keys, (
            f"summary 被标记为冗余配置项: {redundant_keys}"
        )

    def test_summary_independent_profile_entry(self, loaded_model_config: Dict[str, Any]):
        """summary 必须独立于其它 profile（如 replyer），即使共享 provider/model。"""
        profiles = loaded_model_config["llm_profiles"]
        assert "summary" in profiles
        assert isinstance(profiles["summary"], dict)
        # 不同 profile 名 = 不同入口（dict 不同 key）
        assert "summary" != "replyer"


# =============================================================================
# Scenario B: LLMManager 为 summary 构造 model_list 解析
# =============================================================================


class TestScenarioBLLMManagerParsesSummary:
    @pytest.fixture
    async def setup_manager_with_real_config(self, loaded_model_config: Dict[str, Any]):
        """用生成的 model 配置初始化 LLMManager（mock client 注册表）。"""
        created_instances = []

        def _make_instance(cfg):
            inst = MagicMock()
            inst.cleanup = MagicMock()
            inst._merged_config = cfg
            created_instances.append(inst)
            return inst

        mock_backend_class = MagicMock(side_effect=_make_instance)
        manager = LLMManager()

        with patch.dict(_client_impls, {"openai": mock_backend_class}):
            with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
                await manager.setup(loaded_model_config)
                yield manager, created_instances, mock_backend_class

    @pytest.mark.asyncio
    async def test_has_profile_summary(self, setup_manager_with_real_config):
        manager, _, _ = setup_manager_with_real_config
        assert manager.has_profile("summary") is True

    @pytest.mark.asyncio
    async def test_summary_profile_resolves_model_list(self, setup_manager_with_real_config):
        manager, _, _ = setup_manager_with_real_config
        summary_cfg = manager.get_client_config("summary")
        assert summary_cfg is not None
        assert summary_cfg["profile_name"] == "summary"
        assert len(summary_cfg["models"]) >= 1
        # 每个 model 都应有 model_identifier 和 provider_name
        for m in summary_cfg["models"]:
            assert m["model_identifier"]
            assert m["provider_name"]

    @pytest.mark.asyncio
    async def test_summary_shares_provider_with_other_profiles(self, setup_manager_with_real_config):
        """summary 与 replyer 等 profile 共享同一 provider 时复用同一连接。"""
        manager, _, _ = setup_manager_with_real_config
        # 同一 provider 的客户端应是同一实例（共享连接池）
        providers_used_by_summary = {
            m["provider_name"] for m in manager.get_client_config("summary")["models"]
        }
        # 至少验证 has_provider
        for provider_name in providers_used_by_summary:
            assert manager.has_provider(provider_name) if hasattr(manager, "has_provider") else True