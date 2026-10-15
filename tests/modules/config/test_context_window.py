"""``[[llm_models]].context_window``：零值默认（0 = 未配置，前端据此隐藏水位而非显示 0%）、旧安装漂移补默认、用户值保留、引擎装配期暴露"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tomlkit
from pydantic import ValidationError

from src.modules.config.model_schemas import LLMModelConfig, ModelRootConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from tests.config.helpers import tail_version


def _model_doc(config_dir: Path) -> tuple[Path, tomlkit.TOMLDocument]:
    path = config_dir / "model.toml"
    return path, tomlkit.parse(path.read_text(encoding="utf-8-sig"))


def test_schema_defaults_to_zero_accepts_positive_and_rejects_negative() -> None:
    assert LLMModelConfig().context_window == 0
    for value in (1, 4096, 32_768, 128_000, 1_000_000):
        assert LLMModelConfig(context_window=value).context_window == value
    with pytest.raises(ValidationError):  # ge=0
        LLMModelConfig(context_window=-1)
    assert all(model.context_window == 0 for model in ModelRootConfig().llm_models)


def test_old_installs_get_zero_written_back_and_user_values_survive(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    path, doc = _model_doc(config_dir)
    for model in doc["llm_models"]:
        model.pop("context_window", None)  # 模拟旧安装：条目缺该字段
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

    _, report = load_config_dir(config_dir)
    written = _model_doc(config_dir)[1]
    assert all(model.get("context_window", "<missing>") == 0 for model in written["llm_models"])
    assert written["meta"]["version"] == tail_version("model.toml") and not report.has_drift  # 纯新增字段补默认不升版本

    path, doc = _model_doc(config_dir)
    name = doc["llm_models"][0]["name"]
    doc["llm_models"][0]["context_window"] = 128_000
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")
    load_config_dir(config_dir)
    kept = next(m for m in _model_doc(config_dir)[1]["llm_models"] if m["name"] == name)
    assert kept["context_window"] == 128_000  # 不覆盖显式配置


async def test_engine_exposes_configured_windows_keyed_by_identifier_and_zero_for_unknown(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    model_config = dict(load_config_dir(config_dir)[0]["model"])
    models = [dict(m) for m in model_config["llm_models"]]
    models[0]["context_window"] = 128_000
    model_config["llm_models"] = models

    manager = LLMManager()
    backend = MagicMock(side_effect=lambda cfg: MagicMock(cleanup=MagicMock(), _merged_config=cfg))
    with patch.dict(_CLIENT_DISPATCH, {"openai": backend}):
        await manager.setup(model_config)

    windows = manager.get_model_context_windows()
    assert list(windows.values()) == [128_000]  # 全新安装仅 default 一条 model
    assert LLMManager().get_model_context_window("never-configured-model") == 0  # 未配置返回 0，不抛、不取相邻值
