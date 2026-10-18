"""工具提供者 config 子段（tools 动态分类段 [tools.studio.<name>] / [tools.web.<name>]）的注册表校验与默认值补全

覆盖加载期行为：默认值补齐落盘（含字段注释）、未知键与残留段硬错、类型违约硬错、WebUI 写入口校验。
机制与采集器子段同构。avatar 分类已迁出为 avatar.toml [avatar.platform.*]。
"""

from __future__ import annotations

import tomlkit
import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    _validate_file,
    generate_default_configs,
    load_config_dir,
    validate_config_updates,
)

OBS_SECTION = "\n[tools.studio.obs]\nenabled = true\n\n[tools.studio.obs.config]\n"
WEB_SEARCH_SECTION = "\n[tools.web.search]\nenabled = true\n\n[tools.web.search.config]\n"


@pytest.fixture
def config_dir(tmp_path):
    directory = tmp_path / "config"
    generate_default_configs(directory)
    return directory


def _append(config_dir, section_text: str) -> None:
    """在 tools.toml 尾部追加段（绝对表头，尾追安全）。"""
    path = config_dir / "tools.toml"
    path.write_text(path.read_text(encoding="utf-8-sig") + section_text.strip("\n") + "\n", encoding="utf-8-sig")


def _tools_text(config_dir) -> str:
    return (config_dir / "tools.toml").read_text(encoding="utf-8-sig")


def test_empty_config_backfills_defaults_with_comments_into_disk_and_runtime_view(config_dir) -> None:
    _append(config_dir, OBS_SECTION)
    config, _ = load_config_dir(config_dir)

    content = _tools_text(config_dir)
    assert 'host = "localhost"' in content and "port = 4455" in content and "text_source_name" in content
    assert (
        "# OBS WebSocket 主机地址" in content and "# OBS WebSocket 端口" in content
    )  # ConfigSchema description 落盘为字段注释
    obs = config["tools"]["tools"]["studio"]["obs"]["config"]  # 补全后的 config 进入运行时配置树，装配侧消费同一份数据
    assert (obs["text_source_name"], obs["port"]) == ("text", 4455)
    assert not load_config_dir(config_dir)[1].has_drift  # 写回后无残余漂移

    other = config_dir.parent / "web"
    generate_default_configs(other)
    _append(other, WEB_SEARCH_SECTION)
    load_config_dir(other)
    web = _tools_text(other)  # web 动态分类域走同一注册表管线
    assert 'base_url = "https://www.bing.com/search"' in web and "timeout_ms = 10000" in web
    assert "max_fetch_chars" not in web


def test_unknown_keys_and_retired_sections_hard_fail_with_pointers(config_dir) -> None:
    _append(config_dir, OBS_SECTION + "stale_key = 1\n")
    with pytest.raises(ConfigValidationError) as exc_info:  # 静默剥离 = 用户数据丢失，冗余键全域硬错
        load_config_dir(config_dir)
    assert "stale_key" in str(exc_info.value)

    typo = config_dir.parent / "typo"
    generate_default_configs(typo)
    _append(typo, "\n[tools.studio.obs]\nenabled = true\nenbled_typo = false\n\n[tools.studio.obs.config]\n")
    raw = tomlkit.loads((typo / "tools.toml").read_text(encoding="utf-8-sig")).unwrap()
    _, report = _validate_file("tools.toml", raw)
    assert "tools.studio.obs.enbled_typo" in report.redundant  # 段本体未知键：报告可见，加载层聚合硬错接管

    retired = config_dir.parent / "retired"
    generate_default_configs(retired)
    _append(
        retired,
        "\n[tools.studio.retired_thing]\nenabled = false\n\n[tools.studio.retired_thing.config]\nsome_key = 1\n",
    )
    with pytest.raises(ConfigValidationError) as exc_info:  # 残留段（已退役 provider）：点名 + 合法名单
        load_config_dir(retired)
    assert "retired_thing" in str(exc_info.value) and "studio.obs" in str(exc_info.value)


def test_type_violations_fail_at_load_and_on_the_webui_write_path(config_dir) -> None:
    broken = config_dir.parent / "broken"
    generate_default_configs(broken)
    _append(broken, OBS_SECTION + 'port = "abc"\n')
    with pytest.raises(ConfigValidationError) as exc_info:  # 加载期硬错，路径含 tools.studio.obs.config
        load_config_dir(broken)
    assert "tools.studio.obs.config" in str(exc_info.value)

    _append(config_dir, OBS_SECTION + WEB_SEARCH_SECTION)
    for updates in (
        {"tools.studio.obs.config.port": "not-a-port"},
        {"tools.web.search.config.timeout_ms": "not-a-number"},
    ):
        with pytest.raises(ConfigValidationError):  # WebUI 写入口：统一校验事务拒绝，磁盘零写入
            validate_config_updates(config_dir, "tools.toml", updates)
