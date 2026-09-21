"""avatar 域配置毕业跨文件迁移测试（tools.toml / infra.toml v2.0.38 → avatar.toml）

覆盖：
- [tools.avatar.*] → [avatar.platform.*]：成员段去 .config 层平铺、enabled
  布尔换算启用名单、宿主旧段删除、三文件版本推进；
- infra.toml [avatar.lipsync] → avatar.toml [avatar.lipsync]：整段搬迁；
- 用户显式值原样搬迁（不改名不改值）；
- 二次加载零写回（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _seed_old_tools_avatar(config_dir: Path) -> None:
    """给 tools.toml 追加旧 [tools.avatar.*] 段并拨回毕业前版本。"""
    path = config_dir / "tools.toml"
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.37"', 1)
    content += (
        "\n[tools.avatar.vts]\nenabled = true\n\n[tools.avatar.vts.config]\n"
        'type = "vts"\nvts_host = "127.0.0.1"\nvts_port = 8001\n'
        'idle_extra_params = { "SleeveL" = 0.3 }\n'
        "\n[tools.avatar.warudo]\nenabled = false\n\n[tools.avatar.warudo.config]\n"
        'type = "warudo"\nws_port = 19190\n'
    )
    path.write_text(content, encoding="utf-8-sig")


def _seed_old_infra_lipsync(config_dir: Path) -> None:
    """给 infra.toml 追加旧 [avatar.lipsync] 段并拨回毕业前版本。"""
    path = config_dir / "infra.toml"
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.37"', 1)
    content += '\n[avatar]\n\n[avatar.lipsync]\ntype = "lipsync"\nenabled = true\nsample_rate = 22050\n'
    path.write_text(content, encoding="utf-8-sig")


def test_platform_graduation_migrates_and_written_back(tmp_path: Path):
    """旧 tools 构造 → 加载 → 平台段落 avatar.toml、enabled 布尔换算名单、宿主删段。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_tools_avatar(tmp_path)

    load_config_dir(tmp_path)

    avatar_doc = tomlkit.parse((tmp_path / "avatar.toml").read_text(encoding="utf-8-sig"))
    platform = avatar_doc["platform"]
    # enabled 布尔 → 名单：仅 true 成员进名单
    assert list(platform["enabled"]) == ["vts"]
    # 成员段去 .config 层直接铺键 + 用户显式值保真
    assert platform["vts"]["vts_host"] == "127.0.0.1"
    assert platform["vts"]["vts_port"] == 8001
    assert platform["vts"]["idle_extra_params"]["SleeveL"] == 0.3
    # false 成员段搬迁（配置保留）但不进名单
    assert platform["warudo"]["ws_port"] == 19190
    assert "warudo" not in platform["enabled"]
    # 缺省键由 Schema 补默认（typed 递归）
    assert platform["vts"]["base_smile"] == 0.3

    # tools 侧旧段整体删除
    tools_doc = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert "avatar" not in tools_doc["tools"]

    # 版本推进：tools 链尾 2.0.38（毕业钩子 target），目标文件跟进不回退基线
    assert get_config_version(tmp_path, "tools.toml") == "2.0.38"
    assert get_config_version(tmp_path, "avatar.toml") == CONFIG_BASELINE_VERSION


def test_lipsync_graduation_migrates_and_written_back(tmp_path: Path):
    """旧 infra 构造 → 加载 → [avatar.lipsync] 落 avatar.toml、用户值保真。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_infra_lipsync(tmp_path)

    load_config_dir(tmp_path)

    avatar_doc = tomlkit.parse((tmp_path / "avatar.toml").read_text(encoding="utf-8-sig"))
    assert avatar_doc["lipsync"]["sample_rate"] == 22050
    assert avatar_doc["lipsync"]["enabled"] is True

    infra_doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert "avatar" not in infra_doc

    assert get_config_version(tmp_path, "infra.toml") == "2.0.38"


def test_graduation_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_tools_avatar(tmp_path)
    _seed_old_infra_lipsync(tmp_path)

    load_config_dir(tmp_path)
    snapshot = {
        name: (tmp_path / name).read_text(encoding="utf-8-sig")
        for name in ("tools.toml", "infra.toml", "avatar.toml")
    }

    _config, report = load_config_dir(tmp_path)

    assert not report.has_drift
    for name, content in snapshot.items():
        assert (tmp_path / name).read_text(encoding="utf-8-sig") == content
