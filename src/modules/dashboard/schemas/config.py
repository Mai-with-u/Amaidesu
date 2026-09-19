"""配置管理 API Schema

定义配置查询、Schema 获取与更新端点的请求/响应模型。
API 键约定与校验语义见 ``api/config.py`` 模块文档。
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    """完整配置响应 (合并视图)

    顶层键为六个 scope：agents / collectors / tools / model / storage / infra。
    """

    config: Dict[str, Any] = Field(default_factory=dict, description="完整配置字典")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""

    key: str = Field(description="配置键（scope 前缀点分路径，如 'infra.dashboard.port'）")
    value: Any = Field(description="配置值")


class ConfigUpdateResponse(BaseModel):
    """配置更新响应"""

    success: bool = Field(description="是否成功")
    message: str = Field(description="结果消息")
    requires_restart: bool = Field(default=False, description="是否需要重启服务")
    target_file: Optional[str] = Field(
        default=None,
        description="实际写入的 TOML 文件名 (用于调试与排错)",
    )


class BatchConfigChange(BaseModel):
    """批量更新中的单条变更。"""

    key: str = Field(description="配置键（scope 前缀点分路径，如 'tools.tools.tasks.poll_interval_ms'）")
    value: Any = Field(description="配置值")


class BatchConfigUpdateRequest(BaseModel):
    """批量配置更新请求。

    多个变更按提交顺序处理：
    - 同一批次内出现重复 key 时，后者覆盖前者的最终写入值（last-wins），
      这是为了支持前端"反复编辑同字段"时的最终一致性，不视为错误。
    - 整体按事务处理：任意一条校验失败则整个批次回退（无文件被改写）。
    """

    changes: list[BatchConfigChange] = Field(description="本次要提交的变更列表（按顺序处理，重复 key 后者覆盖前者）")


class BatchChangeResult(BaseModel):
    """批量端点中每条变更的处理结果。"""

    key: str = Field(description="配置键")
    success: bool = Field(description="本条是否成功")


class BatchChangeError(BaseModel):
    """批量端点中失败条目的错误明细。"""

    key: str = Field(description="失败的配置键")
    message: str = Field(description="失败原因（中文）")


class BatchConfigUpdateResponse(BaseModel):
    """批量配置更新响应。

    - 全部成功时：``success=true``，``results`` 列出每条 key 与 success=true，
      非 hot 段变更附带 ``requires_restart=true``。
    - 任一失败时：``success=false``，``errors`` 列出失败条目，``message`` 是首条失败的
      中文消息（含 "（另有 N 项失败）" 聚合后缀），并保证磁盘零写入。
    """

    success: bool = Field(description="是否全部成功")
    message: str = Field(description="聚合后的结果消息")
    requires_restart: bool = Field(default=False, description="是否需要重启服务（仅全部成功时为 true）")
    results: list[BatchChangeResult] = Field(
        default_factory=list,
        description="每条变更的处理结果（仅成功时填充）",
    )
    errors: list[BatchChangeError] = Field(
        default_factory=list,
        description="失败条目列表（仅失败时填充）",
    )


class SchemaGroupsResponse(BaseModel):
    """前端 groups 格式响应."""

    groups: list[Dict[str, Any]] = Field(default_factory=list, description="配置分组列表")
    version: str = Field(default="1.0.0", description="Schema 版本号")
