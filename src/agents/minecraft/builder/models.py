"""建筑设计的任务外壳；具体几何内容由 Mod 的 JSON Schema 定义。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.modules.tools.tasks import TaskStatus

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class BuildRequest(BaseModel):
    """父 Agent 转交目标与现场事实，避免在委派前自己推演整个建筑。"""

    model_config = ConfigDict(extra="forbid")
    requirements: Text
    intent: Literal["design", "build"] = "build"
    context: dict[str, Any] = Field(default_factory=dict)
    request_key: str = ""


class BuildResource(BaseModel):
    """教程目录只先提供摘要，任务选择后才读取正文。"""

    uri: Text
    title: Text
    summary: str = ""
    revision: Text
    requires: list[str] = Field(default_factory=list)
    compatible_schema_revisions: list[str] = Field(default_factory=list)


class BuildCatalog(BaseModel):
    """固定资源协议外壳，允许 Mod 添加元数据与设计能力。"""

    protocol_version: Literal[1]
    revision: Text
    design_schema_uri: Text
    design_schema_revision: Text
    edit_schema_ref: str = ""
    # 预算与版本覆盖范围来自 Mod，契约一致不代表场地、库存或可达性已验证。
    planning_budget: dict[str, int] = Field(default_factory=dict)
    revision_scope: str = ""
    capabilities: list[str] = Field(default_factory=list)
    resources: list[BuildResource] = Field(default_factory=list)


class BuildResult(BaseModel):
    """保留完整草稿以供修改，对父 Agent 只交付可执行引用和依据。"""

    artifact_ref: Text
    summary: Text
    # 对象编辑后的完整模型保存在 Mod 场景资源中，不在客户端重写一套合并算法。
    design: dict[str, Any] | None = None
    capability_revision: str
    design_schema_revision: str
    validation: dict[str, Any]
    resource_refs: dict[str, str] = Field(default_factory=dict)


class BuildJob(BaseModel):
    """任务终态留在 Minecraft 会话，通用账本移除后仍能取回设计。"""

    task_id: str
    parent_task_id: str = ""
    request: BuildRequest
    request_revision: int = 1
    status: TaskStatus = "accepted"
    phase: str = "accepted"
    summary: str = "设计请求已受理"
    result: BuildResult | None = None
    dismissed: bool = False
    superseded_by: str = ""
    execution_task_id: str = ""
    execution_status: str = "not_started"

    def snapshot(self) -> dict[str, Any]:
        """状态通知不携带建筑 JSON，减少游戏决策上下文占用。"""
        result = self.model_dump(exclude={"request", "result"})
        result["intent"] = self.request.intent
        if self.result is not None:
            result["result"] = self.result.model_dump(exclude={"design", "validation"})
            result["result"]["validated"] = self.result.validation.get("valid") is True
        return result

    def pending_id(self) -> str:
        """要求建好的任务在设计交付后仍未完成，必须等实际施工成功。"""
        if self.dismissed or self.superseded_by:
            return ""
        if self.status in {"accepted", "running", "waiting_for_decision"}:
            return self.task_id
        if self.status in {"failed", "timeout"}:
            return self.task_id
        if self.request.intent == "build" and self.execution_status != "succeeded":
            return self.execution_task_id or self.task_id
        return ""
