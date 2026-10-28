"""类型注解完备守护：src/ 下所有函数与方法的参数及返回值必须带注解

项目代码约定"所有函数与方法必须含完整类型注解（参数与返回值类型），缺注解视为缺陷"
（见 AGENTS.md）；需要推迟导入的场合走 ``TYPE_CHECKING`` 块解决，不影响运行期注解，
故不设豁免。``self`` / ``cls`` 是隐式约定的接收者、无需注解，不计入。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _unannotated_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    missing = [arg.arg for arg in args if arg.annotation is None and arg.arg not in ("self", "cls")]
    if node.args.vararg is not None and node.args.vararg.annotation is None:
        missing.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg is not None and node.args.kwarg.annotation is None:
        missing.append(f"**{node.args.kwarg.arg}")
    return missing


def test_all_src_functions_are_fully_annotated() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            missing = _unannotated_args(node)
            if node.returns is None:
                missing.append("<return>")
            if missing:
                violations.append(f"{path.relative_to(SRC.parent)}:{node.lineno} {node.name} 缺注解: {missing}")
    assert not violations, (
        "src/ 下存在缺类型注解的函数/方法（参数与返回值必须完整注解）：\n"
        + "\n".join(f"  - {item}" for item in violations)
    )
