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
from typing import Any, Dict, List, NamedTuple, Sequence, Tuple

# 近期上报保留条数（主播状态查询够用）
MAX_REPORTS = 10
# 工作笔记全文长度上限（字符）：跨重启按存档保留，写满时要求模型删掉已完成或失效的旧条目
MAX_NOTEBOOK_CHARS = 2000
# 新任务开局注入"上次在这个存档里的笔记"时的截断上限（字符）
LAST_SESSION_NOTEBOOK_MAX_CHARS = 1200
# 独立钉住规矩槽上限（持久 + 本场临时合计条数上限，以及单条最大字符数）
MAX_PINNED_RULES = 12
MAX_RULE_CHARS = 120


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
    pinned_rules: List[str] = field(default_factory=list)
    session_rules: List[str] = field(default_factory=list)
    notebook_from_last_session: bool = False
    loaded_from_repo: bool = False
    dirty: bool = False


@dataclass(slots=True)
class MinecraftAgentState:
    """MinecraftAgent 内存状态（配合仓储按存档跨重启持久化笔记、待办与长期钉住规矩）

    Attributes:
        todos: 待办列表（minecraft_todo 全量文档）
        notebook: 工作笔记全文（minecraft_notebook 全量文档，上限 MAX_NOTEBOOK_CHARS）
        reports: 近期上报（emit game.report 时同步；最多 MAX_REPORTS 条）
        pinned_rules: 当前存档长期钉住规矩（持久化，抗历史折叠与笔记覆写）
        session_rules: 本场临时钉住规矩/同伴硬拒绝（不落库，新场次自动清空）
        world_id: 这套文档属于哪个存档；空串 = 还没认出过存档（旧版 Mod 一直是空串）
        shelved: 收起来的别的存档的工作文档（存档编号 → 那一份）
        notebook_from_last_session: 当前笔记是否读自上一场（本场尚未改写）
        loaded_from_repo: 当前存档是否已从仓储加载过一次
        dirty: 当前存档自上次写库后是否有未落库的内存改动
    """

    todos: List[TodoItem] = field(default_factory=list)
    notebook: str = ""
    reports: List[Dict[str, str]] = field(default_factory=list)
    pinned_rules: List[str] = field(default_factory=list)
    session_rules: List[str] = field(default_factory=list)
    world_id: str = ""
    shelved: Dict[str, _WorldDocs] = field(default_factory=dict)
    notebook_from_last_session: bool = False
    loaded_from_repo: bool = False
    dirty: bool = False

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
        self.shelved[previous] = _WorldDocs(
            todos=self.todos,
            notebook=self.notebook,
            reports=self.reports,
            pinned_rules=self.pinned_rules,
            session_rules=self.session_rules,
            notebook_from_last_session=self.notebook_from_last_session,
            loaded_from_repo=self.loaded_from_repo,
            dirty=self.dirty,
        )
        docs = self.shelved.pop(world_id, None) or _WorldDocs(todos=[], notebook="", reports=[])
        self.todos = docs.todos
        self.notebook = docs.notebook
        self.reports = docs.reports
        self.pinned_rules = docs.pinned_rules
        self.session_rules = docs.session_rules
        self.notebook_from_last_session = docs.notebook_from_last_session
        self.loaded_from_repo = docs.loaded_from_repo
        self.dirty = docs.dirty
        return True

    def apply_persisted_docs(
        self,
        *,
        notebook: str,
        todos: List[Dict[str, Any]],
        pinned_rules: Sequence[str] = (),
        from_last_session: bool,
        keep_active_todos: bool = False,
    ) -> None:
        """首次认出某个存档时把库里保存的笔记、待办与长期钉住规矩装进当前存档（不覆盖进程内已写过的新内容）。"""
        if self.loaded_from_repo:
            return
        if not self.notebook and notebook:
            self.notebook = notebook[:MAX_NOTEBOOK_CHARS]
            self.notebook_from_last_session = bool(from_last_session)
        if not keep_active_todos and not self.todos and todos:
            self.todos = [
                TodoItem(content=str(t.get("content", "")), status=str(t.get("status", "pending"))) for t in todos
            ]
        if not self.pinned_rules and pinned_rules:
            # 读库回填长期规矩：保序去重、单条截断并遵守总上限，确保不会撑爆固定层
            seen: set[str] = set()
            loaded_rules: List[str] = []
            for raw in pinned_rules:
                cleaned = " ".join(str(raw or "").split())[:MAX_RULE_CHARS]
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                loaded_rules.append(cleaned)
                if len(loaded_rules) >= MAX_PINNED_RULES:
                    break
            self.pinned_rules = loaded_rules
        self.loaded_from_repo = True

    # ---- todo 文档操作 ----

    def set_todos(self, todos: List[Dict[str, Any]]) -> None:
        """全量覆盖待办（minecraft_todo write；无 id，文档式）。"""
        self.todos = [
            TodoItem(content=str(t.get("content", "")), status=str(t.get("status", "pending"))) for t in todos
        ]
        self.dirty = True

    def clear_todos_for_new_task(self) -> None:
        """新任务开局清空上一任务的阶段待办（原先已有待办时标脏以便同步落库）。"""
        if self.todos:
            self.todos = []
            self.dirty = True

    def todo_doc(self) -> Dict[str, Any]:
        """导出待办文档（minecraft_todo read）。"""
        return {"todos": [{"content": t.content, "status": t.status} for t in self.todos]}

    # ---- notebook 文档操作 ----

    def set_notebook(self, content: str) -> None:
        """全量覆盖工作笔记（minecraft_notebook write；本场新写后取消上一场标记）。"""
        self.notebook = content[:MAX_NOTEBOOK_CHARS]
        self.notebook_from_last_session = False
        self.dirty = True

    def notebook_doc(self) -> Dict[str, Any]:
        """导出工作笔记文档（minecraft_notebook read）。"""
        return {"content": self.notebook}

    # ---- 独立钉住规矩槽（抗折叠、与工作笔记分离）----

    def rules_doc(self) -> Dict[str, Any]:
        """导出当前存档生效的长期与本场临时规矩清单（附短编号 P1.. / S1..）。"""
        items: List[Dict[str, Any]] = [
            {"id": f"P{idx}", "rule": text, "durable": True} for idx, text in enumerate(self.pinned_rules, start=1)
        ]
        items.extend(
            {"id": f"S{idx}", "rule": text, "durable": False} for idx, text in enumerate(self.session_rules, start=1)
        )
        return {
            "rules": items,
            "pinned_rules": list(self.pinned_rules),
            "session_rules": list(self.session_rules),
            "count": len(items),
            "limit": MAX_PINNED_RULES,
        }

    def add_rule(self, rule: str, *, durable: bool = True) -> Tuple[bool, str, bool]:
        """增量钉住一条规矩，返回 ``(成功与否, 分配的短编号或错误原因, 持久规矩是否变化)``。

        内容按空白折叠后精确去重；若同一条规矩已存在于另一存储档（持久 <-> 本场临时），
        原地搬移不重复占位，且因持久列表变化标脏。
        """
        cleaned = " ".join((rule or "").split())
        if not cleaned:
            return False, "规矩内容不能为空", False
        if len(cleaned) > MAX_RULE_CHARS:
            return (
                False,
                f"单条规矩超过 {MAX_RULE_CHARS} 字上限（当前 {len(cleaned)} 字），请精简后再记",
                False,
            )
        target = self.pinned_rules if durable else self.session_rules
        other = self.session_rules if durable else self.pinned_rules
        prefix = "P" if durable else "S"

        if cleaned in target:
            return True, f"{prefix}{target.index(cleaned) + 1}", False
        if cleaned in other:
            # 在持久规矩与本场临时规矩之间调整归属：原地搬移不增加总数
            other.remove(cleaned)
            target.append(cleaned)
            self.dirty = True
            return True, f"{prefix}{len(target)}", True
        if len(self.pinned_rules) + len(self.session_rules) >= MAX_PINNED_RULES:
            return (
                False,
                f"钉住规矩已达 {MAX_PINNED_RULES} 条上限，请先用 action=remove 删除过时条目后再添加",
                False,
            )
        target.append(cleaned)
        if durable:
            self.dirty = True
        return True, f"{prefix}{len(target)}", durable

    def remove_rule(self, *, rule_id: str = "", rule: str = "") -> Tuple[bool, str, bool]:
        """按短编号（如 P1/S1/纯序号）或内容匹配删除一条钉住规矩，返回 ``(成功与否, 已删正文或错误, 持久是否变化)``。"""
        id_token = (rule_id or "").strip()
        text_token = " ".join((rule or "").split())
        if not id_token and text_token:
            upper_text = text_token.upper()
            if (len(upper_text) >= 2 and upper_text[0] in ("P", "S") and upper_text[1:].isdigit()) or (
                upper_text.isdigit()
            ):
                id_token = upper_text

        if id_token:
            upper = id_token.upper()
            if upper.startswith("P") and upper[1:].isdigit():
                idx = int(upper[1:]) - 1
                if 0 <= idx < len(self.pinned_rules):
                    removed = self.pinned_rules.pop(idx)
                    self.dirty = True
                    return True, removed, True
                return False, f"未找到编号 {id_token} 对应的长期规矩", False
            if upper.startswith("S") and upper[1:].isdigit():
                idx = int(upper[1:]) - 1
                if 0 <= idx < len(self.session_rules):
                    removed = self.session_rules.pop(idx)
                    return True, removed, False
                return False, f"未找到编号 {id_token} 对应的本场临时规矩", False
            if upper.isdigit():
                combined_idx = int(upper) - 1
                if 0 <= combined_idx < len(self.pinned_rules):
                    removed = self.pinned_rules.pop(combined_idx)
                    self.dirty = True
                    return True, removed, True
                session_idx = combined_idx - len(self.pinned_rules)
                if 0 <= session_idx < len(self.session_rules):
                    removed = self.session_rules.pop(session_idx)
                    return True, removed, False
                return False, f"未找到序号 {id_token} 对应的钉住规矩", False

        if text_token:
            if text_token in self.pinned_rules:
                self.pinned_rules.remove(text_token)
                self.dirty = True
                return True, text_token, True
            if text_token in self.session_rules:
                self.session_rules.remove(text_token)
                return True, text_token, False
            # 无精确命中时尝试唯一子串匹配，避免模型漏写几个标点就删不掉
            pinned_hits = [idx for idx, item in enumerate(self.pinned_rules) if text_token in item]
            session_hits = [idx for idx, item in enumerate(self.session_rules) if text_token in item]
            total_hits = len(pinned_hits) + len(session_hits)
            if total_hits == 1:
                if pinned_hits:
                    removed = self.pinned_rules.pop(pinned_hits[0])
                    self.dirty = True
                    return True, removed, True
                removed = self.session_rules.pop(session_hits[0])
                return True, removed, False
            if total_hits > 1:
                return False, "匹配到多条钉住规矩，请改用 action=list 查看后传精确 id（如 P1、S1）删除", False

        return False, "未找到对应的钉住规矩，请先用 action=list 查看当前编号与内容", False

    def clear_session_rules(self) -> bool:
        """清空当前存档与所有收起存档的本场临时规矩（开新场或下播收工时调用），返回当前存档是否有清理。"""
        had_current = bool(self.session_rules)
        self.session_rules = []
        for docs in self.shelved.values():
            docs.session_rules = []
        return had_current

    # ---- 上报 ----

    def add_report(self, kind: str, content: str, scene: str = "") -> None:
        """新增上报（emit game.report 时同步；保留最近 MAX_REPORTS 条）。"""
        self.reports.append({"kind": kind, "content": content, "scene": scene})
        if len(self.reports) > MAX_REPORTS:
            self.reports = self.reports[-MAX_REPORTS:]

    # ---- 状态导出（minecraft_get_work_log）----

    def to_dict(self) -> Dict[str, Any]:
        """导出完整状态快照（minecraft_get_work_log：todo/notebook/pinned_rules/session_rules/reports）。"""
        return {
            "todo": self.todo_doc()["todos"],
            "notebook": self.notebook,
            "pinned_rules": list(self.pinned_rules),
            "session_rules": list(self.session_rules),
            "recent_reports": [dict(r) for r in self.reports],
        }


__all__ = [
    "LAST_SESSION_NOTEBOOK_MAX_CHARS",
    "MAX_NOTEBOOK_CHARS",
    "MAX_PINNED_RULES",
    "MAX_REPORTS",
    "MAX_RULE_CHARS",
    "MinecraftAgentState",
    "TodoItem",
]
