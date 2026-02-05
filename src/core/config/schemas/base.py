"""Provider配置基类定义"""
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field


class BaseProviderConfig(BaseModel):
    """Provider配置基类

    所有Provider配置的抽象基类，定义通用字段和验证逻辑。
    """
    type: str = Field(description="Provider类型标识")

    class Config:
        extra = "ignore"  # 允许额外字段，便于扩展

    @classmethod
    def generate_toml(cls, output_path: str | Path, schema_name: str | None = None) -> None:
        """导出Schema为TOML配置文件

        生成包含所有可配置字段及其默认值的TOML文件，用于作为配置模板。

        Args:
            output_path: 输出文件路径
            schema_name: Schema名称（用于生成TOML中的表名），默认为类名的小写形式

        Example:
            >>> BaseProviderConfig.generate_toml("config-defaults.toml", "my_provider")
        """
        output_path = Path(output_path)

        # 获取Schema的JSON Schema定义
        schema = cls.model_json_schema()

        # 确定表名
        table_name = schema_name if schema_name else cls.__name__.lower()

        # 递归提取字段和默认值
        def extract_defaults(properties: dict[str, Any]) -> dict[str, Any]:
            """递归提取字段的默认值"""
            result = {}
            for field_name, field_info in properties.items():
                # 跳过内部字段
                if field_name.startswith("_"):
                    continue

                # 处理嵌套对象
                if field_info.get("type") == "object":
                    nested = extract_defaults(field_info.get("properties", {}))
                    if nested:
                        result[field_name] = nested
                # 处理简单字段
                else:
                    default = field_info.get("default")
                    if default is not None:
                        result[field_name] = default
                    # 对于没有默认值但有示例的，使用示例
                    elif "example" in field_info:
                        result[field_name] = field_info["example"]

            return result

        # 构建TOML内容
        toml_data = {table_name: extract_defaults(schema.get("properties", {}))}

        # 写入文件
        output_path.write_text(tomli_w.dumps(toml_data), encoding="utf-8")
