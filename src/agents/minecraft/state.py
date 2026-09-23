"""MinecraftAgent 内存状态（Agent 内部自由）

状态归属：todo（待办）/ notebook（工作笔记）/ reports（近期上报）
全部内存存储，不持久化（Agent 生命周期内有效；跨场次恢复后续按需添加）。

不建模游戏世界数据（health/food/坐标等）——那是 maicraft 执行层的事；
maicraft 返回原样给 LLM 读，本状态只承载 Agent 自己的"指令 + 待办 + 记录"。
任务上下文（用户命令原文、执行历史）在对话消息里，不在本状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# 近期上报保留条数（主播状态查询够用）
MAX_REPORTS = 10


@dataclass(slots=True)
class TodoItem:
    """一条待办（全量文档元素）"""

    content: str
    status: str = "pending"  # pending | in_progress | done


@dataclass(slots=True)
class MinecraftAgentState:
    """MinecraftAgent 内存状态

    Attributes:
        todos: 待办列表（minecraft_todo 全量文档）
        notebook: 工作笔记全文（minecraft_notebook 全量文档）
        reports: 近期上报（emit game.report 时同步；最多 MAX_REPORTS 条）
    """

    todos: List[TodoItem] = field(default_factory=list)
    notebook: str = ""
    reports: List[Dict[str, str]] = field(default_factory=list)

    # ---- todo 文档操作 ----

    def set_todos(self, todos: List[Dict[str, Any]]) -> None:
        """全量覆盖待办（minecraft_todo write；无 id，文档式）。"""
        self.todos = [
            TodoItem(content=str(t.get("content", "")), status=str(t.get("status", "pending"))) for t in todos
        ]

    def todo_doc(self) -> Dict[str, Any]:
        """导出待办文档（minecraft_todo read）。"""
        return {"todos": [{"content": t.content, "status": t.status} for t in self.todos]}

    # ---- notebook 文档操作 ----

    def set_notebook(self, content: str) -> None:
        """全量覆盖工作笔记（minecraft_notebook write）。"""
        self.notebook = content

    def notebook_doc(self) -> Dict[str, Any]:
        """导出工作笔记文档（minecraft_notebook read）。"""
        return {"content": self.notebook}

    # ---- 上报 ----

    def add_report(self, kind: str, content: str, scene: str = "") -> None:
        """新增上报（emit game.report 时同步；保留最近 MAX_REPORTS 条）。"""
        self.reports.append({"kind": kind, "content": content, "scene": scene})
        if len(self.reports) > MAX_REPORTS:
            self.reports = self.reports[-MAX_REPORTS:]

    # ---- 状态导出（minecraft_get_work_log）----

    def to_dict(self) -> Dict[str, Any]:
        """导出完整状态快照（minecraft_get_work_log：todo/notebook/reports 三元组）。"""
        return {
            "todo": self.todo_doc()["todos"],
            "notebook": self.notebook,
            "recent_reports": [dict(r) for r in self.reports],
        }


__all__ = ["MinecraftAgentState", "TodoItem", "MAX_REPORTS"]
