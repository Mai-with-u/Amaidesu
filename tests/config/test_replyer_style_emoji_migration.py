"""style_constraints 旧默认删除"适当使用emoji"的迁移测试（agents.toml v2.0.45）。

表达侧模板统一禁 emoji 后，旧默认落盘值与指令自相矛盾；仅值等于旧默认时改写为
新默认，用户显式设置的其他值不动；重复加载不再改写（幂等）。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _upgrade_replyer_style_drop_emoji


def _write_style(tmp_path: Path, value: str, version: str = "2.0.44") -> Path:
    """把 agents.toml 拨到旧版本并写入给定的 style_constraints 值。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    doc["agents"]["streamer"]["persona"]["style_constraints"] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_old_default_rewritten_and_version_advanced(tmp_path: Path) -> None:
    """旧默认落盘值改写为新默认，版本推进到 2.0.47；再次加载不再改写。"""
    generate_default_configs(tmp_path)
    path = _write_style(tmp_path, "口语化，使用网络流行语，避免机械式回复，适当使用emoji")

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["persona"]["style_constraints"] == "口语化，使用网络流行语，避免机械式回复"
    assert doc["meta"]["version"] == "2.0.50"

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_user_custom_value_untouched(tmp_path: Path) -> None:
    """用户显式设置的其他值不动（版本戳照常推进）。"""
    generate_default_configs(tmp_path)
    path = _write_style(tmp_path, "简短、犀利，带点方言味")

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["persona"]["style_constraints"] == "简短、犀利，带点方言味"
    assert doc["meta"]["version"] == "2.0.50"


def test_hook_is_idempotent() -> None:
    """钩子幂等：已迁移的数据重复执行不再产生变更。"""
    data = {"agents": {"streamer": {"persona": {"style_constraints": "口语化，使用网络流行语，避免机械式回复"}}}}
    assert _upgrade_replyer_style_drop_emoji(data) == []
