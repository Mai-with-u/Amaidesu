"""web 搜索 bing 入口默认值更换迁移测试（tools.toml v2.0.46）

cn.bing.com 对无 cookie 直连请求改为落地必应首页（不再返回结果页），
搜索入口默认值换为 www.bing.com/search。覆盖：
- 落盘值等于旧默认 → 加载 → 磁盘写回新入口、版本推进 2.0.46；
- 用户自定义入口 → 原值保留、版本照常推进；
- 二次加载零写回（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir


def _seed_base_url(config_dir: Path, base_url: str, version: str = "2.0.45") -> None:
    """把 tools.toml 拨回既有版本并写入指定的搜索入口落盘值。

    生成器默认不写 [tools.web.search] 成员段（启用后才物化），seed 自行建段
    模拟"搜索已被启用"的存量用户状态。
    """
    path = config_dir / "tools.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    web = doc["tools"]["web"]
    if "search" not in web:
        web["search"] = {"enabled": True, "config": {}}
    web["search"]["config"]["base_url"] = base_url
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def test_old_default_entry_rewritten_and_version_advanced(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    _seed_base_url(tmp_path, "https://cn.bing.com/search")

    load_config_dir(tmp_path)

    migrated = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert migrated["tools"]["web"]["search"]["config"]["base_url"] == "https://www.bing.com/search"
    assert get_config_version(tmp_path, "tools.toml") == "2.0.46"

    first = (tmp_path / "tools.toml").read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert (tmp_path / "tools.toml").read_text(encoding="utf-8-sig") == first


def test_custom_entry_kept_but_version_still_advances(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    _seed_base_url(tmp_path, "https://mirror.example.com/search")

    load_config_dir(tmp_path)

    migrated = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert migrated["tools"]["web"]["search"]["config"]["base_url"] == "https://mirror.example.com/search"
    assert get_config_version(tmp_path, "tools.toml") == "2.0.46"
