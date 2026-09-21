"""avatar.toml 平台段校验测试

覆盖 avatar 域根 Schema（[avatar.platform] / [avatar.lipsync]）的加载期行为：
enabled 名单封闭集硬错、未注册平台残留段容忍保留、typed 成员段递归漂移检测
（缺键补默认、未知键剥离）、写回注释渲染。
"""

from __future__ import annotations

import tomlkit
import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import _validate_file, generate_default_configs, load_config_dir


def test_default_construction():
    """根 Schema 默认构造：vts 默认启用，成员段全默认。"""
    from src.modules.config.avatar_schemas import AvatarRootConfig

    cfg = AvatarRootConfig()
    assert cfg.platform.enabled == ["vts"]
    assert cfg.platform.vts.vts_host == "localhost"
    assert cfg.lipsync.enabled is True


def test_enabled_unknown_platform_hard_fails(tmp_path):
    """enabled 名单出现合法清单外的平台名 → 加载期硬错（给出合法名单）。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "avatar.toml"
    content = path.read_text(encoding="utf-8-sig")
    path.write_text(content.replace('enabled = ["vts"]', 'enabled = ["vts", "vst"]'), encoding="utf-8-sig")

    raw_doc = tomlkit.parse(path.read_text(encoding="utf-8-sig")).unwrap()
    with pytest.raises(ConfigValidationError) as exc_info:
        _validate_file("avatar.toml", raw_doc)
    assert "vst" in str(exc_info.value)
    assert "warudo" in str(exc_info.value)  # 合法名单随错误给出


def test_unregistered_platform_section_tolerated(tmp_path):
    """未注册平台残留段（extra=allow 保留）→ redundant 报告 + 数据不落盘丢失。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "avatar.toml"
    content = path.read_text(encoding="utf-8-sig")
    content += "\n[platform.retired_platform]\nsome_key = 1\n"
    path.write_text(content, encoding="utf-8-sig")

    raw_doc = tomlkit.parse(path.read_text(encoding="utf-8-sig")).unwrap()
    instance, report = _validate_file("avatar.toml", raw_doc)
    assert "platform.retired_platform" in report.redundant
    # 残留段保留在 extras（校验不剥离）
    assert instance.platform.__pydantic_extra__["retired_platform"] == {"some_key": 1}

    # 加载写回后段仍在磁盘（数据完整性：不因未注册静默丢弃）
    load_config_dir(tmp_path)
    assert "retired_platform" in path.read_text(encoding="utf-8-sig")


def test_member_section_drift_check_via_typed_ref(tmp_path):
    """typed 成员段递归漂移：缺键补默认、未知键剥离并带完整路径报告。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "avatar.toml"
    content = path.read_text(encoding="utf-8-sig")
    # 在 [platform.vts] 段尾追加未知键（vts 段已含全部默认键，直接尾追新键值行）
    content = content.replace(
        '[platform.vts]\n',
        '[platform.vts]\nstale_key = 1\n',
        1,
    )
    path.write_text(content, encoding="utf-8-sig")

    raw_doc = tomlkit.parse(path.read_text(encoding="utf-8-sig")).unwrap()
    _instance, report = _validate_file("avatar.toml", raw_doc)
    assert "platform.vts.stale_key" in report.redundant


def test_load_writeback_renders_member_comments(tmp_path):
    """删空 [platform.vts] 段 → 加载全默认构造 → 写回带字段注释渲染。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "avatar.toml"
    content = path.read_text(encoding="utf-8-sig")
    # 删除 [platform.vts] 整段（从段头到下一段头）
    head = content.index("[platform.vts]")
    tail = content.index("[platform.warudo]")
    path.write_text(content[:head] + content[tail:], encoding="utf-8-sig")

    load_config_dir(tmp_path)

    written = path.read_text(encoding="utf-8-sig")
    assert "[platform.vts]" in written  # 名单引用缺段 → 全默认构造补出
    assert "vts_host = " in written
    assert "# VTS WebSocket 主机地址" in written  # 包内 description 成为行前注释
    assert "# 口型分析采样率 Hz" in written


def test_enabled_member_without_section_gets_defaults(tmp_path):
    """enabled 名单成员缺段 = 全默认构造（讨论定案：启用缺段写回补出）。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "avatar.toml"
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace('enabled = ["vts"]', 'enabled = ["vts", "warudo"]')
    head = content.index("[platform.warudo]")
    tail = content.index("[platform.vrchat]")
    content = content[:head] + content[tail:]
    path.write_text(content, encoding="utf-8-sig")

    _config, report = load_config_dir(tmp_path)

    written = path.read_text(encoding="utf-8-sig")
    assert "[platform.warudo]" in written
    assert 'ws_port = 19190' in written
    assert report is not None
