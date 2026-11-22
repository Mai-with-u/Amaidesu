"""游戏 Agent 自建技能卡数据契约（agent_learned_skills 表的读写模型）。

``agent_learned_skills`` 一行 = 某游戏 Agent 沉淀的一份可复用操作步骤技能卡，
按 ``(game, scope, save_id, name)`` 唯一约束：
- ``scope="world"``：绑定具体存档（``save_id`` 非空），记录本服专属设施或流程；
- ``scope="global"``：跨存档通用（``save_id`` 存空串），记录通用玩法或模组操作流程。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["LearnedSkillRecord", "LearnedSkillScope"]

LearnedSkillScope = Literal["world", "global"]


class LearnedSkillRecord(BaseModel):
    """一条游戏 Agent 自建技能卡持久化记录。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(default=0, description="数据库自增主键")
    game: str = Field(..., description="所属游戏标识")
    save_id: str = Field(default="", description="所属存档编号；global 作用域恒为空串")
    scope: LearnedSkillScope = Field(..., description="作用域：world（本服/本存档）或 global（通用）")
    name: str = Field(..., description="技能名称（同域唯一）")
    trigger: str = Field(default="", description="一句话触发条件（说明何时阅读与使用）")
    content: str = Field(..., description="技能操作步骤正文")
    updated_at_ms: int = Field(default=0, description="最近一次创建或修订时刻（毫秒）")
