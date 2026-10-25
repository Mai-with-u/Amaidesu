"""配置落盘编码策略：写侧统一无 BOM，读侧兼容存量 BOM"""

import tomllib
from pathlib import Path

from src.modules.config.multi_file_loader import (
    _ROOT_SCHEMAS,
    _write_back_schema_file,
    generate_default_configs,
)


def test_generated_files_have_no_bom(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    for name in ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml"):
        assert (config_dir / name).read_bytes()[:3] != b"\xef\xbb\xbf", name


def test_drift_write_back_strips_legacy_bom(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    file_name = "agents.toml"
    target = config_dir / file_name
    target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())  # 模拟存量带 BOM 文件

    data = tomllib.loads(target.read_text(encoding="utf-8-sig"))
    data["agents"]["enabled"] = ["streamer", "minecraft"]
    schema_cls = _ROOT_SCHEMAS[file_name]
    _write_back_schema_file(config_dir, file_name, schema_cls, schema_cls.model_validate(data))

    new_bytes = target.read_bytes()
    assert new_bytes[:3] != b"\xef\xbb\xbf"
    assert tomllib.loads(new_bytes.decode("utf-8"))["agents"]["enabled"] == ["streamer", "minecraft"]
