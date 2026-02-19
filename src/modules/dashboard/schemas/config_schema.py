"""
配置 Schema 定义

用于动态表单生成的 Schema 数据结构。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ConfigFieldType(str, Enum):
    """配置字段类型"""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    SELECT = "select"
    ARRAY = "array"
    OBJECT = "object"


class ValidationRule(BaseModel):
    """验证规则"""

    min: Optional[float] = None
    max: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # 正则表达式
    options: Optional[List[str]] = None  # 选项列表（用于 SELECT 类型）


class ConfigFieldSchema(BaseModel):
    """配置字段 Schema"""

    key: str = Field(description="字段键名（点分隔路径）")
    label: str = Field(description="显示标签")
    description: Optional[str] = Field(default=None, description="字段描述")
    type: ConfigFieldType = Field(description="字段类型")
    default: Optional[Any] = Field(default=None, description="默认值")
    value: Optional[Any] = Field(default=None, description="当前值")
    validation: Optional[ValidationRule] = Field(default=None, description="验证规则")
    properties: Optional[Dict[str, "ConfigFieldSchema"]] = Field(
        default=None, description="嵌套对象属性（OBJECT 类型）"
    )
    items: Optional["ConfigFieldSchema"] = Field(default=None, description="数组项 Schema（ARRAY 类型）")
    required: bool = Field(default=False, description="是否必填")
    sensitive: bool = Field(default=False, description="是否为敏感字段")
    group: Optional[str] = Field(default=None, description="所属分组")


class ConfigGroupSchema(BaseModel):
    """配置分组 Schema"""

    key: str = Field(description="分组键名")
    label: str = Field(description="分组显示名称")
    description: Optional[str] = Field(default=None, description="分组描述")
    icon: Optional[str] = Field(default=None, description="图标名称")
    fields: List[ConfigFieldSchema] = Field(default_factory=list, description="字段列表")
    order: int = Field(default=0, description="排序优先级")


class ConfigSchemaResponse(BaseModel):
    """配置 Schema 响应"""

    groups: List[ConfigGroupSchema] = Field(default_factory=list, description="配置分组列表")
    version: str = Field(default="1.0.0", description="Schema 版本")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""

    key: str = Field(description="配置键（点分隔路径，如 'general.platform_id'）")
    value: Any = Field(description="配置值")


class ConfigUpdateResponse(BaseModel):
    """配置更新响应"""

    success: bool = Field(description="是否成功")
    message: str = Field(description="结果消息")
    requires_restart: bool = Field(default=False, description="是否需要重启服务")
