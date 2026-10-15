"""思考强度档位（profile.reasoning_effort）与 provider 级方言逃生舱（extra_body）：零值默认、旧安装漂移补默认、用户显式值写回保留

纯新增字段零成本：补默认不升版本，版本只随在册钩子推进。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.model_schemas import LLMProfileConfig, LLMProviderConfig, ModelRootConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from tests.config.helpers import tail_version


def _model_doc(config_dir: Path) -> tuple[Path, tomlkit.TOMLDocument]:
    path = config_dir / "model.toml"
    return path, tomlkit.parse(path.read_text(encoding="utf-8-sig"))


def _fresh_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    return config_dir


def test_defaults_are_zero_values_and_both_fields_accept_free_form_values() -> None:
    assert LLMProfileConfig(model_list=["default"]).reasoning_effort == ""  # 不控制 = 请求不含该字段
    for value in (
        "low",
        "medium",
        "high",
        "ultra",
        "minimal",
        "xhigh",
    ):  # 厂商档位词汇随模型代际漂移，Schema 不做封闭枚举
        assert LLMProfileConfig(model_list=["default"], reasoning_effort=value).reasoning_effort == value
    assert LLMProviderConfig().extra_body == {}  # 不注入 = 请求不含该字段
    nested = {"vendor_x": {"nested": 1}, "custom_flag": "yes"}
    assert LLMProviderConfig(extra_body=nested).extra_body == nested  # 原样合并进请求体，schema 不限键

    root = ModelRootConfig()  # 设了才发
    assert all(profile["reasoning_effort"] == "" for profile in root.llm_profiles.model_dump().values())
    assert all(provider.extra_body == {} for provider in root.llm_providers)


def test_old_installs_missing_the_new_fields_get_defaults_written_back_without_a_version_bump(tmp_path: Path) -> None:
    config_dir = _fresh_config(tmp_path / "old")
    path, doc = _model_doc(config_dir)
    for profile in doc["llm_profiles"].values():
        profile.pop("reasoning_effort", None)
    for provider in doc["llm_providers"]:
        provider.pop("extra_body", None)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

    _, report = load_config_dir(config_dir)

    written = _model_doc(config_dir)[1]
    assert all(profile.get("reasoning_effort", "<missing>") == "" for profile in written["llm_profiles"].values())
    assert all(provider.get("extra_body", "<missing>") == {} for provider in written["llm_providers"])
    assert written["meta"]["version"] == tail_version("model.toml") and not report.has_drift


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("profile", "reasoning_effort", "low"),
        ("profile", "reasoning_effort", "high"),
        ("provider", "extra_body", {"k": "v"}),
        ("provider", "extra_body", {"thinking": {"type": "enabled"}}),
    ],
)
def test_user_supplied_values_survive_the_load_and_writeback_round_trip(
    tmp_path: Path, section: str, field: str, value
) -> None:
    config_dir = _fresh_config(tmp_path)
    path, doc = _model_doc(config_dir)
    target = doc["llm_profiles"]["planner"] if section == "profile" else doc["llm_providers"][0]
    target[field] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

    load_config_dir(config_dir)

    written = _model_doc(config_dir)[1]
    written_target = written["llm_profiles"]["planner"] if section == "profile" else written["llm_providers"][0]
    assert written_target[field] == value  # 不覆盖用户显式配置
