"""
技能检视页 Schema（只读快照）

技能 = "怎么把一类事做成"的经验文档：本地技能内聚在消费方包内（``src/**/skills/``），
远端玩法技能由 MaiCraft 经 MCP ``skill://`` 资源在运行期登记（内存持有，不落库）。
本组模型只服务检视：目录、来源与远端登记状态，不承载任何写语义。
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class SkillItem(BaseModel):
    """技能目录条目（本地与远端同形，用 origin 与 path/uri 区分来源）"""

    name: str = Field(description="技能名（LLM 读取正文时填写的参数）")
    description: str = Field(description="一句话说明什么时候用")
    category: str = Field(description="技能分类（目录按此分组）")
    origin: str = Field(description="来源：local 为本地文档，其余为登记来源名（如 MaiCraft）")
    agents: List[str] = Field(description="受众 Agent 注册名列表，或单独的 '*'")
    path: str = Field(default="", description="本地技能的来源文件（仓库相对路径）；远端技能为空")
    uri: str = Field(default="", description="远端技能的 skill:// 地址；本地技能为空")


class SkillRemoteStatus(BaseModel):
    """远端技能登记状态（按可观测性逐级降级，前端据此区分空态文案）"""

    observable: bool = Field(description="游戏 Agent 是否可观测（已注册且实例可取）")
    agent_running: Optional[bool] = Field(default=None, description="游戏 Agent 是否在运行；不可观测时为 None")
    remote_loaded: Optional[bool] = Field(
        default=None,
        description="本轮连接是否已读过 MaiCraft 技能目录（读到空目录也算已读）；不可观测时为 None",
    )
    remote_count: int = Field(default=0, description="技能库中 MaiCraft 来源的条目数")


class SkillsSnapshotResponse(BaseModel):
    """技能检视快照：全量目录（不做受众筛选）+ 远端登记状态"""

    entries: List[SkillItem] = Field(description="全部技能条目（本地 + 已登记的远端，按分类与名字排序）")
    remote: SkillRemoteStatus = Field(description="远端技能登记状态")


class SkillBodyResponse(BaseModel):
    """技能正文（本地直接读仓库文档；远端经游戏 Agent 向 MaiCraft 按需代读）"""

    name: str = Field(description="技能名")
    path: str = Field(description="本地技能的来源文件（仓库相对路径）；远端技能为空")
    content: str = Field(description="正文（不含 frontmatter）")
    source: str = Field(default="local", description="正文来源：local 为本地文档，其余为代读来源名（如 MaiCraft）")


__all__ = ["SkillBodyResponse", "SkillItem", "SkillRemoteStatus", "SkillsSnapshotResponse"]
