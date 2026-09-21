"""工具提供者 config 子段的注册表校验与默认值补全测试

覆盖 tools 动态分类段（[tools.studio.<name>]；avatar 分类已迁出为 avatar.toml
[avatar.platform.*]，平台段校验见 test_avatar_migration.py）的
加载期行为：默认值补齐落盘（含注释渲染）、未知键剥离、类型违约硬错、
残留段容忍、WebUI 写入口校验。机制与采集器子段（_validate_collectors_
sections）同构，断言形状对齐 test_writeback_pipeline 的既有用例。
"""

from __future__ import annotations

import shutil

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    _validate_file,
    generate_default_configs,
    load_config_dir,
    validate_config_updates,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir, ignore_errors=True)


def _add_vts_section(config_dir, section_text: str) -> None:
    """在 tools.toml 尾部追加 [tools.studio.obs] 段（绝对表头，尾追安全）。"""
    path = config_dir / "tools.toml"
    content = path.read_text(encoding="utf-8-sig")
    path.write_text(content + section_text.strip("\n") + "\n", encoding="utf-8-sig")


_VTS_SECTION = """
[tools.studio.obs]
enabled = true

[tools.studio.obs.config]
"""

_WEB_SEARCH_SECTION = """
[tools.web.search]
enabled = true

[tools.web.search.config]
"""


class TestProviderConfigBackfill:
    def test_empty_config_backfills_defaults_to_disk(self, temp_config_dir):
        """空 config 段 → 默认值补齐并写回落盘（本机制的核心验收锚点）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION)

        _config, _report = load_config_dir(temp_config_dir)

        content = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        assert 'host = "localhost"' in content
        assert "port = 4455" in content
        assert "text_source_name" in content

    def test_backfilled_config_reaches_merged_view(self, temp_config_dir):
        """补全后的 config 进入运行时配置树（装配侧消费同一份数据）。

        ``load_config_dir`` 返回 ``{scope: 根 dump}``（ConfigService 再展平），
        故 tools 段在 ``config["tools"]["tools"]`` 下。
        """
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION)

        config, _report = load_config_dir(temp_config_dir)

        obs_config = config["tools"]["tools"]["studio"]["obs"]["config"]
        assert obs_config["text_source_name"] == "text"
        assert obs_config["port"] == 4455

    def test_defaults_render_with_field_comments(self, temp_config_dir):
        """写回的 config 子表带字段级注释（ConfigSchema description 落盘）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION)

        load_config_dir(temp_config_dir)

        content = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        assert "# OBS WebSocket 主机地址" in content
        assert "# OBS WebSocket 端口" in content

    def test_second_load_is_stable(self, temp_config_dir):
        """补全写回后再加载：无残余漂移、不再改写文件（写回短路）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION)
        load_config_dir(temp_config_dir)

        _config, report = load_config_dir(temp_config_dir)

        assert not report.has_drift


class TestProviderConfigDrift:
    def test_unknown_config_key_stripped_and_reported(self, temp_config_dir):
        """config 内未知键 → 剥离 + redundant 报告（带完整路径前缀）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION + "stale_key = 1\n")

        import tomlkit

        with open(temp_config_dir / "tools.toml", "r", encoding="utf-8-sig") as f:
            raw = tomlkit.load(f).unwrap()
        _instance, report = _validate_file("tools.toml", raw)

        assert "tools.studio.obs.config.stale_key" in report.redundant
        # 落盘后物理消失
        load_config_dir(temp_config_dir)
        assert "stale_key" not in (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")

    def test_section_level_unknown_key_reported(self, temp_config_dir):
        """段本体未知键（enabled 拼写错误等）→ redundant 报告可见（写回时清理）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(
            temp_config_dir,
            """
[tools.studio.obs]
enabled = true
enbled_typo = false

[tools.studio.obs.config]
""",
        )

        import tomlkit

        with open(temp_config_dir / "tools.toml", "r", encoding="utf-8-sig") as f:
            raw = tomlkit.load(f).unwrap()
        _instance, report = _validate_file("tools.toml", raw)

        assert "tools.studio.obs.enbled_typo" in report.redundant


class TestProviderConfigHardFail:
    def test_type_violation_raises_with_dotted_path(self, temp_config_dir):
        """类型违约 → 加载期硬错，路径含 tools.studio.obs.config（前移自运行期）。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(temp_config_dir, _VTS_SECTION + 'port = "abc"\n')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)

        message = str(exc_info.value)
        assert "tools.studio.obs.config" in message

    def test_update_path_type_violation_raises(self, temp_config_dir):
        """WebUI 写入口：错误类型经统一校验事务拒绝（磁盘零写入）。"""
        generate_default_configs(temp_dir := temp_config_dir)
        _add_vts_section(temp_dir, _VTS_SECTION)

        with pytest.raises(ConfigValidationError):
            validate_config_updates(
                temp_config_dir,
                "tools.toml",
                {"tools.studio.obs.config.port": "not-a-port"},
            )


class TestUnregisteredSectionTolerance:
    def test_unregistered_section_warns_and_keeps(self, temp_config_dir):
        """残留段（已退役 provider）→ warning 跳过、不抛错、段原样保留。"""
        generate_default_configs(temp_config_dir)
        _add_vts_section(
            temp_config_dir,
            """
[tools.studio.retired_thing]
enabled = false

[tools.studio.retired_thing.config]
some_key = 1
""",
        )

        # 不抛错即通过（残留容忍）；段保留在合并视图
        config, _report = load_config_dir(temp_config_dir)

        retired = config["tools"]["tools"]["studio"]["retired_thing"]
        assert retired["config"]["some_key"] == 1


class TestWebSearchProviderConfig:
    """web 动态分类域（[tools.web.search]）与 avatar/studio 同管线的锚点用例。"""

    def _add_web_search_section(self, config_dir, section_text: str) -> None:
        path = config_dir / "tools.toml"
        content = path.read_text(encoding="utf-8-sig")
        path.write_text(content + section_text.strip("\n") + "\n", encoding="utf-8-sig")

    def test_empty_config_backfills_defaults_to_disk(self, temp_config_dir):
        """空 config 段 → 默认值补齐并写回落盘（web 域走注册表管线的验收锚点）。"""
        generate_default_configs(temp_config_dir)
        self._add_web_search_section(temp_config_dir, _WEB_SEARCH_SECTION)

        load_config_dir(temp_config_dir)

        content = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        assert 'base_url = "https://cn.bing.com/search"' in content
        assert "timeout_ms = 10000" in content
        assert "max_fetch_chars = 4000" in content

    def test_update_path_type_violation_raises(self, temp_config_dir):
        """WebUI 写入口：web.search.config 字段类型违约经统一校验事务拒绝。"""
        generate_default_configs(temp_config_dir)
        self._add_web_search_section(temp_config_dir, _WEB_SEARCH_SECTION)

        with pytest.raises(ConfigValidationError):
            validate_config_updates(
                temp_config_dir,
                "tools.toml",
                {"tools.web.search.config.timeout_ms": "not-a-number"},
            )
