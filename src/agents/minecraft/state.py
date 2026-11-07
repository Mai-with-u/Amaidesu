"""MinecraftAgent 内存状态（Agent 内部自由）

状态归属：todo（待办）/ notebook（工作笔记）/ reports（近期上报）
全部内存存储，不持久化（Agent 生命周期内有效；跨场次恢复后续按需添加）。

三样工作文档按存档分开：换了存档（Minecraft 的另一个世界）时收起旧存档那一份、换上新存档
那一份（新存档是空的），回到老存档还原。读写入口只有本类：``todos`` / ``notebook`` /
``reports`` 永远是眼下这个存档的，任何人往里写都落进眼下这个存档；换存档只经 ``switch_world``。

不建模游戏世界数据（health/food/坐标等）——那是 maicraft 执行层的事；
maicraft 返回原样给 LLM 读，本状态只承载 Agent 自己的"指令 + 待办 + 记录"。
任务原始指令、执行阶段与步数由 MinecraftAgent 随逻辑任务保留，不属于这些可编辑工作文档。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple

# 近期上报保留条数（主播状态查询够用）
MAX_REPORTS = 10


class MinecraftInstruction(NamedTuple):
    """玩家指令保留原二元组协议，系统通知不能冒充新指令来恢复已挂起的任务。"""

    task_id: str
    content: str


class WakeEvenIdle(NamedTuple):
    """闲着也要叫醒模型的通知：交付或挂起后普通通知不开批，这种照样开批。

    两种事用它：计时到点（模型自己用 timer 要的"过 N 秒叫我"）、Mod 自己挂出的决策（死亡恢复：
    没人回答，角色就一直停在死亡屏幕）。两种都只能由游戏 Agent 自己接，闲着不开批就永远没人管。
    排队时不定怎么处理，轮到读它的那一刻才看任务状态：进行中就是一条普通通知；已交付就由它开一个新任务
    （计时的备注、要回答的决策就是这次要做的事）；挂起着就接着原任务做。排队时就定的话，
    排进来那一刻任务还在跑、读到之前刚好交付，这条叫醒会一直留在队列里没人读。
    """

    task_id: str
    content: str


class InteractionNotice(NamedTuple):
    """别的玩家冲角色做了肢体动作的通知：闲着也叫醒模型，但不开任务、不改任务状态。

    蹲起、拍一下、扔东西过来是有人在跟她玩，回一个动作插一下就做完，不是一件要交付的活。
    任务进行中它就是一条普通通知；闲着（已交付，或上报后等主播定夺）时它也叫醒，但那一批只回应互动：
    不开新任务、不续原任务，回完就安静收尾，不代发停了没交付、不改等定夺的委派。要是照计时到点那样开新任务，
    收尾时系统会代发停了没交付，别人蹲一下主播就念一遍。task_id 恒为空串，不参与按任务号撤单。
    """

    task_id: str
    content: str


@dataclass(slots=True)
class TodoItem:
    """一条待办（全量文档元素）"""

    content: str
    status: str = "pending"  # pending | in_progress | done


@dataclass(slots=True)
class _WorldDocs:
    """收起来的某个存档的工作文档。"""

    todos: List[TodoItem]
    notebook: str
    reports: List[Dict[str, str]]


@dataclass(slots=True)
class MinecraftAgentState:
    """MinecraftAgent 内存状态

    Attributes:
        todos: 待办列表（minecraft_todo 全量文档）
        notebook: 工作笔记全文（minecraft_notebook 全量文档）
        reports: 近期上报（emit game.report 时同步；最多 MAX_REPORTS 条）
        world_id: 这三样属于哪个存档；空串 = 还没认出过存档（旧版 Mod 一直是空串）
        shelved: 收起来的别的存档的工作文档（存档编号 → 那一份）
    """

    todos: List[TodoItem] = field(default_factory=list)
    notebook: str = ""
    reports: List[Dict[str, str]] = field(default_factory=list)
    world_id: str = ""
    shelved: Dict[str, _WorldDocs] = field(default_factory=dict)

    # ---- 存档 ----

    def switch_world(self, world_id: str) -> bool:
        """换到某个存档的工作文档，返回是否真的换了一份。

        认出的存档和眼下这份相同：不动。眼下这份还没归属（进程刚起、还没认出过存档）：
        手上的文档就归这个存档——没进世界时角色什么也做不了，写下的只会是这个存档的事。
        否则收起眼下这份、换上那个存档收起过的一份（没有就是空的）。
        """
        if not world_id or world_id == self.world_id:
            return False
        previous, self.world_id = self.world_id, world_id
        if not previous:
            return False
        self.shelved[previous] = _WorldDocs(todos=self.todos, notebook=self.notebook, reports=self.reports)
        docs = self.shelved.pop(world_id, None) or _WorldDocs(todos=[], notebook="", reports=[])
        self.todos, self.notebook, self.reports = docs.todos, docs.notebook, docs.reports
        return True

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
