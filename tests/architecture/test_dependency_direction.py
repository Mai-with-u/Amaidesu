"""架构依赖方向：采集器、Agent、工具三层之间的模块级 import 禁止边

采集器（src/modules/collectors）与工具层（src/modules/tools）是被 Agent（src/agents）使用的一侧，不得反向依赖 Agent；
跨域通信走 EventBus 与 ToolRegistry，不直接 import。强制的禁止边：采集器不 import Agent 与工具、工具层不 import Agent。
Agent 依赖 src.modules.tools（ToolRegistry / ToolProvider / ToolSpec 等工具基础设施）是设计内的形态，不在禁止之列。

TYPE_CHECKING 块与函数/类体内的局部 import 不计（duck-typed Protocol 与延迟加载是合理的跨域形态）。
"""

import ast
from pathlib import Path
from typing import Dict, List

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
LAYER_ROOTS = {"input": SRC / "modules" / "collectors", "agent": SRC / "agents", "tool": SRC / "modules" / "tools"}
IMPORT_PREFIX_LAYER = {"src.agents": "agent", "src.modules.collectors": "input", "src.modules.tools": "tool"}


class _TopLevelImports(ast.NodeVisitor):
    """收集模块顶层 import（跳过 TYPE_CHECKING 块与函数/类体内的局部 import）。"""

    def __init__(self) -> None:
        self.imports: List[Dict] = []
        self._depth = 0
        self._type_checking = False

    def _scoped(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def visit_If(self, node: ast.If) -> None:
        guarded = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        previous, self._type_checking = self._type_checking, self._type_checking or guarded
        self.generic_visit(node)
        self._type_checking = previous

    def visit_Import(self, node: ast.Import) -> None:
        self._record([alias.name for alias in node.names], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._record([node.module], node.lineno)

    def _record(self, modules: List[str], line: int) -> None:
        if self._depth == 0 and not self._type_checking:
            self.imports.extend({"module": module, "line": line} for module in modules)


def _target_layer(module: str) -> str | None:
    return next(
        (layer for prefix, layer in IMPORT_PREFIX_LAYER.items() if module == prefix or module.startswith(prefix + ".")),
        None,
    )


def _violations(source_layer: str, target_layer: str) -> List[str]:
    found = []
    for path in sorted(LAYER_ROOTS[source_layer].rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        visitor = _TopLevelImports()
        visitor.visit(tree)
        found.extend(
            f"{path.relative_to(SRC.parent)}:{imp['line']} imports {imp['module']}"
            for imp in visitor.imports
            if _target_layer(imp["module"]) == target_layer
        )
    return found


@pytest.mark.parametrize(
    ("source", "target", "pattern"),
    [
        ("input", "agent", "采集器经 EventBus（room.message.*）通信，不 import Agent"),
        ("input", "tool", "采集器经 EventBus 通信，不 import 工具"),
        ("tool", "agent", "工具层不依赖 Agent 子系统"),
    ],
)
def test_forbidden_cross_layer_imports(source: str, target: str, pattern: str) -> None:
    found = _violations(source, target)
    assert not found, f"{source} 层不得 import {target} 层（{pattern}）：\n" + "\n".join(
        f"  - {line}" for line in found
    )
