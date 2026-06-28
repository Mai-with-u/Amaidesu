"""Capability 模型 + Pydantic 反射工具。

设计要点:
- 每个 handler 通过 `get_capabilities()` 返回 `HandlerCapabilities`(本地 action 名)
- `OutputHandlerManager.get_all_capabilities()` 自动加 handler 前缀,生成 `UnifiedActionEntry`
- `_pydantic_to_param_spec()` 把 Pydantic model 反射成 `ParameterSpec` 字典
"""

from typing import Any, Dict, List, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, Field

# JSON Schema 风格的参数类型
ParameterType = Literal["string", "number", "integer", "boolean"]


class ParameterSpec(BaseModel):
    """单个参数的描述(用于暴露给 LLM / Plugin)。"""

    type: ParameterType = "string"
    required: bool = False
    default: Optional[Any] = None
    description: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


class ActionSpec(BaseModel):
    """单个动作的描述(本地名,不带 handler 前缀)。"""

    name: str = Field(..., description="本地 action 名,不含 handler 前缀")
    description: Optional[str] = None
    parameters: Dict[str, ParameterSpec] = Field(default_factory=dict)


class HandlerCapabilities(BaseModel):
    """handler 自身暴露的能力(本地 action 名)。"""

    actions: List[ActionSpec] = Field(default_factory=list)


class UnifiedActionEntry(BaseModel):
    """经过 Manager 统一命名后的 action(全限定名,带 handler 前缀)。"""

    name: str = Field(..., description="全限定 action 名,格式 `<handler>.<action>`")
    description: Optional[str] = None
    parameters: Dict[str, ParameterSpec] = Field(default_factory=dict)


class UnifiedCapabilitiesView(BaseModel):
    """整个 Output 阶段所有 handler 的能力聚合视图。"""

    actions: List[UnifiedActionEntry] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Pydantic -> ParameterSpec 反射
# ----------------------------------------------------------------------

# 不支持的复杂类型(Union / List / nested Pydantic 等)
_UNSUPPORTED_ORIGINS = (list, dict, tuple, set, frozenset)


def _python_to_json_type(annotation: Any) -> ParameterType:
    """把 Python 类型注解映射到 JSON Schema 风格类型。"""
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Optional[T] / Union[T, None] → 解包到 T
    if origin is Union and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_to_json_type(non_none[0])
        return "string"  # 复杂 Union 不支持,降级

    if origin in _UNSUPPORTED_ORIGINS:
        return "string"  # 复杂容器,降级

    # 直接类型
    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"

    # 默认降级
    return "string"


def _extract_constraint(field_info: Any, key: str) -> Optional[float]:
    """从 Pydantic FieldInfo 提取 ge/le 等数值约束(兼容 Pydantic v1/v2)。"""
    # Pydantic v2: field_info.ge / field_info.le
    val = getattr(field_info, key, None)
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    # Pydantic v1 兼容: metadata 里找
    metadata = getattr(field_info, "metadata", None) or []
    for m in metadata:
        v = getattr(m, key, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _pydantic_to_param_spec(model_cls: type[BaseModel]) -> Dict[str, ParameterSpec]:
    """把 Pydantic BaseModel 子类反射成 `Dict[str, ParameterSpec]`。

    支持:
    - 必填/可选(required 来自 `is_required()`)
    - default(无 default 时为 None)
    - description
    - ge / le(minimum / maximum)
    - Optional[T] 解包
    - str / int / float / bool / Literal → 映射到 JSON Schema 类型

    不支持(降级为 string):
    - 复杂 Union、List、Dict、nested Pydantic
    """
    result: Dict[str, ParameterSpec] = {}
    for field_name, field_info in model_cls.model_fields.items():
        annotation = field_info.annotation
        # Literal["a", "b"] 类型 → string(且 description 提示取值)
        param_type: ParameterType = _python_to_json_type(annotation)

        # required: FieldInfo.is_required() (Pydantic v2)
        try:
            required = bool(field_info.is_required())
        except AttributeError:
            required = field_info.default is None and field_info.default_factory is None

        # default
        default = None
        if not required:
            if field_info.default is not None:
                default = field_info.default
            elif getattr(field_info, "default_factory", None) is not None:
                # 默认工厂不直接求值,记为 None
                default = None

        result[field_name] = ParameterSpec(
            type=param_type,
            required=required,
            default=default,
            description=field_info.description,
            minimum=_extract_constraint(field_info, "ge"),
            maximum=_extract_constraint(field_info, "le"),
        )
    return result


__all__ = [
    "ParameterType",
    "ParameterSpec",
    "ActionSpec",
    "HandlerCapabilities",
    "UnifiedActionEntry",
    "UnifiedCapabilitiesView",
    "_pydantic_to_param_spec",
]
