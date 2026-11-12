"""游戏工作文档域数据契约（game_work_docs 表的读写模型）

``game_work_docs`` 一行 = 游戏 Agent 在某个存档里的工作文档（工作笔记全文 + 待办列表），
按 ``(game, save_id)`` 唯一覆盖更新，跨进程重启与跨场次保留。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GameWorkDocRecord"]


class GameWorkDocRecord(BaseModel):
    """一个游戏存档的工作文档记录。"""

    model_config = ConfigDict(extra="forbid")

    save_id: str = Field(..., description="游戏报出的存档唯一编号")
    save_name: str = Field(default="", description="存档显示名（仅展示，不作主键）")
    notebook: str = Field(default="", description="工作笔记全文")
    todos: List[Dict[str, str]] = Field(default_factory=list, description="待办列表（content + status）")
    updated_session_id: Optional[int] = Field(default=None, description="最近一次写入所属的场次主键")
    updated_at_ms: int = Field(default=0, description="最近一次写入时刻（毫秒）")
