"""思考强度档位与 provider 级方言逃生舱的 schema 默认 + 漂移写回补默认测试。

跟随 ``tests/modules/config/`` 既有模式：
- 新字段默认值（零值默认：空串 / 空 dict，与 ``price_in`` / ``cache`` 先例一致）
- 旧安装缺新字段 → 漂移写回补默认（不改文件版本，纯新增字段零成本）
- 旧安装写入非空值 → 写回保留用户值（不覆盖用户显式配置）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.model_schemas import (
    LLMProfileConfig,
    LLMProviderConfig,
    ModelRootConfig,
)
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


# === Schema 默认值（零值默认：空串 / 空 dict，与 price_in / cache 先例一致）===


class TestReasoningEffortAndExtraBodyDefaults:
    def test_llm_profile_reasoning_effort_default_empty_string(self) -> None:
        """profile.reasoning_effort 缺省空串（不控制 = 请求不含该字段）。"""
        profile = LLMProfileConfig(model_list=["default"])
        assert profile.reasoning_effort == ""

    def test_llm_profile_reasoning_effort_accepts_free_string(self) -> None:
        """profile.reasoning_effort 是自由字符串（不做封闭枚举校验）。

        各厂商档位词汇（low/medium/high 等）随模型代际漂移；Schema 拒合法值即腐烂。
        """
        for value in ("low", "medium", "high", "ultra", "minimal", "xhigh"):
            profile = LLMProfileConfig(model_list=["default"], reasoning_effort=value)
            assert profile.reasoning_effort == value

    def test_llm_provider_extra_body_default_empty_dict(self) -> None:
        """provider.extra_body 缺省空 dict（不注入 = 请求不含该字段）。"""
        provider = LLMProviderConfig()
        assert provider.extra_body == {}

    def test_llm_provider_extra_body_accepts_arbitrary_keys(self) -> None:
        """provider.extra_body 是自由 dict（原样合并进请求体；schema 不限键）。"""
        provider = LLMProviderConfig(extra_body={"vendor_x": {"nested": 1}, "custom_flag": "yes"})
        assert provider.extra_body == {"vendor_x": {"nested": 1}, "custom_flag": "yes"}

    def test_model_root_default_has_no_reasoning_effort_or_extra_body(self) -> None:
        """全新安装的 ModelRootConfig 默认无 reasoning_effort / extra_body（设了才发语义）。"""
        root = ModelRootConfig()
        for profile in root.llm_profiles.model_dump().values():
            assert profile["reasoning_effort"] == ""
        for provider in root.llm_providers:
            assert provider.extra_body == {}


# === 漂移写回补默认（纯新增字段零成本，不改版本）===


class TestDriftWritebackFillsNewFields:
    def test_existing_profile_missing_reasoning_effort_gets_default_on_load(self, tmp_path: Path) -> None:
        """旧安装 profile 缺 reasoning_effort → 加载时按 schema 默认空串补齐落盘。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        original_version = model_doc["meta"]["version"]
        # 模拟旧安装：所有 profile 缺 reasoning_effort 字段
        for profile in model_doc["llm_profiles"].values():
            if "reasoning_effort" in profile:
                del profile["reasoning_effort"]
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        for profile_name, profile in written["llm_profiles"].items():
            assert profile.get("reasoning_effort", "<missing>") == "", (
                f"profile {profile_name!r} 漂移写回后 reasoning_effort 必须为空串（默认零值）"
            )
        # 纯新增字段零成本：不升文件版本
        assert written["meta"]["version"] == original_version
        assert not report.has_drift

    def test_existing_provider_missing_extra_body_gets_default_on_load(self, tmp_path: Path) -> None:
        """旧安装 provider 缺 extra_body → 加载时按 schema 默认空 dict 补齐落盘。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        original_version = model_doc["meta"]["version"]
        # 模拟旧安装：所有 provider 缺 extra_body 字段
        for provider in model_doc["llm_providers"]:
            if "extra_body" in provider:
                del provider["extra_body"]
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        for provider in written["llm_providers"]:
            assert provider.get("extra_body", "<missing>") == {}, (
                "provider 漂移写回后 extra_body 必须为空 dict（默认零值）"
            )
        assert written["meta"]["version"] == original_version
        assert not report.has_drift

    def test_user_supplied_reasoning_effort_is_preserved_on_writeback(self, tmp_path: Path) -> None:
        """用户显式设置 reasoning_effort=high → 写回保留用户值（不覆盖显式配置）。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        model_doc["llm_profiles"]["planner"]["reasoning_effort"] = "high"
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, _report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        assert written["llm_profiles"]["planner"]["reasoning_effort"] == "high"

    def test_user_supplied_extra_body_is_preserved_on_writeback(self, tmp_path: Path) -> None:
        """用户显式配置 extra_body → 写回保留用户值（不覆盖显式配置）。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        model_doc["llm_providers"][0]["extra_body"] = {"thinking": {"type": "enabled"}}
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, _report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        assert written["llm_providers"][0]["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reasoning_effort", "low"),
        ("reasoning_effort", "medium"),
        ("reasoning_effort", "high"),
        ("extra_body", {"k": "v"}),
        ("extra_body", {"nested": {"deep": 1}}),
    ],
)
def test_round_trip_preserves_field(field: str, value: object, tmp_path: Path) -> None:
    """任意合法配置值都能完整 round-trip：写入 → 加载 → 写出 = 原值。"""
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    model_path = config_dir / "model.toml"
    doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    if field == "reasoning_effort":
        doc["llm_profiles"]["planner"][field] = value  # type: ignore[index]
    else:
        doc["llm_providers"][0][field] = value  # type: ignore[index]
    model_path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

    load_config_dir(config_dir)

    written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    if field == "reasoning_effort":
        assert written["llm_profiles"]["planner"][field] == value  # type: ignore[index]
    else:
        assert written["llm_providers"][0][field] == value  # type: ignore[index]
