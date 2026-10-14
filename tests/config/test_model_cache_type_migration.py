"""llm_models[].cache 开关串归一为显式枚举（none / hit_discount），价格字段原样保留。"""

from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from src.modules.config.model_schemas import LLMModelConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


@pytest.mark.parametrize(
    ("old_cache", "cache_price_in", "expected"),
    [
        # 空串 + 已填缓存价：启用分段意图明确 → hit_discount
        ("", 0.2, "hit_discount"),
        # 空串 + 未填缓存价：无歧义 → none
        ("", 0.0, "none"),
        # 非空旧值（任意串仅作开关用）→ hit_discount
        ("anthropic", 0.0, "hit_discount"),
    ],
)
def test_cache_switch_normalized_to_enum(tmp_path: Path, old_cache: str, cache_price_in: float, expected: str) -> None:
    """旧开关串按旧语义分流归一；价格字段原样保留，重载零漂移。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "model.toml"
    data = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    data["meta"]["version"] = "2.0.42"
    model = data["llm_models"][0]
    model["cache"] = old_cache
    model["cache_price_in"] = cache_price_in
    model["price_in"] = 2.0
    path.write_text(tomlkit.dumps(data), encoding="utf-8")

    load_config_dir(tmp_path)

    migrated = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert migrated["meta"]["version"] == "2.0.44"
    migrated_model = migrated["llm_models"][0]
    assert migrated_model["cache"] == expected
    assert migrated_model["cache_price_in"] == cache_price_in
    assert migrated_model["price_in"] == 2.0

    _, report = load_config_dir(tmp_path)
    assert not report.has_drift


def test_cache_none_with_price_rejected_by_schema() -> None:
    """交叉校验：声明 none 却填缓存价 = 配置矛盾（分段不生效），加载期硬错。"""
    with pytest.raises(ValidationError, match="配置矛盾"):
        LLMModelConfig(name="m", cache="none", cache_price_in=0.1)
