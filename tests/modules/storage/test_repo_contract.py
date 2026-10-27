"""仓储读面契约测试：公开读方法不得返回 ``sqlite3.Row``

仓储的读面以 Pydantic 模型（``storage/models``）为持久化契约；裸
``sqlite3.Row`` 只允许出现在 ``storage/database.py`` 的通用 SQL 逃生口
（``execute`` / ``execute_fetchone``）。两层防线：

1. 源码扫描：``storage/repos/`` 下任何文件不得出现 ``sqlite3.Row`` 字样
   （含内部辅助函数与写面嵌套函数的注解——漏网即红）；
2. 反射扫描：每个仓储类的公开 async 读方法（非 ``_`` 前缀）的返回注解
   里不得出现 ``sqlite3.Row``（含 ``Optional`` / ``List`` 等包装内的嵌套）。

未来任何人新增 Row 形态读面，本测试在 CI 拦下。
"""

from __future__ import annotations

import inspect
import sqlite3
import typing
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest

from src.modules.storage.repos import _base

REPOS_DIR = Path(_base.__file__).parent


def _repo_classes() -> list[type[Any]]:
    """收集仓储包里定义的全部仓储类（含 ``_base.BaseRepo`` 的子类）。"""
    classes: list[type[Any]] = []
    for path in sorted(REPOS_DIR.glob("*.py")):
        module_name = f"src.modules.storage.repos.{path.stem}"
        module = __import__(module_name, fromlist=["*"])
        for _, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module_name:
                classes.append(obj)
    return classes


def _contains_row(annotation: Any) -> bool:
    """递归判断注解里是否出现 ``sqlite3.Row``（穿透 Optional/List/Tuple 等包装）。"""
    if annotation is sqlite3.Row:
        return True
    if annotation is typing.Any or annotation is inspect.Parameter.empty:
        return False
    for sub in get_args(annotation):
        if _contains_row(sub):
            return True
    return False


def test_repos_source_has_no_row_reference() -> None:
    """仓储包源码不得出现 ``sqlite3.Row`` 字样（原始 SQL 逃生口只在 database.py）。"""
    offenders = [
        path.name for path in sorted(REPOS_DIR.glob("*.py")) if "sqlite3.Row" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"仓储读面残留 sqlite3.Row: {offenders}（改用 storage/models 契约模型）"


@pytest.mark.parametrize("repo_cls", _repo_classes(), ids=lambda cls: cls.__name__)
def test_public_read_methods_do_not_return_row(repo_cls: type[Any]) -> None:
    """仓储公开 async 方法的返回注解不得含 ``sqlite3.Row``（含包装内的嵌套）。"""
    for name, member in vars(repo_cls).items():
        if name.startswith("_") or not inspect.isfunction(member) or not inspect.iscoroutinefunction(member):
            continue
        hints = get_type_hints(member)
        return_hint = hints.get("return")
        assert return_hint is not None, f"{repo_cls.__name__}.{name} 缺返回类型注解"
        assert not _contains_row(return_hint), (
            f"{repo_cls.__name__}.{name} 返回注解含 sqlite3.Row：读面应返回 storage/models 的契约模型"
        )


def test_origin_of_row_annotated_methods_is_empty() -> None:
    """防线自检：构造一个返回 ``List[sqlite3.Row]`` 的注解，确认探测器能识别。"""
    assert _contains_row(list[sqlite3.Row]) and _contains_row(sqlite3.Row | None)
