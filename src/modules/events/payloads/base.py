"""
事件 Payload 基类

为所有事件 Payload 提供统一的字符串表示，用于 EventBus 的 debug 日志。
"""

from typing import Any, Optional, Tuple

from pydantic import BaseModel


class BasePayload(BaseModel):
    """
    事件 Payload 基类

    提供统一的 __str__ 方法，将事件内容格式化为易读的字符串。
    子类可以覆盖 __str__() 方法以自定义格式化输出。

    所有事件 Payload 都应继承此类而非直接继承 BaseModel。
    """

    def _format_field_value(self, value: Any, indent: int = 0) -> str:
        """
        格式化字段值用于显示（单行模式）。

        Args:
            value: 字段值
            indent: 缩进级别

        Returns:
            格式化后的字符串
        """
        # 处理嵌套的 Payload 对象（BaseModel 子类）
        if isinstance(value, BaseModel):
            return str(value)

        if isinstance(value, dict):
            if not value:
                return "{}"
            # 单行格式化，只显示前几个键值对
            items = []
            for k, v in list(value.items())[:3]:  # 最多显示 3 个键值对
                formatted_v = self._format_field_value(v, 0)
                items.append(f'"{k}": {formatted_v}')
            if len(value) > 3:
                items.append("...")
            return "{" + ", ".join(items) + "}"
        elif isinstance(value, list):
            if not value:
                return "[]"
            # 单行格式化，只显示前几个元素
            items = [self._format_field_value(item, 0) for item in value[:3]]
            if len(value) > 3:
                items.append("...")
            return "[" + ", ".join(items) + "]"
        elif isinstance(value, str):
            # 限制字符串长度（更短，适合单行日志）
            if len(value) > 30:
                return f'"{value[:27]}..."'
            return f'"{value}"'
        else:
            return str(value)

    def get_log_format(self) -> Optional[Tuple[str, str, Optional[str]]]:
        """
        返回日志格式化的信息

        Returns:
            (text, user_name, extra) 元组，如果不需要特殊格式则返回 None
            - text: 要显示的文本内容（会被截断到 50 字符）
            - user_name: 用户名（可为空）
            - extra: 额外信息（可为空）
        """
        return None

    def __str__(self) -> str:
        """
        生成事件的调试字符串表示（默认实现）。

        格式: {ClassName} field1=value1, field2=value2, ...

        子类可以覆盖此方法以自定义输出格式。

        Returns:
            调试字符串
        """
        class_name = self.__class__.__name__
        parts = []
        for field_name in self.__class__.model_fields.keys():
            value = getattr(self, field_name)
            formatted_value = self._format_field_value(value)
            parts.append(f"{field_name}={formatted_value}")
        return f"{class_name}({', '.join(parts)})"
