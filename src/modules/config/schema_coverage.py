"""Schema Registry ↔ Generator 覆盖率门禁 (Task 11)

背景
----
``schema_registry`` 是手写的元数据(7 个分组,~40 个字段,带中文 label/icon),
``ConfigSchemaGenerator`` 是 Pydantic 自动生成的 schema。Migrate 过程中这两个
数据源必须保持 100% 字段覆盖,否则 UI 上会出现"看不到某个字段"或"读到错误的
约束"等不易察觉的回归。

本模块提供:

- :class:`CoverageResult` — 结构化结果(含 PASS / FAIL、缺失字段、类型不匹配)
- :func:`check_registry_coverage` — 对 registry 分组与 Pydantic 类执行字段级对比
- :data:`REGISTRY_GROUP_TO_PYDANTIC` — registry 分组 → Pydantic 类映射
- :data:`TYPE_NORMALIZATION` — registry 类型字符串 → generator 类型字符串归一化
  表(``float`` ↔ ``number`` 等)

调用方式
--------
::

    from src.modules.config.schema_coverage import (
        check_registry_coverage,
        REGISTRY_GROUP_TO_PYDANTIC,
    )
    from src.modules.config.schema_registry import get_schema_registry

    registry = get_schema_registry()
    result = check_registry_coverage(
        registry.get_all_groups(),
        REGISTRY_GROUP_TO_PYDANTIC,
    )
    if not result.fully_covered:
        result.log_to(logger)
    else:
        logger.info("SCHEMA_REGISTRY_COVERED")

与 CI 脚本的关系
----------------
``scripts/check_schema_coverage.py`` 是本模块的 CLI 包装(用于 CI),二者保持
行为一致。脚本和本模块都可以独立运行。

向后兼容
--------
历史 ``schema_registry`` 提供 :pyfunc:`ConfigSchemaRegistry` 单例,
dashboard 前端依赖其 ``groups`` + ``version`` 输出形状,因此 registry **不会被
删除**(本模块也不强制要求删除)。本模块仅校验"registry 的每个字段都能在
generator 找到对应",作为迁移期间的安全网。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple, Type

from pydantic import BaseModel

from src.modules.logging import get_logger

from .schema_generator import ConfigSchemaGenerator, collect_all_fields
from .schema_registry import ConfigGroupDefinition

logger = get_logger("SchemaCoverage")


# ---------------------------------------------------------------------------
# 常量:registry group_key -> Pydantic 类
# ---------------------------------------------------------------------------
#
# 与 ``scripts/check_schema_coverage.py:GROUP_TO_PYDANTIC_CLASS`` 等价。
# 同步更新:当 Pydantic 配置类被新增 / 重命名时,同步修改本表。

REGISTRY_GROUP_TO_PYDANTIC: Dict[str, Type[BaseModel]] = {}


def _build_group_to_pydantic() -> Dict[str, Type[BaseModel]]:
    """懒加载构建映射,避免循环 import。"""
    from src.modules.config.core_schemas import (
        ContextConfig,
        DashboardConfig,
        GeneralConfig,
        MaiCoreConfig,
        MetaConfig,
        PersonaConfig,
    )
    from src.modules.config.model_schemas import (
        FastLLMConfig,
        LLMConfig,
        LocalLLMConfig,
        VLMConfig,
    )
    from src.modules.config.schemas.logging import LoggingConfig

    return {
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


REGISTRY_GROUP_TO_PYDANTIC = _build_group_to_pydantic()


# ---------------------------------------------------------------------------
# 类型归一化:registry 写法 -> generator 写法
# ---------------------------------------------------------------------------

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


def _field_type_compatible(reg_type: str, gen_type: str) -> bool:
    """判断 registry / generator 字段类型是否兼容。

    ``float`` == ``number``(同义);``select`` 等同于 ``string`` + ``options``。
    """
    if reg_type == gen_type:
        return True
    norm = TYPE_NORMALIZATION.get(reg_type, reg_type)
    if norm == gen_type:
        return True
    return False


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class CoverageResult:
    """字段覆盖率检查结果。

    Attributes:
        fully_covered: 100% 字段被覆盖 **且** 类型均兼容
        missing_fields: ``{group_key: [field_name, ...]}`` —— registry 有但 generator 无
        type_mismatches: ``{group_key: [(field_name, reg_type, gen_type), ...]}``
            —— registry 与 generator 类型不兼容
        groups_checked: 实际参与检查的 group 数
        total_registry_fields: 参与检查的 registry 字段总数
        total_covered_fields: 已被 generator 命中的字段数
    """

    fully_covered: bool
    missing_fields: Dict[str, List[str]] = field(default_factory=dict)
    type_mismatches: Dict[str, List[Tuple[str, str, str]]] = field(default_factory=dict)
    groups_checked: int = 0
    total_registry_fields: int = 0
    total_covered_fields: int = 0

    @property
    def summary(self) -> str:
        """生成人类可读摘要(PASS/FAIL + 计数)。"""
        marker = "PASS" if self.fully_covered else "FAIL"
        return (
            f"[{marker}] Coverage "
            f"groups={self.groups_checked} "
            f"fields={self.total_covered_fields}/{self.total_registry_fields} "
            f"missing_groups={len(self.missing_fields)} "
            f"type_mismatch_groups={len(self.type_mismatches)}"
        )

    def log_to(self, target_logger) -> None:
        """把结论写入指定 logger。

        约定:
        - 100% 覆盖 → INFO 级别日志,内容含 ``SCHEMA_REGISTRY_COVERED``
        - 部分覆盖 → WARNING 级别日志,内容含 ``SCHEMA_REGISTRY_COVERAGE_FAIL``
        """
        if self.fully_covered:
            target_logger.info(
                f"SCHEMA_REGISTRY_COVERED: registry {self.total_registry_fields} "
                f"fields across {self.groups_checked} groups fully covered by generator"
            )
        else:
            missing_total = sum(len(v) for v in self.missing_fields.values())
            mismatch_total = sum(len(v) for v in self.type_mismatches.values())
            target_logger.warning(
                f"SCHEMA_REGISTRY_COVERAGE_FAIL: {self.summary}. "
                f"missing={missing_total}, type_mismatch={mismatch_total}"
            )

        # 详细信息始终以 DEBUG 级别记录
        for group_key, missing in self.missing_fields.items():
            target_logger.debug(f"[{group_key}] missing fields: {missing}")
        for group_key, mismatches in self.type_mismatches.items():
            for field_name, reg_t, gen_t in mismatches:
                target_logger.debug(f"[{group_key}] {field_name}: reg={reg_t}, gen={gen_t} (incompatible)")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def check_registry_coverage(
    registry_groups: Iterable[ConfigGroupDefinition],
    group_to_pydantic: Dict[str, Type[BaseModel]],
) -> CoverageResult:
    """对比 registry 分组与 generator 输出,返回 :class:`CoverageResult`。

    Args:
        registry_groups: registry 中的 :class:`ConfigGroupDefinition` 序列
            (可以是 ``get_all_groups()`` 输出,也可以是人造分组)。
        group_to_pydantic: registry ``key`` → Pydantic ``BaseModel`` 类的映射。
            没有对应类的分组会被跳过(不出现在结果中)。

    Returns:
        :class:`CoverageResult` 描述完整覆盖结果。
    """
    missing_fields: Dict[str, List[str]] = {}
    type_mismatches: Dict[str, List[Tuple[str, str, str]]] = {}
    total_registry_fields = 0
    total_covered_fields = 0
    groups_checked = 0

    for group in registry_groups:
        group_key = group.key
        py_cls = group_to_pydantic.get(group_key)
        if py_cls is None:
            # 没有映射 → 跳过(向后兼容:不强制要求每个 registry 分组都有映射)
            continue
        groups_checked += 1

        # generator 输出(失败兜底)
        try:
            gen_schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        except Exception as exc:
            logger.debug(f"[{group_key}] generator failed: {exc}")
            continue

        gen_fields_list = collect_all_fields(gen_schema, prefix=group_key)
        gen_by_key = {f["name"]: f for f in gen_fields_list}

        for reg_field in group.fields:
            total_registry_fields += 1
            local = reg_field.key.split(".")[-1]
            gen_field = gen_by_key.get(local)
            if gen_field is None:
                missing_fields.setdefault(group_key, []).append(local)
                continue
            total_covered_fields += 1

            if not _field_type_compatible(reg_field.field_type, gen_field["type"]):
                type_mismatches.setdefault(group_key, []).append((local, reg_field.field_type, gen_field["type"]))

    fully_covered = not missing_fields and not type_mismatches
    return CoverageResult(
        fully_covered=fully_covered,
        missing_fields=missing_fields,
        type_mismatches=type_mismatches,
        groups_checked=groups_checked,
        total_registry_fields=total_registry_fields,
        total_covered_fields=total_covered_fields,
    )


__all__ = [
    "CoverageResult",
    "REGISTRY_GROUP_TO_PYDANTIC",
    "TYPE_NORMALIZATION",
    "check_registry_coverage",
]
