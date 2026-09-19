"""工具提供者目录 Schema

定义工具页侧边栏 / 提供者分组端点的响应模型；卡片字段对照
``services.tool_catalog.build_tool_catalog`` 的实际产出。
"""

from typing import List

from pydantic import BaseModel, Field


class ProviderCard(BaseModel):
    """单个工具提供者卡片（配置态 + 运行态 + 管理提示）。"""

    key: str = Field(description="提供者键（配置段键 / 注册表名）")
    provider_name: str = Field(description="展示名（当前与 key 同值）")
    description: str = Field(default="", description="展示描述；未知提供者为空串")
    tool_count: int = Field(default=0, description="归属工具全集数（含停用/熔断）")
    disabled_count: int = Field(default=0, description="停用工具数")
    registered: bool = Field(description="是否已在运行时注册表登记")
    degraded: bool = Field(description="已注册但工具数为 0（连接失败降级等）")
    supports_reconnect: bool = Field(default=False, description="是否支持手动重连")
    last_error: str = Field(default="", description="最近一次连接错误描述")
    enabled: bool = Field(description="声明配置态开关")
    in_config: bool = Field(description="声明配置中是否存在该提供者")
    notice: str = Field(default="", description="面板管理提示文案（无提示为空串）")
    switchable: bool = Field(description="是否可经面板开关（随 Agent 分类不可）")


class ToolCatalogCategory(BaseModel):
    """提供者分类分组（空分类也输出，保持侧边栏稳定）。"""

    category: str
    providers: List[ProviderCard] = Field(default_factory=list)


class ToolCatalogResponse(BaseModel):
    """工具提供者分类目录响应。"""

    categories: List[ToolCatalogCategory] = Field(default_factory=list)
