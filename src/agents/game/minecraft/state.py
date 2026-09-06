"""MinecraftAgent 内存状态（Agent 内部自由）

状态归属：todo（待办）/ memo（备忘录）/ milestones（里程碑）/ current_goal（当前目标）
全部内存存储，不持久化（Agent 生命周期内有效；跨场次恢复后续按需添加）。

不建模游戏世界数据（health/food/坐标等）——那是 maicraft 执行层的事；
maicraft 返回原样给 LLM 读，本状态只承载 Agent 自己的"指令 + 待办 + 记录"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# 里程碑保留条数（近期叙事够用）
MAX_MILESTONES = 10


@dataclass(slots=True)
class TodoItem:
    """一条待办（全量文档元素）"""

    content: str
    status: str = "pending"  # pending | in_progress | done


@dataclass(slots=True)
class MinecraftAgentState:
    """MinecraftAgent 内存状态

    Attributes:
        current_goal: 当前目标（来自 set_goal 命令；无则空）
        todos: 待办列表（mc_todo 全量文档）
        memo: 备忘录全文（mc_memo 全量文档）
        milestones: 近期里程碑（emit game.milestone 时同步；最多 MAX_MILESTONES 条）
    """

    current_goal: str = ""
    todos: List[TodoItem] = field(default_factory=list)
    memo: str = ""
    milestones: List[str] = field(default_factory=list)

    # ---- todo 文档操作 ----

    def set_todos(self, todos: List[Dict[str, Any]]) -> None:
        """全量覆盖待办（mc_todo write；无 id，文档式）。"""
        self.todos = [
            TodoItem(content=str(t.get("content", "")), status=str(t.get("status", "pending"))) for t in todos
        ]

    def todo_doc(self) -> Dict[str, Any]:
        """导出待办文档（mc_todo read）。"""
        return {"todos": [{"content": t.content, "status": t.status} for t in self.todos]}

    # ---- memo 文档操作 ----

    def set_memo(self, content: str) -> None:
        """全量覆盖备忘录（mc_memo write）。"""
        self.memo = content

    def memo_doc(self) -> Dict[str, Any]:
        """导出备忘录文档（mc_memo read）。"""
        return {"content": self.memo}

    # ---- 里程碑 ----

    def add_milestone(self, message: str) -> None:
        """新增里程碑（emit game.milestone 时同步；保留最近 MAX_MILESTONES 条）。"""
        self.milestones.append(message)
        if len(self.milestones) > MAX_MILESTONES:
            self.milestones = self.milestones[-MAX_MILESTONES:]

    # ---- set_goal ----

    def set_goal(self, goal: str) -> None:
        """接收主播命令（set_goal）——目标级意图，写入 current_goal。"""
        self.current_goal = goal

    # ---- 状态导出（mc_get_state）----

    def to_dict(self) -> Dict[str, Any]:
        """导出完整状态快照（mc_get_state：goal/todo/memo/milestones 四元组）。"""
        return {
            "current_goal": self.current_goal,
            "todo": self.todo_doc()["todos"],
            "memo": self.memo,
            "recent_milestones": list(self.milestones),
        }


__all__ = ["MinecraftAgentState", "TodoItem", "MAX_MILESTONES"]
