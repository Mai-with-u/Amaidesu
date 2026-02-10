"""
事件 Payload 基类

为所有事件 Payload 提供统一的字符串表示，用于 EventBus 的 debug 日志。
"""

from typing import Any, List

from pydantic import BaseModel


class BasePayload(BaseModel):
    """
    事件 Payload 基类

    提供统一的 __str__ 方法，将事件内容格式化为易读的字符串。
    子类通过覆盖 _debug_fields() 方法自定义要显示的字段。

    所有事件 Payload 都应继承此类而非直接继承 BaseModel。
    """

    def _debug_fields(self) -> List[str]:
        """
        返回需要在 debug 日志中显示的字段名列表。

        子类覆盖此方法以自定义显示内容。返回的顺序即为显示顺序。

        Returns:
            字段名列表

        Example:
            >>> class MyEvent(BasePayload):
            ...     name: str
            ...     value: int
            ...     secret: str
            ...
            ...     def _debug_fields(self):
            ...         return ["name", "value"]  # 不显示 secret
        """
        # 默认返回所有字段（使用类属性避免 Pydantic 废弃警告）
        return list(self.__class__.model_fields.keys())

    def _format_field_value(self, value: Any, indent: int = 0) -> str:
        """
        格式化字段值用于显示。

        Args:
            value: 字段值
            indent: 缩进级别

        Returns:
            格式化后的字符串
        """
        prefix = "  " * indent

        # 处理嵌套的 Payload 对象（BaseModel 子类）
        if isinstance(value, BaseModel):
            return str(value)

        if isinstance(value, dict):
            if not value:
                return "{}"
            items = [f"{k}: {self._format_field_value(v, 0)}" for k, v in value.items()]
            return "{\n" + "\n".join(prefix + "  " + item for item in items) + "\n" + prefix + "}"
        elif isinstance(value, list):
            if not value:
                return "[]"
            items = [self._format_field_value(item, 0) for item in value]
            return "[" + ", ".join(items) + "]"
        elif isinstance(value, str):
            # 限制字符串长度
            if len(value) > 50:
                return f'"{value[:47]}..."'
            return f'"{value}"'
        else:
            return str(value)

    def __str__(self) -> str:
        """
        生成事件的调试字符串表示。

        格式: {ClassName} field1=value1, field2=value2, ...

        Returns:
            调试字符串
        """
        class_name = self.__class__.__name__
        fields = self._debug_fields()

        parts = []
        for field_name in fields:
            if hasattr(self, field_name):
                value = getattr(self, field_name)
                formatted_value = self._format_field_value(value)
                parts.append(f"{field_name}={formatted_value}")

        return f"{class_name}({', '.join(parts)})"
