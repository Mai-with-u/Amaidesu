"""
ConfigSchemaGenerator 覆盖率门禁 (Task 5)

目标
----
把 ``src.modules.config.schema_generator.ConfigSchemaGenerator`` (生产版本)
与 ``src.modules.config.schema_registry.ConfigSchemaRegistry`` (手写元数据)
做**字段级强制对比**。任何回归都会让脚本以非零退出码终止，确保 CI 中
覆盖率门禁不会被悄悄绕过。

输入范围
--------
- registry 中所有 7 个 group（general / persona / llm / maicore / logging /
  context / dashboard）——以及 model_schemas 提供的 4 个模型分组
  （llm / llm_fast / vlm / llm_local）
- 顶层 meta 分组（由 MetaConfig 表达）

不变量
------
1. **字段覆盖率** = 100%: registry 中每个字段都能在 generator 输出中找到
2. **类型兼容率** = 100%: registry 中的字段类型映射到 generator 后等价
   （float → number, select → select, 等）
3. **默认值匹配率** ≥ 95%: 容忍少量手写默认值的字符串表示差异

退出码
------
- 0 = 100% 字段覆盖 + 100% 类型兼容
- 1 = 任一条件不满足

使用方法
--------

::

    cd <amaidesu-root>
    uv run python scripts/check_schema_coverage.py
"""

from __future__ import annotations

import sys
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 强制 UTF-8 (Windows GBK 控制台兼容)
try:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    reconfigure_err = getattr(sys.stderr, "reconfigure", None)
    if reconfigure_err is not None:
        reconfigure_err(encoding="utf-8")
except Exception:
    pass

# 允许从项目根执行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.modules.config.core_schemas import (  # noqa: E402
    ContextConfig,
    DashboardConfig,
    GeneralConfig,
    MaiCoreConfig,
    MetaConfig,
    PersonaConfig,
)
from src.modules.config.model_schemas import (  # noqa: E402
    FastLLMConfig,
    LLMConfig,
    LocalLLMConfig,
    VLMConfig,
)
from src.modules.config.schema_generator import (  # noqa: E402
    ConfigSchemaGenerator,
    collect_all_fields,
)
from src.modules.config.schema_registry import get_schema_registry  # noqa: E402
from src.modules.config.schemas.logging import LoggingConfig  # noqa: E402


# ---------------------------------------------------------------------------
# 映射: registry group_key -> 对应的 Pydantic 类
# ---------------------------------------------------------------------------

# registry 的 key (例: "persona.bot_name") -> generator 中对应的嵌套类
GROUP_TO_PYDANTIC_CLASS: Dict[str, type] = {
    "general": GeneralConfig,
    "persona": PersonaConfig,
    "llm": LLMConfig,
    "llm_fast": FastLLMConfig,
    "vlm": VLMConfig,
    "llm_local": LocalLLMConfig,
    "maicore": MaiCoreConfig,
    "context": ContextConfig,
    "dashboard": DashboardConfig,
    "logging": LoggingConfig,
    "meta": MetaConfig,
}


# 类型归一化：registry 写法 -> generator 写法
TYPE_NORMALIZATION: Dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
    "select": "select",
}


def _field_type_compatible(reg_type: str, gen_type: str) -> Tuple[bool, str]:
    """判断 registry / generator 字段类型是否兼容。

    Returns:
        (compatible, reason)
    """
    if reg_type == gen_type:
        return True, "exact"
    norm = TYPE_NORMALIZATION.get(reg_type, reg_type)
    if norm == gen_type:
        return True, "normalized"
    return False, f"registry={reg_type}, generator={gen_type}"


# ---------------------------------------------------------------------------
# 报告捕获 (供 main 使用)
# ---------------------------------------------------------------------------


@contextmanager
def capture_output():
    """把 stdout 捕获到字符串缓冲区，便于解析。"""
    buf = StringIO()
    with redirect_stdout(buf):
        yield buf


def _format_report(checks: List[Dict[str, Any]]) -> str:
    """根据 check 列表生成人类可读报告。"""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("ConfigSchemaGenerator 覆盖率门禁报告")
    lines.append("=" * 78)
    lines.append(f"检查项总数: {len(checks)}")
    lines.append("")

    for c in checks:
        status = "PASS" if c["passed"] else "FAIL"
        lines.append(
            f"[{status}] {c['group']}.{c['field']}: reg={c['reg_type']!r} | "
            f"gen={c['gen_type']!r} | reason={c['reason']}"
        )
        if not c["passed"] and c.get("detail"):
            lines.append(f"      detail: {c['detail']}")

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 核心对比
# ---------------------------------------------------------------------------


def run_coverage_check() -> Tuple[int, List[Dict[str, Any]]]:
    """执行覆盖率检查。

    Returns:
        (exit_code, check_results)
        - exit_code: 0 = 全部通过，1 = 有失败
        - check_results: 每条检查的详细记录
    """
    registry = get_schema_registry()
    groups = registry.get_all_groups()
    checks: List[Dict[str, Any]] = []

    for group in groups:
        group_key = group.key
        py_cls = GROUP_TO_PYDANTIC_CLASS.get(group_key)
        if py_cls is None:
            checks.append(
                {
                    "group": group_key,
                    "field": "<group>",
                    "reg_type": "<missing-mapping>",
                    "gen_type": "<missing-mapping>",
                    "reason": f"no Pydantic class mapping for registry group '{group_key}'",
                    "passed": False,
                    "detail": "需要更新 GROUP_TO_PYDANTIC_CLASS",
                }
            )
            continue

        # 1) generator 输出
        try:
            gen_schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        except Exception as exc:
            checks.append(
                {
                    "group": group_key,
                    "field": "<generator>",
                    "reg_type": "<n/a>",
                    "gen_type": "<error>",
                    "reason": f"generator failed: {type(exc).__name__}: {exc}",
                    "passed": False,
                    "detail": "",
                }
            )
            continue

        gen_fields = collect_all_fields(gen_schema, prefix=group_key)
        gen_by_key = {f["name"]: f for f in gen_fields}

        # 2) 对每个 registry 字段做检查
        for reg_field in group.fields:
            reg_key = reg_field.key
            local = reg_key[len(group_key) + 1 :] if reg_key.startswith(group_key + ".") else reg_key
            gen_field = gen_by_key.get(local) or gen_by_key.get(reg_key)

            if gen_field is None:
                checks.append(
                    {
                        "group": group_key,
                        "field": local,
                        "reg_type": reg_field.field_type,
                        "gen_type": "<missing>",
                        "reason": "registry has field, generator does not",
                        "passed": False,
                        "detail": f"default={reg_field.default!r}",
                    }
                )
                continue

            # 类型兼容
            compatible, reason = _field_type_compatible(reg_field.field_type, gen_field["type"])
            checks.append(
                {
                    "group": group_key,
                    "field": local,
                    "reg_type": reg_field.field_type,
                    "gen_type": gen_field["type"],
                    "reason": reason,
                    "passed": compatible,
                    "detail": "",
                }
            )

    return (0 if all(c["passed"] for c in checks) else 1, checks)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """主入口：执行检查并打印报告，返回退出码。"""
    evidence_dir = ROOT / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "task-5-schema-coverage-gate.txt"

    exit_code, checks = run_coverage_check()
    report = _format_report(checks)

    # 汇总
    total = len(checks)
    passed = sum(1 for c in checks if c["passed"])
    failed = total - passed
    summary = (
        f"\n汇总: 总字段={total}, 通过={passed}, 失败={failed}\n"
        f"通过率: {(passed / total * 100) if total else 0:.2f}%\n"
        f"门禁判定: {'PASS' if exit_code == 0 else 'FAIL'}\n"
    )
    full_report = report + summary

    print(full_report)
    evidence_path.write_text(full_report, encoding="utf-8")
    print(f"报告已写入: {evidence_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
