"""
Schema Generator — 从 Pydantic Config 类自动生成 UI Schema

从 Pydantic v2 配置类（含 Amaidesu 的 ``BaseConfig`` 子类）生成
Dashboard / WebUI 可消费的 schema dict，不修改既有模型、不引入额外运行时依赖。

输出 Schema 形状
----------------
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

限制
----
- 仅支持 Pydantic v2 Field 约束 (ge, le, gt, lt, pattern, min_length, max_length)。
"""

from __future__ import annotations

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

    @staticmethod
    def _build_label(label: str) -> Dict[str, str]:
        """构造多语言标签 dict。"""
        return {"zh_CN": label}

    # ----- 类型映射 ----------------------------------------------------------
    @classmethod
    def _map_field_type(cls, annotation: Any) -> str:
        """把 Python 类型注解映射为 UI 类型字符串。

        返回值集合：``string``、``integer``、``number``、
        ``boolean``、``array``、``object``、``select``。
        """
        # Optional[X] 剥皮
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

        # 裸 dict（非 Dict[str, Any] 泛型写法）-> object
        if unwrapped is dict:
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
    ) -> Dict[str, Any]:
        """构造单个字段的 UI schema。"""
        field_type = cls._map_field_type(annotation)
        # _wrap_ 标记转换为换行符
        description = (field_info.description or "").replace("_wrap_", "\n").strip("\n")

        schema: Dict[str, Any] = {
            "name": field_name,
            "type": field_type,
            # 字段展示名：Field(title=...) 声明的中文名优先，未声明回退字段名
            "label": cls._build_label(field_info.title or field_name),
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
            except Exception:
                # default_factory 是任意可调用对象，可能依赖尚未就绪的外部状态、
                # 或接受本模型实例为参数而在此处调用签名不兼容；无法产出默认值
                # 时放弃填充（debug 留痕），前端回退为空值
                logger.debug("default_factory 调用失败，跳过 Schema 默认值填充", exc=True)

        # array items
        unwrapped_annotation = _unwrap_optional(annotation)
        origin = get_origin(unwrapped_annotation)
        args = get_args(unwrapped_annotation)
        if origin in {list, set} and args:
            inner = args[0]
            if _is_basemodel_subclass(inner):
                # 对象数组（TOML array-of-tables）：items 携带元素子字段树，
                # 前端据此渲染元素卡片编辑器（数组是一等公民，不做降维展开）
                item_schema = cls.generate_config_schema(inner, include_nested=False)
                schema["items"] = {"type": "object", "fields": item_schema.get("fields", [])}
            else:
                schema["items"] = {"type": cls._map_field_type(inner)}
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
        """Pydantic 类 -> UI schema dict（generate_config_schema 的兼容入口）。"""
        return cls.generate_config_schema(config_class, include_nested=include_nested)

    @classmethod
    def generate_config_schema(
        cls,
        config_class: type,
        include_nested: bool = True,
    ) -> Dict[str, Any]:
        """Pydantic 类 -> UI schema dict。

        Args:
            config_class: Pydantic BaseModel 子类（建议继承 ``BaseConfig``）。
            include_nested: 是否递归展开 ``BaseConfig``/BaseModel 类型的字段。
        """
        if not (isinstance(config_class, type) and issubclass(config_class, BaseModel)):
            raise TypeError(f"config_class 必须是 pydantic.BaseModel 子类，收到: {config_class!r}")

        fields: List[Dict[str, Any]] = []
        nested: Dict[str, Dict[str, Any]] = {}

        for field_name, field_info in config_class.model_fields.items():
            annotation = field_info.annotation
            field_schema = cls._build_field_schema(
                config_class,
                field_name,
                annotation,
                field_info,
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

        _apply_registry_options(schema, config_class)

        return schema


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------


def _apply_registry_options(schema: Dict[str, Any], config_class: type) -> None:
    """把注册表驱动的动态候选名单注入 schema（读取侧单点化）。

    - collectors.enabled 的候选 = 采集器注册表键集，名单随注册表走，
      Schema 定义处不再手抄（手抄与注册表必然漂移）。定义处直引注册表
      会成环（collectors_schemas → registry → 组件包 → multi_file_loader
      → collectors_schemas），故在 schema 服务路径上注入。
    - infra.toml 的 [tts].provider 候选 = tts 装配表键集（加引擎只改
      装配表一处，schema 选项与装配校验同源）。
    """
    file_name = getattr(config_class, "__file_name__", "")
    if file_name == "collectors.toml":
        # 函数内 import 规避循环依赖（组件目录拉起组件包，组件包引用本包）
        from src.modules.component_catalog import ensure_component_registry
        from src.modules.config.registry import COLLECTOR_SCHEMAS

        ensure_component_registry()
        for field in schema.get("fields", []):
            if field.get("name") == "enabled":
                field["options"] = sorted(COLLECTOR_SCHEMAS)
    elif file_name == "infra.toml":
        tts_schema = (schema.get("nested") or {}).get("tts")
        if isinstance(tts_schema, dict):
            # 函数内 import：装配链经引擎包拉起 numpy/soundfile 等重依赖，
            # 延迟到 infra schema 生成路径再加载
            from src.modules.tts.assembly import TTS_PROVIDER_NAMES

            for field in tts_schema.get("fields", []):
                if field.get("name") == "provider":
                    field["options"] = list(TTS_PROVIDER_NAMES)


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
    """把嵌套 schema 铺平为 ``prefix.field_name`` 形式的字段列表。

    对象数组（list[BaseModel]）不降维：数组字段自身就是可编辑的一等字段
    （元素子字段树在 ``items.fields``），其元素字段不展开为独立键——
    否则会生成无法寻址写回的伪键（如 ``llm_providers.api_key``）。
    """
    out: List[Dict[str, Any]] = []
    fields = schema.get("fields", [])
    array_names = {f["name"] for f in fields if f.get("type") == "array"}
    for field in fields:
        out.append(
            {
                "key": f"{prefix}.{field['name']}" if prefix else field["name"],
                **field,
            }
        )
    nested = schema.get("nested") or {}
    for field_name, sub_schema in nested.items():
        if field_name in array_names:
            continue
        out.extend(
            collect_all_fields(
                sub_schema,
                prefix=f"{prefix}.{field_name}" if prefix else field_name,
            )
        )
    return out
