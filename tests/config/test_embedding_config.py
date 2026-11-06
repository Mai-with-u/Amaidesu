"""model.toml 的 ``[embedding]`` 段：默认指向硅基流动的 bge-m3，旧文件加载时补默认值落盘；密钥留空即不开。"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from src.modules.config.model_schemas import EmbeddingConfig, ModelRootConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


def test_default_points_at_siliconflow_bge_m3_with_an_empty_key() -> None:
    """默认引用硅基流动条目与 bge-m3；那个条目密钥留空（是否真开由装配时看密钥定）。"""
    config = ModelRootConfig()
    assert (config.embedding.provider, config.embedding.model_identifier) == ("siliconflow", "BAAI/bge-m3")
    (siliconflow,) = [item for item in config.llm_providers if item.name == "siliconflow"]
    assert (siliconflow.base_url, siliconflow.api_key) == ("https://api.siliconflow.cn/v1", "")
    assert EmbeddingConfig(enabled=False).configured is False
    assert EmbeddingConfig(model_identifier=" ").configured is False


def test_model_name_without_a_known_provider_fails_at_load() -> None:
    with pytest.raises(ValidationError, match="embedding.provider"):
        ModelRootConfig(embedding={"model_identifier": "BAAI/bge-m3", "provider": "nope"})
    # 没填模型名时 provider 留空也合法（不开语义检索）
    assert ModelRootConfig(embedding={"provider": "", "model_identifier": ""}).embedding.provider == ""


def test_old_model_file_gets_the_section_written_back_on_load(tmp_path: Path) -> None:
    """升级前生成的 model.toml 没有这一段：加载时补默认值并写回文件，用户打开文件就能看到。"""
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    path = config_dir / "model.toml"
    doc = tomlkit.loads(path.read_text(encoding="utf-8-sig"))
    del doc["embedding"]
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    config, _report = load_config_dir(config_dir)

    assert config["model"]["embedding"]["model_identifier"] == "BAAI/bge-m3"
    written = tomlkit.loads(path.read_text(encoding="utf-8-sig"))
    assert written["embedding"]["enabled"] is True
    assert written["embedding"]["provider"] == "siliconflow"
