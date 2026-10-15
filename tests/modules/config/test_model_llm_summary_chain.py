"""ModelConfig → LLMManager 链路（summary 用途 profile）：Schema 声明、生成配置的引用合法性、LLMManager 解析与连接共享"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.modules.config.model_schemas import LLMProfilesConfig, ModelRootConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager


@pytest.fixture
def loaded(tmp_path: Path) -> tuple[Dict[str, Any], Any]:
    """tmp 下生成七文件基线（自包含，不依赖机器本地配置），返回 (model section, drift report)。"""
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    config, report = load_config_dir(config_dir)
    return config["model"], report


def test_summary_profile_is_declared_and_its_models_reference_valid_providers(loaded) -> None:
    model_config, report = loaded
    assert "summary" in LLMProfilesConfig.model_fields and "llm_profiles" in ModelRootConfig.model_fields
    assert not [r for r in report.redundant if "summary" in r]  # summary 不应被漂移报告标记为冗余

    summary = model_config["llm_profiles"]["summary"]
    assert summary["model_list"], "summary.model_list 为空"
    models = {m["name"]: m for m in model_config["llm_models"]}
    providers = {p["name"] for p in model_config["llm_providers"]}
    for name in summary["model_list"]:
        assert name in models and models[name]["api_provider"] in providers  # 引用的 model 存在且 provider 合法


async def test_llm_manager_resolves_summary_and_shares_provider_connections(loaded) -> None:
    model_config, _ = loaded
    backend = MagicMock(side_effect=lambda cfg: MagicMock(cleanup=MagicMock(), _merged_config=cfg))
    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"openai": backend}):
        await manager.setup(model_config)

    profile = manager._profiles["summary"]
    assert profile.profile_name == "summary" and profile.models
    assert all(m.model_identifier and m.provider_name for m in profile.models)
    for provider_name in {m.provider_name for m in profile.models}:
        assert provider_name in manager._provider_clients  # 同 provider 的多个 profile 共享一个连接
