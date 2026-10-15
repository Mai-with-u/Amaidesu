"""avatar.toml 平台段校验：enabled 封闭集与残留段硬错、typed 成员段漂移检测、缺段全默认构造、写回注释渲染"""

from __future__ import annotations

import pytest
import tomlkit

from src.modules.config.avatar_schemas import AvatarRootConfig
from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import _validate_file, generate_default_configs, load_config_dir


def _raw(path) -> dict:
    return tomlkit.parse(path.read_text(encoding="utf-8-sig")).unwrap()


def _edit(path, edit) -> None:
    path.write_text(edit(path.read_text(encoding="utf-8-sig")), encoding="utf-8-sig")


@pytest.fixture
def avatar_path(tmp_path):
    generate_default_configs(tmp_path)
    return tmp_path / "avatar.toml"


def test_default_construction_enables_vts_with_default_member_sections() -> None:
    cfg = AvatarRootConfig()
    assert cfg.platform.enabled == ["vts"] and cfg.platform.vts.vts_host == "localhost" and cfg.lipsync.enabled is True


def test_unknown_platform_name_or_leftover_section_hard_fails_with_the_valid_list(avatar_path) -> None:
    _edit(avatar_path, lambda c: c.replace('enabled = ["vts"]', 'enabled = ["vts", "vst"]'))
    with pytest.raises(ConfigValidationError) as exc_info:  # enabled 名单出现合法清单外的平台名
        _validate_file("avatar.toml", _raw(avatar_path))
    assert "vst" in str(exc_info.value) and "warudo" in str(exc_info.value)  # 合法名单随错误给出

    _edit(
        avatar_path,
        lambda c: c.replace('enabled = ["vts", "vst"]', 'enabled = ["vts"]')
        + "\n[platform.retired_platform]\nsome_key = 1\n",
    )
    with pytest.raises(ConfigValidationError) as exc_info:  # 未注册平台残留段：avatar.toml 同为契约面，当面拦下
        _validate_file("avatar.toml", _raw(avatar_path))
    assert "retired_platform" in str(exc_info.value) and "warudo" in str(exc_info.value)


def test_typed_member_section_reports_unknown_keys_with_the_full_path(avatar_path) -> None:
    _edit(avatar_path, lambda c: c.replace("[platform.vts]\n", "[platform.vts]\nstale_key = 1\n", 1))
    _, report = _validate_file("avatar.toml", _raw(avatar_path))
    assert "platform.vts.stale_key" in report.redundant


def test_missing_member_sections_are_built_from_defaults_and_written_back_with_comments(avatar_path) -> None:
    def drop_vts(content: str) -> str:
        return content[: content.index("[platform.vts]")] + content[content.index("[platform.warudo]") :]

    _edit(avatar_path, drop_vts)
    load_config_dir(avatar_path.parent)
    written = avatar_path.read_text(encoding="utf-8-sig")
    assert "[platform.vts]" in written and "vts_host = " in written  # 名单引用缺段 → 全默认构造补出
    assert "# VTS WebSocket 主机地址" in written and "# 口型分析采样率 Hz" in written  # 包内 description 成为行前注释

    def enable_warudo_without_section(content: str) -> str:
        content = content.replace('enabled = ["vts"]', 'enabled = ["vts", "warudo"]')
        return content[: content.index("[platform.warudo]")] + content[content.index("[platform.vrchat]") :]

    _edit(avatar_path, enable_warudo_without_section)  # enabled 名单成员缺段 = 全默认构造，写回补出
    load_config_dir(avatar_path.parent)
    assert "[platform.warudo]" in avatar_path.read_text(encoding="utf-8-sig")
    assert "ws_port = 19190" in avatar_path.read_text(encoding="utf-8-sig")
