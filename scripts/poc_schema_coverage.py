"""
POC Schema Coverage Script (Task 1)

目标
----
验证 ``src.modules.config.schema_generator.ConfigSchemaGenerator``（POC
版本，迁移自 MaiBot v1.0.0 ``ConfigSchemaGenerator``）对 Amaidesu 项目内
Pydantic 配置类的覆盖率：

- **field count match rate**: 生成 schema 的字段数 vs Pydantic model ``model_fields``
- **type mapping accuracy**: 生成的 UI 类型 (string/integer/number/boolean/
  array/object/select) 是否与底层 Python / Pydantic 注解一致
- **constraint retention rate**: ``Field(ge=, le=, pattern=...)`` 约束是否
  保留到 UI schema（minValue / maxValue / pattern）

输入范围
--------
- Amaidesu 顶层配置：``CoreConfig``、``ModelConfig``
- 所有阶段（input / decision / output）的 ``ConfigSchema`` 嵌套类

不修改项目内任何源码，仅做只读分析。

使用方法
--------
::

    cd <amaidesu-root>
    uv run python scripts/poc_schema_coverage.py
    # 报告同时写入 stdout 与 .omo/evidence/task-1-schema-coverage.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

# 强制 stdout/stderr 使用 UTF-8（避免 Windows GBK 控制台编码错误）
try:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    reconfigure_err = getattr(sys.stderr, "reconfigure", None)
    if reconfigure_err is not None:
        reconfigure_err(encoding="utf-8")
except Exception:
    pass

import importlib
import inspect
import pkgutil
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Set, Tuple, Type

# 允许从项目根执行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel  # noqa: E402
from pydantic.fields import FieldInfo  # noqa: E402

from src.modules.config.schema_generator import (  # noqa: E402
    ConfigSchemaGenerator,
    collect_all_fields,
)
from src.modules.config.core_schemas import CoreConfig  # noqa: E402
from src.modules.config.model_schemas import ModelConfig  # noqa: E402


# ---------------------------------------------------------------------------
# 报告输出重定向（同时写文件 + stdout）
# ---------------------------------------------------------------------------


@contextmanager
def tee_output(path: Path):
    """把 stdout / stderr 同时输出到控制台与指定文件。"""
    log_file = path.open("w", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams

        def write(self, data: str):
            for s in self._streams:
                s.write(data)

        def flush(self):
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass

    tee_stdout = _Tee(original_stdout, log_file)
    tee_stderr = _Tee(original_stderr, log_file)
    sys.stdout = tee_stdout
    sys.stderr = tee_stderr
    try:
        yield log_file
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


# ---------------------------------------------------------------------------
# 发现 ConfigSchema 嵌套类
# ---------------------------------------------------------------------------


STAGE_PACKAGES: Tuple[str, ...] = (
    "src.stages.input",
    "src.stages.decision",
    "src.stages.output",
)


def _iter_submodules(pkg_name: str) -> Iterable[str]:
    """按广度优先遍历包内所有子模块。"""
    pkg = importlib.import_module(pkg_name)
    if not hasattr(pkg, "__path__"):
        yield pkg_name
        return
    for mod_info in pkgutil.walk_packages(pkg.__path__, prefix=pkg_name + "."):
        yield mod_info.name


def discover_config_schemas() -> List[Tuple[str, Type[BaseModel]]]:
    """扫描所有阶段包中的 ``<组件类>.ConfigSchema`` 嵌套类。

    Amaidesu 的阶段参与者以 ``class ConsoleInputCollector: class ConfigSchema(BaseConfig): ...``
    形式将配置 Schema 作为嵌套类挂在组件类下，因此需要先枚举模块内所有
    ``ConfigSchema`` 嵌套类，再返回 ``(模块名, 嵌套类)``。
    """
    found: List[Tuple[str, Type[BaseModel]]] = []
    seen_ids: Set[int] = set()

    for pkg_name in STAGE_PACKAGES:
        try:
            mod_names = list(_iter_submodules(pkg_name))
        except Exception as exc:
            print(f"[警告] 遍历 {pkg_name} 失败: {exc}")
            continue

        for mod_name in mod_names:
            try:
                module = importlib.import_module(mod_name)
            except Exception as exc:
                print(f"[警告] 跳过 {mod_name}: {exc}")
                continue

            # 遍历模块内全部类（含嵌套）—— ConfigSchema 通常嵌套在组件类中
            for _, component_cls in inspect.getmembers(module, inspect.isclass):
                if component_cls.__module__ != mod_name:
                    continue
                nested = getattr(component_cls, "ConfigSchema", None)
                if nested is None:
                    continue
                if not inspect.isclass(nested):
                    continue
                if not issubclass(nested, BaseModel):
                    continue
                key = id(nested)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                found.append((mod_name, nested))

    found.sort(key=lambda t: (t[0], t[1].__name__))
    return found


# ---------------------------------------------------------------------------
# 字段计数 / 类型映射 / 约束提取
# ---------------------------------------------------------------------------


# UI 类型字符串 → 兼容的 Python 类型集合
TYPE_EXPECTATIONS: Dict[str, Set[type]] = {
    "string": {str},
    "integer": {int},
    "number": {int, float},
    "boolean": {bool},
    "array": {list, set, tuple},
    "object": set(),  # 任意 dict / BaseModel 子类
    "select": set(),  # 由 Literal / Enum 推导
}


# Pydantic 注解 → 期望 UI 类型（粗分类）
def _expected_ui_type(annotation: Any) -> str:
    """根据 Python 注解推导期望 UI 类型（用于校验）。"""
    import typing as t

    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is str:
        return "string"

    origin = t.get_origin(annotation)
    args = t.get_args(annotation)

    if origin is t.Literal:
        return "select"
    if origin in {list, set, tuple}:
        return "array"
    if origin is dict:
        return "object"
    if origin is t.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _expected_ui_type(non_none[0])
        return "string"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "object"
    return "string"


def _field_has_constraints(field_info: FieldInfo) -> bool:
    """判断字段是否声明了 ge/le/gt/lt/pattern/min_length/max_length。"""
    if not getattr(field_info, "metadata", None):
        return False
    for c in field_info.metadata:
        for attr in ("ge", "le", "gt", "lt", "pattern", "min_length", "max_length", "multiple_of"):
            if getattr(c, attr, None) is not None:
                return True
    return False


def _ui_constraints_retained(field_schema: Dict[str, Any]) -> bool:
    """UI schema 中是否保留至少一项约束。"""
    constraint_keys = {
        "minValue",
        "maxValue",
        "exclusiveMinValue",
        "exclusiveMaxValue",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
    }
    return any(k in field_schema for k in constraint_keys)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _separator(title: str) -> str:
    line = "=" * 78
    return f"\n{line}\n{title}\n{line}"


def main() -> int:
    evidence_dir = ROOT / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output_path = evidence_dir / "task-1-schema-coverage.txt"

    with tee_output(output_path) as _:
        print(_separator("POC Schema Coverage Report - Task 1"))
        print("生成器: src.modules.config.schema_generator.ConfigSchemaGenerator")
        print("Pydantic 顶层类: CoreConfig, ModelConfig")
        print(f"阶段扫描范围: {', '.join(STAGE_PACKAGES)}")
        print(f"输出时间: {Path(__file__).name}")

        # -------------------------------------------------------------------
        # 1. 顶层配置 (CoreConfig / ModelConfig)
        # -------------------------------------------------------------------
        top_level_results: List[Dict[str, Any]] = []
        for cls in [CoreConfig, ModelConfig]:
            schema = ConfigSchemaGenerator.generate_config_schema(cls)
            top_level_results.append(_evaluate_one_class(cls, schema, label=cls.__name__))

        # -------------------------------------------------------------------
        # 2. 阶段 ConfigSchema 嵌套类
        # -------------------------------------------------------------------
        discovered = discover_config_schemas()
        print(_separator(f"发现的 ConfigSchema 嵌套类 ({len(discovered)} 个)"))
        for mod_name, cls in discovered:
            print(f"  - {mod_name}.ConfigSchema  → {cls.__name__}")

        stage_results: List[Dict[str, Any]] = []
        for mod_name, cls in discovered:
            try:
                schema = ConfigSchemaGenerator.generate_config_schema(cls)
            except Exception as exc:
                stage_results.append(
                    {
                        "label": f"{mod_name}.ConfigSchema",
                        "error": f"{type(exc).__name__}: {exc}",
                        "matched_count": 0,
                        "total_count": 0,
                        "field_match_rate": 0.0,
                        "type_match_rate": 0.0,
                        "constraint_retention_rate": 0.0,
                        "fields": [],
                    }
                )
                continue
            label = f"{mod_name.rsplit('.', 1)[-1]}.{cls.__name__}"
            result = _evaluate_one_class(cls, schema, label=label, module_name=mod_name)
            stage_results.append(result)

        # -------------------------------------------------------------------
        # 3. 汇总统计
        # -------------------------------------------------------------------
        print(_separator("汇总统计"))
        all_results = top_level_results + stage_results
        total_pairs = sum(r["total_count"] for r in all_results)
        total_matched = sum(r["matched_count"] for r in all_results)
        total_type_ok = sum(r["type_ok_count"] for r in all_results)
        total_constraint_required = sum(r["constraint_required_count"] for r in all_results)
        total_constraint_kept = sum(r["constraint_kept_count"] for r in all_results)

        field_match_rate = (total_matched / total_pairs) if total_pairs else 0.0
        type_match_rate = (total_type_ok / total_pairs) if total_pairs else 0.0
        constraint_rate = (total_constraint_kept / total_constraint_required) if total_constraint_required else 1.0

        print(f"评估类总数:        {len(all_results)}")
        print(f"字段总数:          {total_pairs}")
        print(f"命中字段 (存在):   {total_matched}")
        print(f"字段覆盖率:        {field_match_rate:.4f}  ({total_matched}/{total_pairs})")
        print(f"类型映射正确率:    {type_match_rate:.4f}  ({total_type_ok}/{total_pairs})")
        print(
            f"约束保留率:        {constraint_rate:.4f}  "
            f"({total_constraint_kept}/{total_constraint_required} 个有约束字段)"
        )

        # 失败明细
        failures: List[Tuple[str, str]] = []
        for r in all_results:
            for f in r["fields"]:
                if not f.get("present"):
                    failures.append((r["label"], f"[missing] {f['key']}"))
                elif not f.get("type_ok"):
                    failures.append(
                        (
                            r["label"],
                            f"[type-mismatch] {f['key']}: 期望 {f['expected_type']}, 实际 {f['actual_type']}",
                        )
                    )
                elif f.get("expected_constraint") and not f.get("constraint_kept"):
                    failures.append(
                        (
                            r["label"],
                            f"[constraint-lost] {f['key']}: 字段声明约束但 UI schema 未保留",
                        )
                    )

        if failures:
            print(_separator("失败明细"))
            for label, msg in failures[:50]:
                print(f"  {label}: {msg}")
            if len(failures) > 50:
                print(f"  … 剩余 {len(failures) - 50} 条省略")
        else:
            print(_separator("失败明细"))
            print("  无。所有字段均匹配。")

        # -------------------------------------------------------------------
        # 4. 结论
        # -------------------------------------------------------------------
        print(_separator("结论"))
        threshold = 0.95
        passed = field_match_rate >= threshold and type_match_rate >= threshold and constraint_rate >= threshold
        print(f"通过阈值: {threshold:.2f}（覆盖率 / 类型 / 约束）")
        print(f"最终判定: {'PASS' if passed else 'FAIL'}")
        print(f"\n报告已写入: {output_path}")

    return 0 if passed else 1


def _evaluate_one_class(
    cls: Type[BaseModel],
    schema: Dict[str, Any],
    label: str,
    module_name: str = "",
) -> Dict[str, Any]:
    """对单个 ConfigSchema 类的生成结果做字段级评估。"""
    fields_eval: List[Dict[str, Any]] = []
    matched = 0
    type_ok = 0
    constraint_required = 0
    constraint_kept = 0

    flat_fields = collect_all_fields(schema)
    flat_by_key = {f["name"]: f for f in flat_fields}

    print(_separator(f"{label}  ({len(flat_fields)} flattened fields)"))

    for field_name, field_info in cls.model_fields.items():
        if field_name in {"field_docs", "_validate_any", "suppress_any_warning"}:
            continue
        if field_name == "type":
            # `type` 字段是组件标识符，保留但跳过覆盖率统计
            continue

        flat = flat_by_key.get(field_name)
        present = flat is not None
        if present:
            matched += 1

        annotation = field_info.annotation
        expected_ui = _expected_ui_type(annotation)
        actual_ui = flat["type"] if flat else "<missing>"
        type_matches = present and (actual_ui == expected_ui)
        if type_matches:
            type_ok += 1

        has_constraint = _field_has_constraints(field_info)
        constraint_retained = present and _ui_constraints_retained(flat)
        if has_constraint:
            constraint_required += 1
            if constraint_retained:
                constraint_kept += 1

        fields_eval.append(
            {
                "key": field_name,
                "present": present,
                "expected_type": expected_ui,
                "actual_type": actual_ui,
                "type_ok": type_matches,
                "expected_constraint": has_constraint,
                "constraint_kept": constraint_retained,
            }
        )

        if not present or not type_matches:
            tag = "MISS" if not present else f"BAD_TYPE({actual_ui}!={expected_ui})"
            print(f"  [X] {field_name}: {tag} | annotation={annotation} | constraint_present={has_constraint}")
        elif has_constraint and not constraint_retained:
            print(f"  [!] {field_name}: 约束声明但未保留")
        else:
            suffix = "(constraint kept)" if has_constraint else ""
            print(f"  [OK] {field_name}: {expected_ui} {suffix}")

    total = len(fields_eval)
    field_match_rate = (matched / total) if total else 1.0
    type_match_rate = (type_ok / total) if total else 1.0
    constraint_rate = (constraint_kept / constraint_required) if constraint_required else 1.0

    print(
        f"  → fields={total} matched={matched} ({field_match_rate:.2%}) | "
        f"types={type_match_rate:.2%} | "
        f"constraints={constraint_rate:.2%} ({constraint_kept}/{constraint_required})"
    )

    return {
        "label": label,
        "matched_count": matched,
        "total_count": total,
        "type_ok_count": type_ok,
        "constraint_required_count": constraint_required,
        "constraint_kept_count": constraint_kept,
        "field_match_rate": field_match_rate,
        "type_match_rate": type_match_rate,
        "constraint_retention_rate": constraint_rate,
        "fields": fields_eval,
    }


if __name__ == "__main__":
    sys.exit(main())
