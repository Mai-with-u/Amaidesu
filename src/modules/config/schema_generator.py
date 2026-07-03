"""
POC Schema Generator — 从 Pydantic Config 类自动生成 UI Schema

背景
----
本模块是 MaiBot v1.0.0 ``ConfigSchemaGenerator`` 的 Amaidesu 适配版 (POC)。
MaiBot 的原版依赖其自研 ``ConfigBase``，本版本改用 Pydantic v2 BaseModel (含 Amaidesu 的
``BaseConfig`` 子类)，遵循 Amaidesu 项目的现有约束：

- 不修改既有 Pydantic 模型或业务逻辑
- 不引入 MaiBot 的运行时依赖
- 输出 dict 形状与 MaiBot 原版兼容，便于后续对接 Dashboard / WebUI

输出 Schema 形状 (与 MaiBot 对齐)
--------------------------------
::

    {
        "className": "PersonaConfig",
        "classDoc": "...",
        "fields": [
            {"name": "bot_name", "type": "string", "label": {"zh_CN": "bot_name"},
             "description": "...", "required": True, "default": "麦麦",
             "minValue": ..., "maxValue": ..., "options": [...], "items": {...}}
        ],
        "nested": {  # 仅 BaseConfig/BaseModel 子字段展开
            "persona": { ... PersonaConfig 的 schema ... }
        }
    }

POC 限制 (后续替换 schema_registry 时需要扩展)
----------------------------------------------
- 仅支持 Pydantic v2 Field 约束 (ge, le, gt, lt, pattern, min_length, max_length)。
- ``__ui_parent__`` / ``__ui_label__`` / ``__ui_advanced__`` 等 MaiBot UI 标记，Amaidesu
  项目当前未使用，保留读取能力但不影响输出。
- ``AMemorix*`` 可见性策略来自 MaiBot 私域逻辑，本版本不做平移。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Union, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from src.modules.logging import get_logger

logger = get_logger("ConfigSchemaGenerator")


# ---------------------------------------------------------------------------
# 内部工具：拆解注解（处理 Optional[X] / Union[X, None]）
# ---------------------------------------------------------------------------

_NONE_TYPE = type(None)


def _unwrap_optional(annotation: Any) -> Any:
    """剥离 ``Optional[X]`` / ``Union[X, None]`` 包装。

    保留原始 ``Literal`` / ``List`` 嵌套结构。
    """
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    if origin is Union:
        non_none = [a for a in args if a is not _NONE_TYPE]
        if len(non_none) == 1:
            return non_none[0]
        return annotation
    return annotation


def _is_basemodel_subclass(annotation: Any) -> bool:
    """判断注解是否是 BaseModel 子类（处理 string annotations）。"""
    if annotation is None:
        return False
    if not isinstance(annotation, type):
        return False
    try:
        return issubclass(annotation, BaseModel)
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# ConfigSchemaGenerator
# ---------------------------------------------------------------------------


class ConfigSchemaGenerator:
    """将 Pydantic 配置类转为 UI 友好的 schema dict。"""

    # ----- 字段文档（兼容 MaiBot 接口） ------------------------------------
    @staticmethod
    @lru_cache(maxsize=None)
    def _get_class_field_docs(config_class: type) -> Dict[str, str]:
        """提取字段文档（兼容 MaiBot ``ConfigBase.get_class_field_docs``）。"""
        getter = getattr(config_class, "get_class_field_docs", None)
        if callable(getter):
            try:
                result = getter()
                # MaiBot 的 ConfigBase 可能返回 dict[bytes, bytes]，统一转 str
                if isinstance(result, dict):
                    return {str(k): str(v) for k, v in result.items()}
            except Exception as exc:
                logger.debug(f"调用 {config_class.__name__}.get_class_field_docs() 失败: {exc}")
        return {}

    @staticmethod
    def _build_label(label: str) -> Dict[str, str]:
        """构造多语言标签 dict（与 MaiBot 形状一致）。"""
        return {"zh_CN": label}

    # ----- 类型映射 ----------------------------------------------------------
    @classmethod
    def _map_field_type(cls, annotation: Any) -> str:
        """把 Python 类型注解映射为 UI 类型字符串。

        返回值集合（与 MaiBot 对齐）：``string``、``integer``、``number``、
        ``boolean``、``array``、``object``、``select``。
        """
        # 1. Optional[X] 剥皮
        unwrapped = _unwrap_optional(annotation)
        if unwrapped is not annotation:
            return cls._map_field_type(unwrapped)

        origin = get_origin(unwrapped)

        # Literal[...] -> select
        if origin is not None and str(origin) == "typing.Literal":
            return "select"

        # list / set / tuple -> array
        if origin in {list, set, tuple}:
            return "array"

        # dict -> object（保留 key 信息已由 nested 接管）
        if origin in {dict}:
            return "object"

        # Pydantic / BaseModel 子类 -> object
        if _is_basemodel_subclass(unwrapped):
            return "object"

        # 基础类型
        if unwrapped is bool:
            return "boolean"
        if unwrapped is int:
            return "integer"
        if unwrapped is float:
            return "number"
        if unwrapped is str:
            return "string"

        # 兜底
        return "string"

    # ----- 选项 / 约束 -------------------------------------------------------
    @staticmethod
    def _extract_options(annotation: Any) -> Optional[List[str]]:
        """从 ``Literal[...]`` / ``Optional[Literal[...]]`` 中枚举选项。"""
        unwrapped = _unwrap_optional(annotation)
        origin = get_origin(unwrapped)
        if origin is None:
            return None
        if str(origin) != "typing.Literal":
            return None
        args = get_args(unwrapped)
        options = [str(item) for item in args]
        return options or None

    @staticmethod
    def _extract_constraints(field_info: FieldInfo) -> Dict[str, Any]:
        """提取 Pydantic Field 的约束为 UI 字段。

        支持：
        - ``ge`` / ``le`` -> ``minValue`` / ``maxValue``
        - ``gt`` / ``lt`` -> ``exclusiveMinValue`` / ``exclusiveMaxValue`` (前端约定)
        - ``multiple_of`` -> ``step`` (UI 步长，兼容旧 ``multipleOf``)
        - ``pattern`` -> ``pattern``
        - ``min_length`` / ``max_length`` -> ``minLength`` / ``maxLength``
        """
        result: Dict[str, Any] = {}
        metadata = getattr(field_info, "metadata", None) or []
        for constraint in metadata:
            # Pydantic v2 使用 annotated_types 实现 Ge/Gt/Le/Lt
            ge = getattr(constraint, "ge", None)
            if ge is not None and not isinstance(ge, type(lambda: None)):
                result["minValue"] = ge
            le = getattr(constraint, "le", None)
            if le is not None and not isinstance(le, type(lambda: None)):
                result["maxValue"] = le
            gt = getattr(constraint, "gt", None)
            if gt is not None and not isinstance(gt, type(lambda: None)):
                result["exclusiveMinValue"] = gt
            lt = getattr(constraint, "lt", None)
            if lt is not None and not isinstance(lt, type(lambda: None)):
                result["exclusiveMaxValue"] = lt
            multiple = getattr(constraint, "multiple_of", None)
            if multiple is not None:
                # ``step`` 是 UI 数字输入框的标准属性；同时保留 ``multipleOf`` 以兼容旧前端
                result["step"] = multiple
                result["multipleOf"] = multiple
            # StringConstraints 在 annotated_types 中
            min_length = getattr(constraint, "min_length", None)
            if min_length is not None:
                result["minLength"] = min_length
            max_length = getattr(constraint, "max_length", None)
            if max_length is not None:
                result["maxLength"] = max_length
            # StrSchema 模式
            pattern = getattr(constraint, "pattern", None)
            if pattern is not None:
                result["pattern"] = pattern

        # 兼容 Pydantic v2 中 ``Field(pattern=...)`` 直接放在 metadata 的情况
        if "pattern" not in result:
            for constraint in metadata:
                pattern = getattr(constraint, "pattern", None)
                if pattern:
                    result["pattern"] = pattern
                    break

        return result

    # ----- 单字段 schema -----------------------------------------------------
    @classmethod
    def _build_field_schema(
        cls,
        config_class: type,
        field_name: str,
        annotation: Any,
        field_info: FieldInfo,
        field_docs: Dict[str, str],
    ) -> Dict[str, Any]:
        """构造单个字段的 UI schema。"""
        field_type = cls._map_field_type(annotation)
        raw_description = field_docs.get(field_name, field_info.description or "")
        # _wrap_ 标记来自 MaiBot docstring 约定，转换为换行符
        description = raw_description.replace("_wrap_", "\n").strip("\n")

        schema: Dict[str, Any] = {
            "name": field_name,
            "type": field_type,
            "label": cls._build_label(field_name),
            "description": description,
            "required": field_info.is_required(),
        }

        # default
        if field_info.default is not PydanticUndefined:
            schema["default"] = field_info.default
        elif field_info.default_factory is not None:
            try:
                factory = cast(Callable[[], Any], field_info.default_factory)
                schema["default"] = factory()
            except (TypeError, Exception):
                # default_factory 可能依赖外部状态/接受 model dict 参数，跳过
                pass

        # array items
        unwrapped_annotation = _unwrap_optional(annotation)
        origin = get_origin(unwrapped_annotation)
        args = get_args(unwrapped_annotation)
        if origin in {list, set} and args:
            schema["items"] = {"type": cls._map_field_type(args[0])}
        # dict 暂不递归展开 items，由 nested 接管
        if origin in {tuple} and args:
            schema["items"] = {"type": cls._map_field_type(args[0])}

        # options (Literal)
        options = cls._extract_options(annotation)
        if options:
            schema["options"] = options

        # json_schema_extra：合并 UI 提示（x-widget, x-icon 等）
        json_extra = getattr(field_info, "json_schema_extra", None)
        if json_extra and isinstance(json_extra, dict):
            # x-ui-type 覆盖默认类型映射（用于把 str 强制标记为 select 等）
            override_type = json_extra.get("x-ui-type")
            if override_type in {"string", "integer", "number", "boolean", "array", "object", "select"}:
                schema["type"] = override_type
            # x-options 强制设置 options（用于没有 Literal 但希望是 select 的字段）
            override_options = json_extra.get("x-options")
            if isinstance(override_options, list):
                schema["options"] = [str(o) for o in override_options]
            # 其它键原样透传
            schema.update(json_extra)

        # 约束 -> UI 字段
        schema.update(cls._extract_constraints(field_info))

        return schema

    # ----- 嵌套 schema -------------------------------------------------------
    @classmethod
    def _build_nested_schema(cls, annotation: Any) -> Optional[Dict[str, Any]]:
        """判断并构造嵌套 BaseModel schema，递归返回 None 表示不嵌套。"""
        unwrapped = _unwrap_optional(annotation)
        if _is_basemodel_subclass(unwrapped):
            return cls.generate_config_schema(unwrapped)
        origin = get_origin(unwrapped)
        args = get_args(unwrapped)
        # list[SomeConfig] / List[SomeConfig] —— 列表元素类型
        if origin in {list, set, tuple} and args:
            inner = args[0]
            if _is_basemodel_subclass(inner):
                return cls.generate_config_schema(inner)
        return None

    # ----- 主入口 ------------------------------------------------------------
    @classmethod
    def generate_schema(
        cls,
        config_class: type,
        include_nested: bool = True,
    ) -> Dict[str, Any]:
        """Pydantic 类 -> UI schema dict（兼容 MaiBot ``generate_schema``）。"""
        return cls.generate_config_schema(config_class, include_nested=include_nested)

    @classmethod
    def generate_config_schema(
        cls,
        config_class: type,
        include_nested: bool = True,
        skip_internal_fields: bool = True,
    ) -> Dict[str, Any]:
        """Pydantic 类 -> UI schema dict（兼容 MaiBot ``generate_config_schema``）。

        Args:
            config_class: Pydantic BaseModel 子类（建议继承 ``BaseConfig``）。
            include_nested: 是否递归展开 ``BaseConfig``/BaseModel 类型的字段。
            skip_internal_fields: 是否跳过 ``type`` / ``field_docs`` 等元字段。
        """
        if not (isinstance(config_class, type) and issubclass(config_class, BaseModel)):
            raise TypeError(f"config_class 必须是 pydantic.BaseModel 子类，收到: {config_class!r}")

        fields: List[Dict[str, Any]] = []
        nested: Dict[str, Dict[str, Any]] = {}
        field_docs = cls._get_class_field_docs(config_class)

        # 内部需要跳过的字段
        skip_names = {
            "field_docs",
            "_validate_any",
            "suppress_any_warning",
        }

        for field_name, field_info in config_class.model_fields.items():
            if skip_internal_fields and field_name in skip_names:
                continue

            annotation = field_info.annotation
            field_schema = cls._build_field_schema(
                config_class,
                field_name,
                annotation,
                field_info,
                field_docs,
            )
            fields.append(field_schema)

            if include_nested:
                nested_schema = cls._build_nested_schema(annotation)
                if nested_schema is not None:
                    nested[field_name] = nested_schema

        schema: Dict[str, Any] = {
            "className": config_class.__name__,
            "classDoc": (config_class.__doc__ or "").strip(),
            "fields": fields,
            "nested": nested,
        }

        # UI 分组元数据（兼容 MaiBot __ui_*__；Amaidesu 当前未使用）
        ui_parent = getattr(config_class, "__ui_parent__", "")
        ui_label = getattr(config_class, "__ui_label__", "")
        ui_advanced = bool(getattr(config_class, "__ui_advanced__", False))
        if ui_parent:
            schema["uiParent"] = ui_parent
        if ui_label:
            schema["uiLabel"] = ui_label
        schema["uiAdvanced"] = ui_advanced

        return schema


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------


def get_field_count(schema: Dict[str, Any]) -> int:
    """统计 schema 中字段（含嵌套）总数。"""
    total = len(schema.get("fields", []))
    for sub in (schema.get("nested") or {}).values():
        total += get_field_count(sub)
    return total


def collect_all_fields(
    schema: Dict[str, Any],
    prefix: str = "",
) -> List[Dict[str, Any]]:
    """把嵌套 schema 铺平为 ``prefix.field_name`` 形式的字段列表。"""
    out: List[Dict[str, Any]] = []
    for field in schema.get("fields", []):
        out.append(
            {
                "key": f"{prefix}.{field['name']}" if prefix else field["name"],
                **field,
            }
        )
    nested = schema.get("nested") or {}
    for field_name, sub_schema in nested.items():
        out.extend(
            collect_all_fields(
                sub_schema,
                prefix=f"{prefix}.{field_name}" if prefix else field_name,
            )
        )
    return out
