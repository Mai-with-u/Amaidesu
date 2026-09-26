"""
Agent 控制面 Schema

定义 /api/v1/agents 控制面端点的请求与响应数据模型。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class AgentSummary(BaseModel):
    """Agent 列表条目（运行态 + 配置态 enabled 标记）"""

    name: str
    description: str = ""
    state: str
    heartbeat_ms: int
    is_alive: bool
    restart_count: int
    enabled: bool


class AgentListResponse(BaseModel):
    """Agent 列表响应"""

    agents: list[AgentSummary] = []


class AgentStateResponse(BaseModel):
    """单个 Agent 运行状态响应"""

    name: str
    description: str = ""
    state: str
    heartbeat_ms: int
    is_alive: bool
    restart_count: int


class AgentControlAction(str, Enum):
    """Agent 框架级控制动作"""

    PAUSE = "pause"
    RESUME = "resume"
    SHUTDOWN = "shutdown"
    RESTART = "restart"


class AgentControlRequest(BaseModel):
    """Agent 控制请求体。

    ``confirm``：shutdown / restart 属高风险动作（停机 / 重建实例），缺省
    False，缺确认时端点以 400 拒绝；pause / resume 不要求。
    """

    action: str
    confirm: bool = False


class AgentControlResponse(BaseModel):
    """Agent 控制响应"""

    success: bool
    action: str
    name: str
    message: str
    state: Optional[str] = None


class AgentPromptRequest(BaseModel):
    """Agent 递话请求体（运营提醒/插话：纯文本留言，不派新任务）"""

    content: str


class AgentPromptResponse(BaseModel):
    """Agent 递话响应"""

    delivered: bool


class AgentCancelTaskResponse(BaseModel):
    """任务硬取消响应"""

    cancelled: bool
