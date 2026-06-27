"""
类型匹配依赖注入工具。

按类的 `__init__` 参数类型注解从服务字典中查找并注入依赖。

## 使用示例

```python
from src.modules.di import instantiate_with_di
from src.modules.llm import LLMManager
from src.modules.events import EventBus

event_bus = EventBus()
llm = LLMManager(...)

handler = instantiate_with_di(
    MyHandler,
    config=handler_config,
    services_by_type={
        EventBus: event_bus,
        LLMManager: llm,
    },
)
```

## 支持的特性

- **类型匹配**：按参数的类型注解从 `services_by_type` 字典查找服务
- **Optional 解包**：`Optional[X]` 自动解包为 `X` 类型
- **kwargs 处理**：检测到 `**kwargs` 时自动把所有剩余服务传入
- **config 特殊处理**：`config` 参数从 `config=` 关键字直接获取
- **默认值**：有默认值的无注解参数会被跳过

## 与参数名匹配的比较

类型匹配相比参数名匹配的优势：
- 参数名可自由调整（重构更友好）
- IDE 重命名会同步更新所有匹配点
- 避免"重命名导致 DI 静默失败"的隐患
- 更接近容器模式，未来引入容器时无需修改参与者的签名

## 异常

- `DependencyInjectionError`：缺失必填服务、参数无注解且无默认值时抛出
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Type, Union, get_args, get_origin


class DependencyInjectionError(Exception):
    """依赖注入失败（缺失必填服务或参数无类型注解）。"""


def _resolve_annotation(annotation: Any) -> Union[Type, tuple, None]:
    """
    解析类型注解，解包 Optional[X] 和 Union[X, Y]。

    Returns:
        - 单个 Type：明确单一类型
        - tuple of Type：Union 多个类型（任一匹配即可）
        - None：注解为空
    """
    if annotation is inspect.Parameter.empty:
        return None

    # Optional[X] == Union[X, None]，通过 get_origin 识别
    origin = get_origin(annotation)

    if origin is Union:
        # 排除 NoneType，剩下的就是要匹配的类型
        non_none = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(non_none) == 1:
            return non_none[0]
        elif len(non_none) == 0:
            return None
        else:
            return non_none

    return annotation


def _is_builtin_param(annotation: Any) -> bool:
    """
    判断是否是"非服务"参数（如 Dict[str, Any]、int、str 等）。

    这些参数需要从其他途径（config / kwargs）传入，不通过 DI 注入。
    """
    if annotation is inspect.Parameter.empty:
        return True

    # 基本类型不算服务
    if annotation in (int, float, str, bool, bytes, list, dict, set, tuple):
        return True

    # typing/内置 泛型（如 Dict[str, Any]、List[int]、dict[str, Any]）也算非服务
    if get_origin(annotation) is not None:
        return True

    return False


def instantiate_with_di(
    cls: Type[Any],
    config: Any,
    services_by_type: Dict[Type[Any], Any],
) -> Any:
    """
    按类型注解实例化类。

    Args:
        cls: 要实例化的类
        config: 注入到 `config` 参数的配置对象（如果 `__init__` 有 `config` 参数）
        services_by_type: 服务字典（key 为类型，value 为服务实例）

    Returns:
        cls 的实例

    Raises:
        DependencyInjectionError: 缺失必填服务、参数无注解且无默认值
    """
    sig = inspect.signature(cls.__init__)
    kwargs: Dict[str, Any] = {}
    remaining_services: Dict[Type[Any], Any] = dict(services_by_type)

    for name, param in sig.parameters.items():
        # 跳过 self
        if name == "self":
            continue

        # **kwargs 表示"接受任意额外参数但不强制使用"，
        # 类型匹配 DI 已经把所有类型化服务按需注入完毕，
        # 剩下的服务由参与者自行按需消费，这里不显式传任何东西给 **kwargs
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue

        # 处理 config 参数
        if name == "config":
            kwargs[name] = config
            continue

        # 跳过 *args
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue

        annotation = _resolve_annotation(param.annotation)

        # 没注解且没默认值 → 错误
        if annotation is None:
            if param.default is inspect.Parameter.empty:
                raise DependencyInjectionError(
                    f"{cls.__name__}.__init__ 参数 '{name}' 缺少类型注解，"
                    f"且无默认值，无法通过类型匹配注入。请添加类型注解或默认值。"
                )
            # 有默认值但没注解 → 跳过（无法判断是否需要注入）
            continue

        # 基本类型（int/str/Dict[str, Any]等）跳过，靠调用方显式传入
        # 注意：先 resolve（解包 Optional/Union）再判断，否则 Optional[X] 会被误判为 GenericAlias
        if _is_builtin_param(annotation):
            if param.default is inspect.Parameter.empty:
                # config 已经在上面处理，这里只剩其他基本类型
                # 如果调用方没传，只能报错
                raise DependencyInjectionError(
                    f"{cls.__name__}.__init__ 参数 '{name}' 注解为基本类型 {param.annotation!r}，无法通过 DI 注入。"
                )
            continue

        # 按类型查找服务
        # annotation 可能是单个 Type 或 tuple of Type（Union 情况）
        if isinstance(annotation, tuple):
            # Union 情况：任一类型匹配即可
            matched = False
            for ann in annotation:
                if ann in services_by_type:
                    kwargs[name] = services_by_type[ann]
                    remaining_services.pop(ann, None)
                    matched = True
                    break
            if not matched:
                if param.default is not inspect.Parameter.empty:
                    continue
                raise DependencyInjectionError(
                    f"{cls.__name__}.__init__ 参数 '{name}' 类型 {annotation!r} "
                    f"在服务字典中未找到。可用服务类型: {list(services_by_type.keys())}"
                )
        else:
            if annotation not in services_by_type:
                if param.default is not inspect.Parameter.empty:
                    # Optional[X] 且服务未提供 → 跳过（让默认 None 生效）
                    continue
                raise DependencyInjectionError(
                    f"{cls.__name__}.__init__ 参数 '{name}' 类型 {annotation!r} "
                    f"在服务字典中未找到。可用服务类型: {list(services_by_type.keys())}"
                )
            kwargs[name] = services_by_type[annotation]
            remaining_services.pop(annotation, None)

    return cls(**kwargs)
