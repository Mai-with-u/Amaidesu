"""架构守护测试共享的只读源码 AST 缓存。

同一进程内七个守护测试都会扫描 ``src/`` 或 ``tests/`` 的 Python 语法树；
在模块/会话级各解析一次并缓存只读元组，避免重复磁盘读取与 ``ast.parse``。
调用方只读遍历语法树，不修改 AST 节点。
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
TESTS_ROOT = REPO_ROOT / "tests"

ParsedTreeList = Tuple[Tuple[Path, ast.Module], ...]


@lru_cache(maxsize=None)
def _scan_root(root: Path) -> ParsedTreeList:
    """一次性扫描整个根目录下的全部 ``*.py`` 文件并解析 AST。"""
    entries: list[tuple[Path, ast.Module]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        entries.append((path, tree))
    return tuple(entries)


@lru_cache(maxsize=None)
def parsed_py_files(root: Path) -> ParsedTreeList:
    """返回 ``root`` 下所有可解析的 ``(Path, ast.Module)`` 只读元组。

    若 ``root`` 落在 ``src/`` 或 ``tests/`` 内，直接复用顶层根的已解析缓存并按前缀过滤，
    不再重复遍历目录或重新 ``ast.parse``。
    """
    resolved = root.resolve()
    for top in (SRC_ROOT, TESTS_ROOT):
        if resolved == top:
            return _scan_root(top)
        if resolved.is_relative_to(top):
            return tuple((path, tree) for path, tree in _scan_root(top) if path.is_relative_to(resolved))
    return _scan_root(resolved)
