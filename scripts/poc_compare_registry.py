"""
POC Schema Registry Comparison Script (Task 1 - optional)

目标
----
将 ``src.modules.config.schema_registry.ConfigSchemaRegistry``（手写 7 个 group、
约 40 个字段）与 ``ConfigSchemaGenerator`` 自动生成的 schema 做**字段级对比**，
量化以下指标：

- **registry vs generator** 字段覆盖率（生成器新增/删除多少字段）
- **类型映射差异**：registry 中 ``field_type="float"`` vs generator 中 ``type="number"``；
  registry 中 ``field_type="select"`` 与 ``field_type="string"`` 的不一致
- **默认值差异**：registry 写死 default vs generator 读 ``Field(default=...)``
- **约束差异**：registry 的 ``validation.min/max`` vs generator 的 ``minValue/maxValue``

输出
----
- 写入 ``.omo/evidence/task-1-registry-comparison.txt``
- 退出码：0 = 信息已记录（与覆盖率脚本解耦）

注意
----
该脚本不修改任何项目内源代码，只读 registry + 生成 schema。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 强制 UTF-8（Windows GBK 控制台兼容）
try:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    reconfigure_err = getattr(sys.stderr, "reconfigure", None)
    if reconfigure_err is not None:
        reconfigure_err(encoding="utf-8")
except Exception:
    pass

from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

# 允许从项目根执行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.modules.config.schema_generator import (  # noqa: E402
    ConfigSchemaGenerator,
    collect_all_fields,
)
from src.modules.config.schema_registry import get_schema_registry  # noqa: E402
from src.modules.config.core_schemas import (  # noqa: E402
    DashboardConfig,
    LoggingConfig as CoreLoggingConfig,
    MetaConfig,
    GeneralConfig,
    PersonaConfig,
    MaiCoreConfig,
    ContextConfig,
)
from src.modules.config.model_schemas import (  # noqa: E402
    LLMConfig,
    FastLLMConfig,
    VLMConfig,
    LocalLLMConfig,
)


# ---------------------------------------------------------------------------
# 报告重定向
# ---------------------------------------------------------------------------


@contextmanager
def tee_output(path: Path):
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

    sys.stdout = _Tee(original_stdout, log_file)
    sys.stderr = _Tee(original_stderr, log_file)
    try:
        yield log_file
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


# ---------------------------------------------------------------------------
# registry → 生成器 schema 的映射（手写，因为 registry 用 "x.y.z" 点号键，
# 而 generator 输出嵌套 dict + collect_all_fields 铺平为 "x.y.z"）
# ---------------------------------------------------------------------------

# 字段类型归一化：registry 实际写法 -> generator 写法
TYPE_NORMALIZATION = {
    "string": "string",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
    "select": "select",
}


# registry 的 key (例: "persona.bot_name") -> 生成器 schema 中对应的嵌套类
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
    "logging": CoreLoggingConfig,
    "meta": MetaConfig,
}


def _field_type_compatible(reg_type: str, gen_type: str) -> Tuple[bool, str]:
    """判断 registry / generator 字段类型是否兼容。

    处理以下已知的合理差异：
    - ``float`` == ``number``（同义）
    - ``select`` 是 ``string`` 的子类型（generator 比 registry 更精确）
    """
    if reg_type == gen_type:
        return True, "exact"
    norm = TYPE_NORMALIZATION.get(reg_type, reg_type)
    if norm == gen_type:
        return True, "normalized"
    if reg_type == "select" and gen_type == "string":
        return False, "registry=select but generator=string (likely error)"
    if reg_type == "string" and gen_type == "select":
        return True, "generator stricter (Literal → select)"
    if reg_type == "boolean" and gen_type == "boolean":
        return True, "exact"
    return False, f"registry={reg_type} generator={gen_type}"


def _extract_registry_value(reg: Dict[str, Any], key: str) -> Optional[Any]:
    if "value" in reg and reg["value"] is not None:
        return reg["value"]
    if "default" in reg:
        return reg["default"]
    return None


def _generator_constraint(field: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """返回 (minValue, maxValue) 或 (None, None)"""
    mn = field.get("minValue", field.get("exclusiveMinValue"))
    mx = field.get("maxValue", field.get("exclusiveMaxValue"))
    return mn, mx


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _separator(title: str) -> str:
    line = "=" * 78
    return f"\n{line}\n{title}\n{line}"


def main() -> int:
    evidence_dir = ROOT / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output_path = evidence_dir / "task-1-registry-comparison.txt"

    with tee_output(output_path) as _:
        print(_separator("POC Schema Registry Comparison - Task 1"))
        print("registry 源: src.modules.config.schema_registry.ConfigSchemaRegistry")
        print("generator 源: src.modules.config.schema_generator.ConfigSchemaGenerator")
        print("对比范围: registry 中所有 7 个 group 的字段 vs generator 生成的 schema")

        registry = get_schema_registry()
        groups = registry.get_all_groups()

        # 汇总
        total_reg_fields = 0
        total_gen_fields = 0
        matched_keys = 0
        type_exact = 0
        type_norm = 0
        type_stricter = 0
        type_mismatch = 0
        default_match = 0
        default_mismatch = 0
        default_skipped = 0
        constraint_match = 0
        constraint_mismatch = 0
        constraint_only_in_one = 0
        missing_in_generator: List[str] = []
        extra_in_generator: List[str] = []
        type_mismatch_details: List[str] = []

        for group in groups:
            group_key = group.key
            pyd_cls = GROUP_TO_PYDANTIC_CLASS.get(group_key)
            print(_separator(f"Group: {group_key} ({group.label})"))

            if pyd_cls is None:
                print(f"  [SKIP] registry group {group_key} 没有对应的 Pydantic 映射")
                continue

            # 1) generator 输出
            try:
                gen_schema = ConfigSchemaGenerator.generate_config_schema(pyd_cls)
            except Exception as exc:
                print(f"  [ERROR] generator 失败: {exc}")
                continue
            gen_fields = collect_all_fields(gen_schema, prefix=group_key)
            gen_by_key = {f["name"]: f for f in gen_fields}

            print(f"  registry 字段: {len(group.fields)}")
            print(f"  generator 字段: {len(gen_fields)}")

            # 2) 对每个 registry 字段找 generator 对应
            for reg_field in group.fields:
                reg_key = reg_field.key  # e.g., "persona.bot_name"
                # strip group_key prefix to get local name
                local = reg_key[len(group_key) + 1 :] if reg_key.startswith(group_key + ".") else reg_key
                # 可能 local 是嵌套路径 (e.g. "dashboard.cors_origins.0")，这里 collector 走平后是 .0
                # 我们的 collect_all_fields 已经嵌套展开为 prefix.field，对应 reg_key 形如
                # "dashboard.cors_origins"，因此 local == reg_field_name
                gen_field = gen_by_key.get(local)
                if gen_field is None:
                    # 试嵌套名：registry 中 "llm.api_key" 对应 generator "llm.api_key" —
                    # reg_key already includes group prefix
                    gen_field = gen_by_key.get(reg_key)
                total_reg_fields += 1
                if gen_field is None:
                    missing_in_generator.append(f"{group_key}.{local}")
                    print(
                        f"  [-] {reg_key}: registry 有, generator 无 "
                        f"(reg.type={reg_field.field_type}, default={reg_field.default!r})"
                    )
                    continue
                matched_keys += 1

                # 类型
                reg_type = reg_field.field_type
                gen_type = gen_field["type"]
                compatible, reason = _field_type_compatible(reg_type, gen_type)
                if reason == "exact":
                    type_exact += 1
                elif reason == "normalized":
                    type_norm += 1
                elif reason.startswith("generator stricter"):
                    type_stricter += 1
                else:
                    type_mismatch += 1
                    type_mismatch_details.append(f"{reg_key}: registry={reg_type}, generator={gen_type}")

                # default
                reg_default = _extract_registry_value(reg_field.to_dict(), reg_key)
                gen_default = gen_field.get("default")
                if reg_default is None and gen_default is None:
                    default_match += 1
                elif reg_default is None or gen_default is None:
                    default_mismatch += 1
                elif str(reg_default) == str(gen_default):
                    default_match += 1
                elif isinstance(reg_default, type(gen_default)):
                    # 类型相同但字符串不同（如 enum 名称 vs 索引）
                    default_mismatch += 1
                else:
                    default_skipped += 1

                # 约束（仅对数值字段有意义）
                reg_min = reg_field.validation.get("min")
                reg_max = reg_field.validation.get("max")
                gen_min, gen_max = _generator_constraint(gen_field)
                if reg_min is None and reg_max is None and gen_min is None and gen_max is None:
                    pass  # 都没有
                elif reg_min is None and reg_max is None:
                    constraint_only_in_one += 1
                elif gen_min is None and gen_max is None:
                    constraint_only_in_one += 1
                elif reg_min == gen_min and reg_max == gen_max:
                    constraint_match += 1
                else:
                    constraint_mismatch += 1

                # 详细打印
                if not compatible or (reg_default is not None and str(reg_default) != str(gen_default)):
                    print(
                        f"  [~] {reg_key}: reg={reg_type}/{reg_default!r} | gen={gen_type}/{gen_default!r} | {reason}"
                    )

            # 3) generator 中有但 registry 缺失的字段
            for gen_key, _ in gen_by_key.items():
                if gen_key == "type":
                    continue
                if any(reg.key == f"{group_key}.{gen_key}" for reg in group.fields):
                    continue
                if any(reg.key == gen_key for reg in group.fields):
                    continue
                # 拼接
                full_key = f"{group_key}.{gen_key}" if not gen_key.startswith(group_key + ".") else gen_key
                extra_in_generator.append(full_key)
                print(f"  [+] {full_key}: generator 有 ({gen_by_key[gen_key]['type']}), registry 缺失")

            # 小计
            total_gen_fields += len(gen_fields)

        # -------------------------------------------------------------------
        # 汇总
        # -------------------------------------------------------------------
        print(_separator("汇总统计"))
        print(f"registry 字段总数:        {total_reg_fields}")
        print(f"generator 字段总数:       {total_gen_fields}")
        print(f"共同字段 (key 匹配):      {matched_keys}")
        print(f"  类型一致 (exact):       {type_exact}")
        print(f"  类型归一化 (float→number 等): {type_norm}")
        print(f"  generator 更严格 (Literal→select): {type_stricter}")
        print(f"  类型不兼容:              {type_mismatch}")
        print(f"默认值匹配:              {default_match}")
        print(f"默认值不一致:            {default_mismatch}")
        print(f"默认值跳过 (类型不可比):  {default_skipped}")
        print(f"约束一致:                {constraint_match}")
        print(f"约束不一致:              {constraint_mismatch}")
        print(f"约束只在一侧存在:        {constraint_only_in_one}")

        if missing_in_generator:
            print(_separator("Registry 有但 Generator 无的字段 (潜在覆盖缺口)"))
            for k in missing_in_generator:
                print(f"  - {k}")

        if extra_in_generator:
            print(_separator("Generator 有但 Registry 无的字段 (手写滞后)"))
            for k in extra_in_generator:
                print(f"  - {k}")

        if type_mismatch_details:
            print(_separator("类型不兼容的字段"))
            for line in type_mismatch_details:
                print(f"  - {line}")

        # 结论：关键指标
        # 1) 关键字段覆盖率：matched / total_reg
        key_coverage = (matched_keys / total_reg_fields) if total_reg_fields else 0
        # 2) 类型兼容率：compatible = exact + normalized + stricter; mismatch = type_mismatch
        total_compared = type_exact + type_norm + type_stricter + type_mismatch
        type_compat = (type_exact + type_norm + type_stricter) / total_compared if total_compared else 0
        # 3) 约束一致率（只统计 registry 显式声明了约束的）
        total_reg_constraint = constraint_match + constraint_mismatch
        constraint_match_rate = (constraint_match / total_reg_constraint) if total_reg_constraint else 0
        # 4) default 匹配率（不计入 skipped）
        total_default = default_match + default_mismatch
        default_match_rate = (default_match / total_default) if total_default else 0

        print(_separator("关键指标"))
        print(f"registry 关键字段覆盖率: {key_coverage:.4f}  ({matched_keys}/{total_reg_fields})")
        print(
            f"类型兼容率:              {type_compat:.4f}  ({type_exact + type_norm + type_stricter}/{total_compared})"
        )
        print(f"约束一致率:              {constraint_match_rate:.4f}  ({constraint_match}/{total_reg_constraint})")
        print(f"默认值匹配率:            {default_match_rate:.4f}  ({default_match}/{total_default})")
        print(_separator("结论"))
        print(
            "本对比仅做参考。说明:\n"
            "  - registry 是手写元数据，generator 是自动生成；\n"
            "  - 大多数不一致是 generator 比 registry 更精确（如 Literal→select）；\n"
            "  - 真正的任务是验证 generator 能完整覆盖 registry 字段、并提供更准确的类型。\n"
        )
        print(f"报告已写入: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
