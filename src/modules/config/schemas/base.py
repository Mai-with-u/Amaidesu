"""Provider配置基类定义

定义所有Provider配置的抽象基类。
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class BaseProviderConfig(BaseModel):
    """Provider配置基类

    所有Provider配置的抽象基类，定义通用字段和验证逻辑。

    注意：
    - 此基类不包含'enabled'字段，该字段由Manager统一管理
    - Provider本地配置文件禁止包含'enabled'字段
    """

    type: str = Field(description="Provider类型标识")

    model_config = {"extra": "ignore"}  # Pydantic v2 语法

    @classmethod
    def generate_toml(
        cls,
        output_path: str | Path,
        provider_name: str | None = None,
        include_comments: bool = True,
    ) -> None:
        """从Schema生成TOML配置文件

        生成包含所有可配置字段及其默认值的TOML文件，用作配置模板。
        包含字段的description作为注释。

        Args:
            output_path: 输出文件路径
            provider_name: Provider名称（用于生成TOML中的表名），默认为类名的小写形式
            include_comments: 是否包含字段注释（从Field description生成）

        Example:
            >>> ConsoleInputProviderConfig.generate_toml("config.toml", "console_input")
        """
        output_path = Path(output_path)

        # 确定表名
        table_name = provider_name if provider_name else cls.__name__.lower()

        # 创建Schema实例获取默认值
        schema_instance = cls()
        config_dict = schema_instance.model_dump(exclude_unset=False)

        # 生成带注释的TOML内容
        lines = []

        # 添加文档字符串作为文件头注释
        if include_comments and cls.__doc__:
            docstring = cls.__doc__.strip()
            lines.append(f"# {docstring}")
            lines.append("")

        # 添加配置节
        lines.append(f"[{table_name}]")

        # 添加字段注释和值
        for field_name, field_info in cls.model_fields.items():
            # 获取字段值
            field_value = config_dict.get(field_name)

            # 添加字段注释
            if include_comments and field_info.description:
                lines.append(f"# {field_info.description}")

            # 格式化字段值（转为TOML格式）
            toml_value = _format_toml_value(field_value)
            lines.append(f"{field_name} = {toml_value}")

        # 写入文件
        toml_content = "\n".join(lines)
        output_path.write_text(toml_content, encoding="utf-8")

    @classmethod
    def get_default_dict(cls) -> dict[str, Any]:
        """获取默认配置字典

        Returns:
            包含所有字段默认值的字典
        """
        schema_instance = cls()
        return schema_instance.model_dump(exclude_unset=False)


def _format_toml_value(value: Any) -> str:
    """将Python值转换为TOML字面量字符串

    Args:
        value: Python值

    Returns:
        TOML格式的字符串
    """
    if value is None:
        return '""'
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        # 转义特殊字符
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, list):
        items = [_format_toml_item(v) for v in value]
        return f"[{', '.join(items)}]"
    elif isinstance(value, dict):
        # 嵌套字典使用内联表格格式
        pairs = [f"{k} = {_format_toml_item(v)}" for k, v in value.items()]
        return f"{{{', '.join(pairs)}}}"
    else:
        # 其他类型转为字符串
        return f'"{str(value)}"'


def _format_toml_item(value: Any) -> str:
    """格式化TOML数组项（简化版）

    Args:
        value: 数组项值

    Returns:
        TOML格式的字符串
    """
    if value is None:
        return '""'
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(value, (int, float)):
        return str(value)
    else:
        return f'"{str(value)}"'
