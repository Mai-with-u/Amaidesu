"""建造子任务的预算与新 Mod 接口绑定，不包含具体建筑技法。"""

from pydantic import Field, model_validator

from src.modules.config.schemas.base import BaseConfig


class MinecraftBuilderConfig(BaseConfig):
    """按需设计使用独立预算，Mod 工具名由连接处明确授予。"""

    enabled: bool = Field(default=True, description="允许 Minecraft 委派建筑设计；空闲不调用模型")
    catalog_uri: str = Field(default="maicraft://building/index", min_length=1, description="新建造器资源目录 URI")
    execute_tool: str = Field(
        default="maicraft_execute", min_length=1, description="Mod 受理工具原名；设计与施工按操作隔离"
    )
    task_tool: str = Field(default="maicraft_task", min_length=1, description="Mod 任务查询工具原名")
    operation_poll_interval_ms: int = Field(default=1000, ge=1, description="等待已受理设计操作终态的间隔")
    max_steps: int = Field(default=12, ge=1, le=100, description="单个设计任务最多推理轮数")
    task_timeout_ms: int = Field(default=600_000, ge=1000, description="设计总时限，包含资料读取、模型重试与校验")
    max_context_chars: int = Field(default=96_000, ge=4000, description="单次设计推理的上下文字符上限")
    # 教材及已验证产物由代码保留，预算不足时只集中整理先前的设计推理。
    recent_turns: int = Field(default=6, ge=1, le=20, description="整理设计历史时优先保留的完整决策轮数")
    summary_max_chars: int = Field(default=6000, ge=1000, le=12000, description="设计推理摘要的最大字符数")
    retained_jobs: int = Field(default=10, ge=1, le=100, description="会话中保留的已收尾设计数")

    @model_validator(mode="after")
    def separate_query_from_submission(self) -> "MinecraftBuilderConfig":
        """受理与查询属于不同协议操作，查询不能意外触发新的设计或施工。"""
        if self.execute_tool == self.task_tool:
            raise ValueError("Mod 受理与任务查询必须绑定不同工具")
        return self
