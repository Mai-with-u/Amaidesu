"""
POC Schema Diff - Task 4 (Dashboard 前端 schema 兼容性验证)

目标
----
对比 ``schema_registry.py`` (手写 Schema, Dashboard 当前依赖) 与
``schema_generator.py`` (Pydantic 自动生成, Task 1 产出) 的输出,
**重点分析前端会遇到的兼容性问题**。

注意: 本脚本**不修改任何项目内代码**, 仅做只读分析和报告。

对比三个层级的 schema 形状:

1. **Top-level (API 响应根)** — 决定前端 store 能否解析响应
   - registry: ``{"groups": [...], "version": "1.0.0"}``
   - generator: ``{"className": ..., "classDoc": ..., "fields": [...], "nested": {...}}``

2. **Group/Class 层级** — 决定侧边栏导航能否渲染
   - registry: 每个 group 带 ``key`` / ``label`` / ``icon`` / ``order`` / 扁平 ``fields``
   - generator: 每个 class 带 ``className`` / ``classDoc`` / ``fields`` + ``nested`` 子类

3. **Field 层级** — 决定 FieldRenderer 能否渲染每个输入控件
   - registry: ``{key, label, type, default, value, validation, required, sensitive, properties, items, group}``
   - generator: ``{name, type, label: {zh_CN: ...}, description, default, required, minValue, maxValue, options, items}``

输出
----
- ``.omo/evidence/task-4-schema-diff.txt`` — 完整 diff 报告 (含 breaking changes 清单)

设计原则
--------
- 字段类型映射: ``number`` (generator) ↔ ``float`` (frontend/registry)
- 验证字段映射: ``minValue/maxValue`` (generator) ↔ ``validation.min/max`` (frontend)
- label 映射: ``label: {zh_CN: ...}`` (generator) ↔ ``label: "..."`` (frontend)
- 字段名映射: ``name`` (generator) ↔ ``key`` (frontend, 带 group 前缀)
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径与编码
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


# ---------------------------------------------------------------------------
# registry / generator / pydantic 模型导入
# ---------------------------------------------------------------------------

from src.modules.config.schema_generator import (  # noqa: E402
    ConfigSchemaGenerator,
    collect_all_fields,
)
from src.modules.config.schema_registry import get_schema_registry  # noqa: E402
from src.modules.config.core_schemas import (  # noqa: E402
    DashboardConfig,
    GeneralConfig,
    MaiCoreConfig,
    MetaConfig,
    PersonaConfig,
    ContextConfig,
)
from src.modules.config.model_schemas import (  # noqa: E402
    LLMConfig,
    FastLLMConfig,
    VLMConfig,
    LocalLLMConfig,
)
from src.modules.config.schemas.logging import LoggingConfig as CoreLoggingConfig  # noqa: E402

# ---------------------------------------------------------------------------
# registry group -> pydantic class 映射
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 输出捕获 (tee 到 stdout + 文件)
# ---------------------------------------------------------------------------


@contextmanager
def tee_output(path: Path):
    """stdout/stderr 同时输出到文件。"""
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
# 报告工具
# ---------------------------------------------------------------------------


def _sep(title: str = "") -> str:
    line = "=" * 78
    if title:
        return f"\n{line}\n{title}\n{line}"
    return line


def _sub_sep(title: str = "") -> str:
    line = "-" * 78
    if title:
        return f"\n{line}\n{title}\n{line}"
    return line


# ---------------------------------------------------------------------------
# 转换辅助: generator 输出 → "前端期望格式" (推测适配层需要的格式)
# ---------------------------------------------------------------------------

# generator 字段类型 → 前端实际使用的类型
GEN_TYPE_TO_FRONTEND_TYPE = {
    "string": "string",
    "integer": "integer",
    "number": "float",  # 关键差异: generator 用 "number", frontend/registry 用 "float"
    "boolean": "boolean",
    "select": "select",
    "array": "array",
    "object": "object",
}


def _generator_field_to_frontend_shape(
    gen_field: Dict[str, Any],
    group_key: str,
    field_path: str,
) -> Dict[str, Any]:
    """将 generator 的字段 schema 转换为前端 ConfigFieldSchema 期望的格式。

    仅做形状映射，不丢失任何信息。
    """
    raw_type = gen_field.get("type", "string")
    fe_type = GEN_TYPE_TO_FRONTEND_TYPE.get(raw_type, raw_type)

    fe_schema: Dict[str, Any] = {
        "key": field_path,
        "label": gen_field.get("label", {}).get("zh_CN", gen_field.get("name", "")),
        "description": gen_field.get("description", ""),
        "type": fe_type,
        "default": gen_field.get("default"),
        "value": gen_field.get("default"),
        "required": gen_field.get("required", False),
        # generator 没有 sensitive 字段 (Pydantic Field 不存 metadata)
        "sensitive": False,
        # validation 字段映射: minValue → validation.min, maxValue → validation.max
        "validation": {},
        "group": group_key,
    }

    # constraints → validation
    if "minValue" in gen_field or "maxValue" in gen_field:
        fe_schema["validation"] = {
            "min": gen_field.get("minValue"),
            "max": gen_field.get("maxValue"),
        }
    if "minLength" in gen_field or "maxLength" in gen_field:
        fe_schema["validation"].update(
            {
                "min_length": gen_field.get("minLength"),
                "max_length": gen_field.get("maxLength"),
            }
        )
    if "pattern" in gen_field:
        fe_schema["validation"]["pattern"] = gen_field["pattern"]
    if "options" in gen_field:
        fe_schema["validation"]["options"] = gen_field["options"]

    if "items" in gen_field:
        item_raw_type = gen_field["items"].get("type", "string")
        fe_schema["items"] = {
            "type": GEN_TYPE_TO_FRONTEND_TYPE.get(item_raw_type, item_raw_type),
        }

    return fe_schema


def _generator_class_to_frontend_group(
    cls: type,
    schema: Dict[str, Any],
    group_key: str,
    registry_group: Optional[Any] = None,
) -> Dict[str, Any]:
    """将 generator 的 class schema 转成前端 ConfigGroupSchema 期望的格式。"""
    # 从 registry 复制 icon / label / order (generator 不产生这些)
    label = registry_group.label if registry_group else cls.__name__
    icon = registry_group.icon if registry_group else ""
    description = (schema.get("classDoc") or "").strip() or (registry_group.description if registry_group else "")
    order = registry_group.order if registry_group else 0

    # flatten fields
    flat = collect_all_fields(schema, prefix=group_key)
    fe_fields = []
    for f in flat:
        # name is just the field name; build dotted path
        local_path = f["key"]  # already includes group_key prefix from collect_all_fields
        fe_fields.append(_generator_field_to_frontend_shape(f, group_key, local_path))

    return {
        "key": group_key,
        "label": label,
        "description": description,
        "icon": icon,
        "order": order,
        "fields": fe_fields,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    evidence_dir = ROOT / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output_path = evidence_dir / "task-4-schema-diff.txt"

    # 收集所有 divergences
    divergences: List[Dict[str, Any]] = []

    with tee_output(output_path) as _:
        print(_sep("POC Schema Diff - Task 4 (Dashboard 前端 schema 兼容性验证)"))
        print("对比对象:")
        print("  - registry: src.modules.config.schema_registry.ConfigSchemaRegistry (手写 7 groups)")
        print("  - generator: src.modules.config.schema_generator.ConfigSchemaGenerator (Pydantic 自动生成)")
        print("  - 前端期望: dashboard/src/types/settings.ts + dashboard/src/components/settings/FieldRenderer.vue")
        print()
        print("三层对比:")
        print("  1) API 响应根结构 (top-level)")
        print("  2) Group/Class 层级 (侧边栏导航)")
        print("  3) Field 层级 (表单输入渲染)")

        # =====================================================================
        # 第一部分: API 响应根结构对比
        # =====================================================================
        print(_sep("Part 1: API 响应根结构对比"))
        print()
        print("Dashboard API: GET /api/v1/config/schema")
        print("当前实现: src/modules/dashboard/api/config.py:get_config_schema()")
        print("调用源: registry.get_schema_for_config(config)")
        print()

        # registry 实际 API 输出 (无 config 时)
        registry = get_schema_registry()
        reg_response = registry.get_schema_for_config({})

        print("Registry 实际输出 (顶部截取):")
        reg_top_keys = sorted(reg_response.keys())
        print(f"  顶层 keys: {reg_top_keys}")
        if "groups" in reg_response:
            print(f"  groups 数量: {len(reg_response['groups'])}")
            print(f"  version: {reg_response.get('version')!r}")
            first_group = reg_response["groups"][0] if reg_response["groups"] else {}
            print(f"  第一个 group keys: {sorted(first_group.keys())}")
            if first_group.get("fields"):
                print(f"  第一个 group 的 field keys: {sorted(first_group['fields'][0].keys())}")

        print()
        print("Generator 实际输出 (CoreConfig):")
        gen_core = ConfigSchemaGenerator.generate_config_schema(GeneralConfig.__mro__[1])  # type: ignore
        # 上面只是为了占位，使用真实的 CoreConfig
        from src.modules.config.core_schemas import CoreConfig

        gen_core = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
        gen_top_keys = sorted(gen_core.keys())
        print(f"  顶层 keys: {gen_top_keys}")
        print(f"  fields 数量: {len(gen_core.get('fields', []))}")
        print(f"  nested 数量: {len(gen_core.get('nested', {}))}")
        if gen_core.get("nested"):
            print(f"  nested 子类: {sorted(gen_core['nested'].keys())}")
        print(f"  uiAdvanced: {gen_core.get('uiAdvanced')}")

        # 顶层对比
        print()
        print("差异:")
        print("  - registry 顶层 keys: ['groups', 'version']")
        print("  - generator 顶层 keys: ['className', 'classDoc', 'fields', 'nested', 'uiAdvanced']")
        print("  - 完全不兼容: 前端 store (settings.ts) 直接读取 response.groups, 无 'groups' 字段将崩溃")
        divergences.append(
            {
                "scope": "API 响应根",
                "field": "顶层结构",
                "registry": "{groups: [...], version: '1.0.0'}",
                "generator": "{className, classDoc, fields, nested, uiAdvanced}",
                "severity": "BREAKING",
                "frontend_impact": "Settings.vue 调用 settingsStore.fetchSchema() → schema.value.groups → undefined → 侧边栏为空",
            }
        )

        # =====================================================================
        # 第二部分: Group 层级对比
        # =====================================================================
        print(_sep("Part 2: Group / Class 层级对比"))
        print()
        print("Registry Group 实际形状 (registry.get_schema_for_config({})['groups'][0]):")
        if reg_response["groups"]:
            print(json.dumps(reg_response["groups"][0], ensure_ascii=False, indent=2))

        print()
        print("Generator Class 实际形状 (PersonaConfig.generate_config_schema()):")
        gen_persona = ConfigSchemaGenerator.generate_config_schema(PersonaConfig)
        # 移除 fields/nested 细节，便于查看顶层形状
        gen_persona_shape = {k: v for k, v in gen_persona.items() if k != "fields"}
        gen_persona_shape["fields_count"] = len(gen_persona["fields"])
        gen_persona_shape["nested_keys"] = list(gen_persona.get("nested", {}).keys())
        print(json.dumps(gen_persona_shape, ensure_ascii=False, indent=2))

        print()
        print("Group/Class 层级差异:")
        for f in [
            ("key", "registry 有", "generator 无 (只有 className)"),
            ("label", "registry 有", "generator 无 (label 是字段级 dict)"),
            ("icon", "registry 有 (e.g. 'Setting', 'User')", "generator 无 (无 UI 元数据)"),
            ("order", "registry 有 (排序优先级)", "generator 无"),
            ("className", "registry 无", "generator 有 (Python 类名)"),
            ("classDoc", "registry 无", "generator 有 (类文档字符串)"),
            ("uiAdvanced", "registry 无", "generator 有 (Pydantic 未设，默认 false)"),
        ]:
            print(f"  - {f[0]}: {f[1]} | {f[2]}")
            divergences.append(
                {
                    "scope": "Group 层级",
                    "field": f[0],
                    "registry": f[1],
                    "generator": f[2],
                    "severity": "MEDIUM",
                    "frontend_impact": f"Settings.vue 用 group.{f[0]} 渲染; generator 缺失 → 图标/排序失效"
                    if f[0] in {"key", "label", "icon", "order"}
                    else "Settings.vue 不直接使用 className/classDoc/uiAdvanced, 仅在调试时有用",
                }
            )

        # =====================================================================
        # 第三部分: Field 层级对比 (per group)
        # =====================================================================
        print(_sep("Part 3: Field 层级逐字段对比"))

        for group in registry.get_all_groups():
            group_key = group.key
            pyd_cls = GROUP_TO_PYDANTIC_CLASS.get(group_key)
            print(_sub_sep(f"Group: {group_key} ({group.label})"))

            if pyd_cls is None:
                print(f"  [SKIP] registry group {group_key} 无对应 Pydantic 类")
                continue

            # generator 输出
            try:
                gen_schema = ConfigSchemaGenerator.generate_config_schema(pyd_cls)
            except Exception as exc:
                print(f"  [ERROR] generator 失败: {exc}")
                continue

            gen_fields = collect_all_fields(gen_schema, prefix=group_key)
            gen_by_key: Dict[str, Dict[str, Any]] = {}
            for f in gen_fields:
                # name 是 generator 字段名 (e.g., "platform_id")
                # key 是带 group_key 前缀 (e.g., "general.platform_id")
                gen_by_key[f["key"]] = f

            print(f"  registry 字段: {len(group.fields)} | generator 字段: {len(gen_fields)}")

            # 转换 registry 字段为标准化 dict
            reg_by_key: Dict[str, Dict[str, Any]] = {}
            for reg_field in group.fields:
                reg_by_key[reg_field.key] = reg_field.to_dict()

            # 1) registry 有但 generator 无
            print()
            print("  --- registry 有, generator 无 ---")
            reg_only = [k for k in reg_by_key if k not in gen_by_key]
            if reg_only:
                for k in reg_only:
                    rf = reg_by_key[k]
                    print(
                        f"  [-] {k}: type={rf['type']}, default={rf['default']!r}, "
                        f"required={rf.get('required', False)}, sensitive={rf.get('sensitive', False)}"
                    )
                    divergences.append(
                        {
                            "scope": f"Field[{group_key}]",
                            "field": k,
                            "registry": rf["type"],
                            "generator": "<MISSING>",
                            "severity": "BREAKING",
                            "frontend_impact": "前端 fetchSchema 后该字段不出现在 UI, 用户无法编辑",
                        }
                    )
            else:
                print("  (无)")

            # 2) generator 有但 registry 无
            print()
            print("  --- generator 有, registry 无 ---")
            gen_only = [k for k in gen_by_key if k not in reg_by_key]
            if gen_only:
                for k in gen_only:
                    gf = gen_by_key[k]
                    print(
                        f"  [+] {k}: type={gf['type']}, default={gf.get('default')!r}, "
                        f"required={gf.get('required', False)}"
                    )
                    divergences.append(
                        {
                            "scope": f"Field[{group_key}]",
                            "field": k,
                            "registry": "<MISSING>",
                            "generator": gf["type"],
                            "severity": "INFO",
                            "frontend_impact": "前端 fetchSchema 后多出新字段, 用户可见可编辑 (一般无副作用, 仅扩展 UI)",
                        }
                    )
            else:
                print("  (无)")

            # 3) 共有字段的详细 diff
            print()
            print("  --- 共有字段 side-by-side diff ---")
            common = sorted(set(reg_by_key) & set(gen_by_key))
            for k in common:
                rf = reg_by_key[k]
                gf = gen_by_key[k]

                # 类型对比 (考虑 number ↔ float 归一化)
                rt = rf["type"]
                gt = gf["type"]
                gt_norm = "float" if gt == "number" else gt

                # 类型差异
                type_diff = False
                if rt != gt and rt != gt_norm:
                    type_diff = True
                elif rt == "select" and gt == "string":
                    # registry 用 select (枚举), generator 退化为 string (无 Literal 约束)
                    type_diff = True

                # default 差异
                rd = rf.get("default")
                gd = gf.get("default")
                default_diff = (str(rd) != str(gd)) and not (rd is None and gd is None)

                # required 差异
                req_diff = bool(rf.get("required", False)) != bool(gf.get("required", False))

                # validation 差异 (registry.validation.{min,max,options} vs gen.minValue/maxValue/options)
                reg_val = rf.get("validation") or {}
                reg_min = reg_val.get("min")
                reg_max = reg_val.get("max")
                reg_options = reg_val.get("options")
                gen_min = gf.get("minValue")
                gen_max = gf.get("maxValue")
                gen_options = gf.get("options")

                val_diff = False
                val_details = []
                if reg_min != gen_min:
                    val_diff = True
                    if reg_min is not None or gen_min is not None:
                        val_details.append(f"min: reg={reg_min} gen={gen_min}")
                if reg_max != gen_max:
                    val_diff = True
                    if reg_max is not None or gen_max is not None:
                        val_details.append(f"max: reg={reg_max} gen={gen_max}")
                if (reg_options or []) != (gen_options or []):
                    val_diff = True
                    val_details.append(f"options: reg={reg_options} gen={gen_options}")

                # sensitive: registry 有, generator 无
                sensitive_diff = rf.get("sensitive", False)  # 只在 registry=True 时记

                # 描述差异
                desc_diff = (rf.get("description") or "") != (gf.get("description") or "")

                if type_diff or default_diff or req_diff or val_diff or sensitive_diff or desc_diff:
                    print(f"  [~] {k}")
                    print(
                        f"      registry : type={rt}, default={rd!r}, required={rf.get('required', False)}, "
                        f"sensitive={rf.get('sensitive', False)}, "
                        f"validation={reg_val if reg_val else '{}'}"
                    )
                    print(
                        f"      generator: type={gt}, default={gd!r}, required={gf.get('required', False)}, "
                        f"sensitive=N/A, "
                        f"minValue={gen_min}, maxValue={gen_max}, options={gen_options}"
                    )
                    if desc_diff:
                        print(f"      [desc]  reg: {rf.get('description') or ''!r}")
                        print(f"              gen: {gf.get('description') or ''!r}")

                    # 收集 divergences
                    if type_diff:
                        divergences.append(
                            {
                                "scope": f"Field[{group_key}]",
                                "field": k,
                                "aspect": "type",
                                "registry": rt,
                                "generator": gt,
                                "severity": "HIGH" if rt in {"select", "integer"} and gt != rt else "MEDIUM",
                                "frontend_impact": "FieldRenderer 根据 type 派发: 'string' → el-input, 'float' → el-input-number step=0.1, 'select' → el-select. 类型变化影响渲染",
                            }
                        )
                    if default_diff:
                        divergences.append(
                            {
                                "scope": f"Field[{group_key}]",
                                "field": k,
                                "aspect": "default",
                                "registry": repr(rd),
                                "generator": repr(gd),
                                "severity": "MEDIUM",
                                "frontend_impact": "前端显示的默认值与手写文档不一致, 可能误导用户",
                            }
                        )
                    if req_diff:
                        divergences.append(
                            {
                                "scope": f"Field[{group_key}]",
                                "field": k,
                                "aspect": "required",
                                "registry": rf.get("required", False),
                                "generator": gf.get("required", False),
                                "severity": "MEDIUM",
                                "frontend_impact": "前端表单星号 * 标记 / 必填校验会不一致",
                            }
                        )
                    if val_diff:
                        for det in val_details:
                            divergences.append(
                                {
                                    "scope": f"Field[{group_key}]",
                                    "field": k,
                                    "aspect": det,
                                    "registry": "见字段",
                                    "generator": "见字段",
                                    "severity": "MEDIUM",
                                    "frontend_impact": "前端 el-input-number 的 min/max, el-select 的 options 失效或范围错误",
                                }
                            )
                    if sensitive_diff:
                        divergences.append(
                            {
                                "scope": f"Field[{group_key}]",
                                "field": k,
                                "aspect": "sensitive",
                                "registry": True,
                                "generator": False,
                                "severity": "HIGH",
                                "frontend_impact": "敏感字段 (API key, token) 将以明文输入框展示, 安全风险",
                            }
                        )
                    if desc_diff:
                        divergences.append(
                            {
                                "scope": f"Field[{group_key}]",
                                "field": k,
                                "aspect": "description",
                                "registry": (rf.get("description") or "")[:50],
                                "generator": (gf.get("description") or "")[:50],
                                "severity": "LOW",
                                "frontend_impact": "前端字段描述文案变化 (用户可见, 通常无功能影响)",
                            }
                        )

        # =====================================================================
        # 第四部分: 模型级根聚合对比 (CoreConfig / ModelConfig)
        # =====================================================================
        print(_sep("Part 4: 模型级根聚合对比 (CoreConfig / ModelConfig)"))
        print()
        print("Dashboard 当前 API **不会** 调用 generator, 也不会返回 CoreConfig/ModelConfig.")
        print("下面对比仅做参考: 如果将来要扩展 API 输出整个配置根, 这些是 generator 会产生的根.")

        from src.modules.config.core_schemas import CoreConfig
        from src.modules.config.model_schemas import ModelConfig

        for root_name, root_cls in [("CoreConfig", CoreConfig), ("ModelConfig", ModelConfig)]:
            print(_sub_sep(f"{root_name}"))
            schema = ConfigSchemaGenerator.generate_config_schema(root_cls)
            print(f"  className: {schema['className']}")
            print(f"  fields (顶层): {len(schema['fields'])}")
            for f in schema["fields"]:
                print(f"    - {f['name']}: type={f['type']}, default={f.get('default')!r}")
            print(f"  nested (子配置类): {sorted(schema['nested'].keys())}")
            for nested_name, nested in schema["nested"].items():
                print(f"    [{nested_name}] fields: {[f['name'] for f in nested['fields']]}")

        # =====================================================================
        # 第五部分: 前端期望的 schema endpoint 当前状态
        # =====================================================================
        print(_sep("Part 5: Schema API Endpoint 当前状态"))
        print()
        print("Dashboard API 路由: GET /api/v1/config/schema")
        print("实现位置: src/modules/dashboard/api/config.py:get_config_schema() (line 112-161)")
        print()
        print("API 调用源:")
        print("  registry = get_schema_registry()")
        print("  schema_data = registry.get_schema_for_config(config)")
        print("  → 100% 依赖 registry, 0% 使用 generator")
        print()
        print("API 当前返回 (前端 settings.ts:fetchSchema):")
        print("  {")
        print("    groups: [{key, label, description, icon, order, fields: [...]}],")
        print('    version: "1.0.0"')
        print("  }")
        print()
        print("如切换到 generator, 需改造:")
        print("  1) generator 输出嵌套 {className, fields, nested}, 需包装为 {groups: [...]}")
        print("  2) generator 字段用 'name', 前端期望 'key' (点号路径)")
        print("  3) generator label 是 {zh_CN: ...}, 前端期望 string")
        print("  4) generator 类型 'number' → 前端 'float' (FieldRenderer 不识别 'number')")
        print("  5) generator 约束 minValue/maxValue → validation.min/max")
        print("  6) generator 无 sensitive 字段, 需从 Pydantic Field 约定提取 (如 alias='password')")
        print("  7) generator 无 icon / order, 需从 group 约定 (e.g., __ui_icon__) 提取或硬编码")

        # =====================================================================
        # 第六部分: 汇总
        # =====================================================================
        print(_sep("Part 6: Divergence 汇总"))

        # 按 severity 分组
        by_severity: Dict[str, List[Dict[str, Any]]] = {}
        for d in divergences:
            by_severity.setdefault(d["severity"], []).append(d)

        for sev in ["BREAKING", "HIGH", "MEDIUM", "LOW", "INFO"]:
            items = by_severity.get(sev, [])
            if not items:
                continue
            print(f"\n  [{sev}] ({len(items)} 项):")
            for d in items:
                field = d.get("field", "")
                aspect = d.get("aspect", "")
                ident = f"{field}.{aspect}" if aspect else field
                print(f"    - [{d['scope']}] {ident}")
                print(f"        registry : {d['registry']}")
                print(f"        generator: {d['generator']}")
                print(f"        impact   : {d['frontend_impact']}")

        # =====================================================================
        # 第七部分: 结论 & 风险清单
        # =====================================================================
        print(_sep("Part 7: 结论 & 风险清单"))

        breaking_count = len(by_severity.get("BREAKING", []))
        high_count = len(by_severity.get("HIGH", []))
        medium_count = len(by_severity.get("MEDIUM", []))
        low_count = len(by_severity.get("LOW", []))
        info_count = len(by_severity.get("INFO", []))

        print()
        print("统计:")
        print(f"  BREAKING : {breaking_count}")
        print(f"  HIGH     : {high_count}")
        print(f"  MEDIUM   : {medium_count}")
        print(f"  LOW      : {low_count}")
        print(f"  INFO     : {info_count}")
        print(f"  合计     : {len(divergences)}")

        print()
        print("前置条件: 直接用 generator 输出替换 API endpoint → 前端立即崩溃 (BREAKING 结构变化).")
        print()
        print("必要的中间层 (适配器) 改造:")
        print("  1. 实现 Generator → Frontend shape adapter:")
        print("     - 嵌套 {className, fields, nested} → 扁平 {groups: [...], fields: dotted-key}")
        print("     - type 'number' → 'float'")
        print("     - label {zh_CN} → label string")
        print("     - minValue/maxValue → validation.min/max")
        print("  2. 元数据扩展 (Pydantic Field):")
        print("     - 通过 json_schema_extra 注入 x-icon, x-label, x-order, x-sensitive")
        print("     - 或在 BaseConfig 基类添加 __ui_icon__ / __ui_label__ 等约定 (类似 MaiBot)")
        print("  3. registry 字段全部迁移到 Pydantic 模型:")
        print("     - 当前 registry 用 select 标注的字段 (llm.client, logging.level 等),")
        print("       generator 仅在 Pydantic 用 Literal[...] 时才输出 select")
        print("     - 需要把这些字段类型改为 Literal[...], 才会被 generator 正确识别")
        print("  4. sensitive 字段提取:")
        print("     - 当前 Pydantic 模型无 'sensitive' 概念")
        print("     - 需要约定: 字段名包含 'token'/'api_key'/'password' → sensitive=True")
        print("       或通过 json_schema_extra={'x-sensitive': True}")
        print()
        print("判定: 直接切换 = BREAKING, 需要先实现适配层. POC 验证完成.")
        print()
        print(f"报告已写入: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
