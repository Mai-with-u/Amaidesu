"""配置测试共享助手：迁移钩子链尾版本（避免各测试硬编码版本串）、tomlkit 读写与条件轮询。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

import pytest
import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.upgrade import _FILE_HOOKS, _version_tuple


async def wait_until(predicate: Callable[[], bool], *, what: str, timeout_s: float = 5.0) -> None:
    """轮询等待异步条件成立，超时 fail 并点名等的是什么。

    替代固定 sleep 等待文件监视器/热重载落地：条件一成立立即放行，超时上限
    按满载耗时估算留宽裕（正常耗时的几十倍），不贴正常耗时。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() >= deadline:
            pytest.fail(f"等待超时（{timeout_s}s）：{what}")
        await asyncio.sleep(0.02)


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
