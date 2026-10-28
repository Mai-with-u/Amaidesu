"""空异常处理守护：裸 ``except:`` 与宽捕获（Exception / BaseException）的 pass / continue 空体直接红

项目约定每个异常路径都记录日志并携带原因上下文，空 catch 块按缺陷处理（见 AGENTS.md）。
窄类型异常（如 ``asyncio.CancelledError: pass``、``json.JSONDecodeError: continue``）是
控制流惯用法、能从异常类型读出"为什么可以吞"，不在本守护范围；本守护只拦"宽捕获且零
处理体"——读者既不知道吞了什么、也没有任何理由说明。确属合理的宽捕获空体走豁免清单
逐项登记理由，新增违规会让本测试红。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# 豁免清单："相对路径:行号" → 豁免理由（逐项注明；行号随代码漂移时本测试红，需同步更新）
# 现无豁免：历史两处宽捕获空体已改为记 debug 日志（schema_generator 默认值填充、
# gptsovits provider 取消路径收敛等待），新违规请补日志或收窄异常类型而非登记豁免
EXEMPTED_SILENT_BROAD_EXCEPTS: dict[str, str] = {}


class _BroadSilentExceptFinder(ast.NodeVisitor):
    """收集"裸 except 或宽捕获且处理体仅为 pass/continue"的异常处理器"""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
        )
        silent_body = len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue))
        if broad and silent_body:
            self.hits.append(node.lineno)
        self.generic_visit(node)


def test_no_broad_silent_exception_handlers() -> None:
    violations: list[str] = []
    matched_exempt_keys: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        finder = _BroadSilentExceptFinder()
        finder.visit(tree)
        for lineno in finder.hits:
            key = f"{path.relative_to(SRC.parent).as_posix()}:{lineno}"
            if key in EXEMPTED_SILENT_BROAD_EXCEPTS:
                matched_exempt_keys.add(key)
            else:
                violations.append(key)
    assert not violations, (
        "发现宽捕获（裸 except / Exception / BaseException）且处理体仅为 pass/continue 的空异常处理："
        "按缺陷处理，请补充日志或收窄异常类型；确属合理的走 EXEMPTED_SILENT_BROAD_EXCEPTS 逐项登记理由：\n"
        + "\n".join(f"  - {item}" for item in violations)
    )
    stale = set(EXEMPTED_SILENT_BROAD_EXCEPTS) - matched_exempt_keys
    assert not stale, "豁免清单存在已失效条目（代码已改、行号对不上），请清理或更新：\n" + "\n".join(
        f"  - {item}" for item in sorted(stale)
    )
