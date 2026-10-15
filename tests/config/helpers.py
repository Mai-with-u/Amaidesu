"""配置测试共享助手：迁移钩子链尾版本（避免各测试硬编码版本串）与 tomlkit 读写。"""

from __future__ import annotations

import re
from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.upgrade import _FILE_HOOKS, _version_tuple


def tail_version(file_name: str) -> str:
    """文件被加载后应推进到的版本：该文件钩子链最后一个 target；没有钩子的文件停在基线。"""
    targets = [hook.target_version for hook in _FILE_HOOKS.get(file_name, [])]
    return max(targets, key=_version_tuple) if targets else CONFIG_BASELINE_VERSION


def read_doc(config_dir: Path, file_name: str) -> tomlkit.TOMLDocument:
    return tomlkit.parse((config_dir / file_name).read_text(encoding="utf-8-sig"))


def write_doc(config_dir: Path, file_name: str, doc: tomlkit.TOMLDocument) -> None:
    (config_dir / file_name).write_text(tomlkit.dumps(doc), encoding="utf-8")


def rewind_and_append(config_dir: Path, file_name: str, version: str, extra_text: str = "") -> None:
    """把文件版本拨回 version，并在文件尾追加旧布局的 TOML 文本。"""
    path = config_dir / file_name
    content = re.sub(r'version = "[^"]+"', f'version = "{version}"', path.read_text(encoding="utf-8-sig"), count=1)
    path.write_text(content + extra_text, encoding="utf-8-sig")
