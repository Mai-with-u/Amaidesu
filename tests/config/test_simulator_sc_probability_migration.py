"""[simulator].sc_probability 死键删键迁移测试（infra.toml v2.0.41）

覆盖：
- 旧布局（带 sc_probability 键）→ 加载 → 磁盘写回后键消失、版本推进、其余内容不动
- 无该键的文件 → 幂等通过、版本正常推进
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import load_config_dir


def _seed_old_layout(config_dir: Path) -> None:
    """在生成的 infra.toml 的 [simulator] 段注入旧键并拨回上一版本。"""
    path = config_dir / "infra.toml"
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace("[simulator]", "[simulator]\nsc_probability = 0.01\n", 1)
    path.write_text(content, encoding="utf-8-sig")


def test_sc_probability_dropped_and_written_back(tmp_path: Path):
    """旧布局构造 → 加载 → 磁盘写回 sc_probability 键消失、版本推进、其余内容不动。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_layout(tmp_path)

    before = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert before["simulator"]["sc_probability"] == 0.01

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    simulator = doc["simulator"]
    assert "sc_probability" not in simulator
    assert "sc_pay_probability" in simulator  # 实际生效的付费 SC 概率不受影响
    assert simulator["gift_probability"] == before["simulator"]["gift_probability"]
    assert doc["meta"]["version"] == "2.0.41"


def test_sc_probability_migration_idempotent(tmp_path: Path):
    """迁移后的配置二次加载零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_layout(tmp_path)

    load_config_dir(tmp_path)
    first = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")

    load_config_dir(tmp_path)
    second = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")

    assert first == second
    doc = tomlkit.parse(second)
    assert "sc_probability" not in doc["simulator"]


def test_no_key_file_version_advances(tmp_path: Path):
    """不带旧键的存量文件（版本 2.0.40）→ 幂等通过、版本仍正常推进。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)  # 新生成文件不含 sc_probability，版本为基线
    path = tmp_path / "infra.toml"
    original = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert "sc_probability" not in original["simulator"]

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert "sc_probability" not in doc["simulator"]
    assert doc["meta"]["version"] == "2.0.41"
