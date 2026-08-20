"""MessageType 登记表。

把 ``NormalizedMessage.data_type`` 从自由字符串改为登记制——任何 ``NormalizedMessage``
构造（包括反序列化 ``model_validate``）都必须传入已登记的类型名，否则抛出
``MessageTypeNotRegistered``。

设计要点
--------

- **模块级 dict + 装饰器**：与 ``src/modules/pipeline/registry.py``（PipelineRegistry）、
  ``src/modules/events/registry.py``（@register_event）保持一致风格。
- **装饰器 import 即注册**：模块 import 即生效（不依赖外部 ``register_*()`` 调用），
  仿照 ``@register_event`` 的 import-driven 语义；但**重复登记会报错**（``MessageTypeRegistrationError``），
  与 ``@register_event`` 的幂等行为不同——登记表是封闭集合，重复登记是编码错误。
- **模板两档**：

  - ``prompt_template``：AI 侧文本（MessageBuffer / Planner / Simulator 消费的 prompt）。
    变量集：``{text}``（消息原文）、``{nickname}``（观众昵称）。
  - ``display_template``：前端展示（Widget）。变量集：``{content}``、``{gift_name}``、
    ``{gift_count}``、``{sc_price}``、``{sc_message}``、``{guard_name}``。

  渲染时由调用方负责 resolve 默认值（如 ``gift_name or '礼物'``），模板只描述结构。

- **字节级一致硬约束**：模板值已与 ``message_buffer.py:201-218`` 现状渲染输出对齐；
  任何"顺手优化文案"都视作破坏行为，必须停手回滚。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, TypeVar

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

T = TypeVar("T", bound=Any)


# ==================== 异常 ====================


class MessageTypeRegistrationError(ValueError):
    """MessageType 重复登记或非法登记时抛出。"""


class MessageTypeNotRegistered(ValueError):
    """构造 NormalizedMessage 时 data_type 未在登记表中注册时抛出。"""


# ==================== 数据契约 ====================


class MessageTypeSpec(BaseModel):
    """登记项的数据契约。

    Attributes:
        name: 注册名，与 ``NormalizedMessage.data_type`` 字段值一一对应。
        label: 人类可读标签（如"礼物"、"入场"），供 UI 展示。
        prompt_template: AI 侧 prompt 模板。变量：``{text}``、``{nickname}``。
        display_template: 前端展示模板（widget 用）。变量：``{content}``、
            ``{gift_name}``、``{gift_count}``、``{sc_price}``、``{sc_message}``、
            ``{guard_name}``。允许为空（表示该类型无前端展示）。
        default_importance: 默认重要性评分（0-1）。
    """

    name: str = Field(description="注册名（与 NormalizedMessage.data_type 一致）")
    label: str = Field(description="人类可读标签")
    prompt_template: str = Field(description="AI 侧 prompt 模板")
    display_template: str = Field(default="", description="前端展示模板")
    default_importance: float = Field(default=0.5, ge=0.0, le=1.0, description="默认重要性评分")


# ==================== 模块级注册表 ====================


MESSAGE_TYPE_REGISTRY: Dict[str, MessageTypeSpec] = {}

logger = get_logger("MessageTypeRegistry")


# ==================== 装饰器 ====================


def register_message_type(
    name: str,
    label: str,
    prompt_template: str,
    display_template: str = "",
    default_importance: float = 0.5,
) -> Callable[[T], T]:
    """把一个类/对象标记为某 message_type 的占位并登记到 ``MESSAGE_TYPE_REGISTRY``。

    与 ``@register_event`` 的 import-driven 注册风格一致，但**重复登记会抛
    ``MessageTypeRegistrationError``**（登记表是封闭集合，重复登记视为编码错误）。
    被装饰类获得 ``cls._registered_message_type = name`` 反向引用，便于日志/调试。

    Args:
        name: 注册名（与 ``NormalizedMessage.data_type`` 一致）。
        label: 人类可读标签。
        prompt_template: AI 侧模板（变量 ``{text}``/``{nickname}``）。
        display_template: 前端模板（变量 ``{content}`` 等），可为空。
        default_importance: 默认重要性（0-1）。

    Returns:
        装饰器函数。被装饰对象原样返回。

    Raises:
        MessageTypeRegistrationError: 同一 ``name`` 已登记为不同 spec。

    Example:
        ::

            @register_message_type(
                name="text",
                label="消息",
                prompt_template="{nickname}: {text}",
                display_template="{content}",
            )
            class TextMessageType: ...
    """

    def decorator(cls: T) -> T:
        if name in MESSAGE_TYPE_REGISTRY:
            existing = MESSAGE_TYPE_REGISTRY[name].label
            raise MessageTypeRegistrationError(f"message_type '{name}' 已登记为 '{existing}'，不能重复登记")

        spec = MessageTypeSpec(
            name=name,
            label=label,
            prompt_template=prompt_template,
            display_template=display_template,
            default_importance=default_importance,
        )
        MESSAGE_TYPE_REGISTRY[name] = spec
        cls._registered_message_type = name  # type: ignore[attr-defined]
        logger.debug(f"MessageType 已登记: {name} -> {label}")
        return cls

    return decorator


# ==================== 查询 API ====================


def require_message_type(name: str) -> MessageTypeSpec:
    """获取已登记的 message_type。**未登记即抛** ``MessageTypeNotRegistered``。

    用于 ``NormalizedMessage`` 构造期关口（``@field_validator("data_type")``），
    是整个登记制的"拦截点"——任何未登记字符串都会触发异常并向上传播。

    Args:
        name: 待查询的 message_type 名。

    Returns:
        对应的 ``MessageTypeSpec``。

    Raises:
        MessageTypeNotRegistered: ``name`` 不在 ``MESSAGE_TYPE_REGISTRY`` 中。
    """
    spec = MESSAGE_TYPE_REGISTRY.get(name)
    if spec is None:
        registered = sorted(MESSAGE_TYPE_REGISTRY.keys())
        raise MessageTypeNotRegistered(f"message_type '{name}' 未登记。已登记类型: {registered}")
    return spec


def get_message_type(name: str) -> Optional[MessageTypeSpec]:
    """查询 message_type，未登记返回 ``None``（不抛错）。

    用于前端/容错场景——例如 widget 收到未知类型时回退到 ``unknown`` 模板展示。

    Args:
        name: 待查询的 message_type 名。

    Returns:
        对应的 ``MessageTypeSpec``，未登记返回 ``None``。
    """
    return MESSAGE_TYPE_REGISTRY.get(name)


def list_message_types() -> Dict[str, MessageTypeSpec]:
    """列出全部已登记的 message_type（返回副本，外部修改不影响内部注册表）。

    Returns:
        注册表副本（``Dict[str, MessageTypeSpec]``）。
    """
    return MESSAGE_TYPE_REGISTRY.copy()


def clear() -> None:
    """清空注册表。**仅供测试使用**——调用后所有 7 个内置类型登记都会被清除。

    生产代码不应调用。测试通常与 ``pytest`` 的 ``monkeypatch``/``setup`` 配合使用。
    """
    MESSAGE_TYPE_REGISTRY.clear()


def reset_to_builtins() -> None:
    """清空注册表并重新登记 7 个内置类型。**仅供测试使用**。

    与 ``clear()`` 不同，本函数调用后 ``MESSAGE_TYPE_REGISTRY`` 会恢复成模块加载时的
    初始状态。生产代码不应调用。

    新增内置类型时必须同步更新 ``_register_builtins()``（本模块单一事实源）。
    """
    MESSAGE_TYPE_REGISTRY.clear()
    _register_builtins()


def _register_builtins() -> None:
    """注册 7 个内置 message_type。**模块加载时调用一次**；``reset_to_builtins()``
    也会再次触发。

    这是内置类型登记的**单一事实源**——新增内置类型时只改这里，模块装饰器和
    ``reset_to_builtins()`` 自动同步。
    """

    @register_message_type(
        name="text",
        label="消息",
        prompt_template="{nickname}: {text}",
        display_template="{content}",
    )
    class _BuiltinText:
        pass

    @register_message_type(
        name="gift",
        label="礼物",
        prompt_template="[礼物] {nickname}: {text}",
        display_template="送出 {gift_name} x{gift_count}",
    )
    class _BuiltinGift:
        pass

    @register_message_type(
        name="super_chat",
        label="醒目留言",
        prompt_template="[醒目留言] {nickname}: {text}",
        display_template="¥{sc_price} {sc_message}",
    )
    class _BuiltinSuperChat:
        pass

    @register_message_type(
        name="guard",
        label="上舰",
        prompt_template="[上舰] {nickname}: {text}",
        display_template="开通了 {guard_name}",
    )
    class _BuiltinGuard:
        pass

    @register_message_type(
        name="enter",
        label="入场",
        prompt_template="[入场] {nickname}: {text}",
        display_template="进入了直播间",
    )
    class _BuiltinEnter:
        pass

    @register_message_type(
        name="unknown",
        label="未知消息",
        prompt_template="{nickname}: {text}",
        display_template="{content}",
    )
    class _BuiltinUnknown:
        pass

    @register_message_type(
        name="game.text",
        label="游戏文本",
        prompt_template="[游戏] {text}",
        display_template="{content}",
    )
    class _BuiltinGameText:
        pass


# ==================== 模块加载时登记 7 个内置类型 ====================
_register_builtins()


__all__ = [
    "MESSAGE_TYPE_REGISTRY",
    "MessageTypeSpec",
    "MessageTypeRegistrationError",
    "MessageTypeNotRegistered",
    "register_message_type",
    "require_message_type",
    "get_message_type",
    "list_message_types",
    "clear",
    "reset_to_builtins",
]
