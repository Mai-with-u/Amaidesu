"""建造子任务的预算与新 Mod 接口绑定，不包含具体建筑技法。"""

from pydantic import Field, model_validator

from src.modules.config.schemas.base import BaseConfig


class MinecraftBuilderConfig(BaseConfig):
    """按需设计使用独立预算，Mod 工具名由连接处明确授予。"""

    enabled: bool = Field(default=True, description="允许 Minecraft 委派建筑设计；空闲不调用模型")
    catalog_uri: str = Field(default="maicraft://building/index", min_length=1, description="新建造器资源目录 URI")
    validate_tool: str = Field(default="builder_validate", min_length=1, description="Mod 设计校验工具原名")
    preview_tool: str = Field(default="", description="Mod 只读设计预览工具原名；空串表示未提供")
    execute_tool: str = Field(
        default="builder_execute", min_length=1, description="Mod 施工受理工具原名，仅父 Agent 使用"
    )
    task_tool: str = Field(default="maicraft_task", min_length=1, description="Mod 任务查询工具原名")
    operation_poll_interval_ms: int = Field(default=1000, ge=1, description="等待已受理设计操作终态的间隔")
    max_steps: int = Field(default=12, ge=1, le=100, description="单个设计任务最多推理轮数")
    task_timeout_ms: int = Field(default=600_000, ge=1000, description="设计总时限，包含资料读取、模型重试与校验")
    max_resource_chars: int = Field(default=24_000, ge=1000, description="单份资料最大字符数")
    max_context_chars: int = Field(default=96_000, ge=4000, description="单次设计推理的上下文字符上限")
    retained_jobs: int = Field(default=10, ge=1, le=100, description="会话中保留的已收尾设计数")

    @model_validator(mode="after")
    def separate_design_from_execution(self) -> "MinecraftBuilderConfig":
        """校验与预览不能绑定施工入口，避免设计中的自纠操作直接改变世界。"""
        names = [self.validate_tool, self.execute_tool]
        if self.preview_tool:
            names.append(self.preview_tool)
        if len(names) != len(set(names)):
            raise ValueError("建造校验、预览与施工必须绑定不同工具")
        return self
