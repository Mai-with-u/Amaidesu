"""长期笔记的条目格式：MEMORY.md 的解析与渲染，以及开播注入、点名注入、合并清单三种视图（纯函数）。

笔记仍是一份人能直接读、直接改的 Markdown：``## 节名`` 下每行一条 ``- 正文``，
行尾可带一组标签 ``〔未兑现 · 对 老王#12 · 游戏 minecraft · 始于 #43 · 最近 #45〕``。
标签记的是代码要用的状态：

- 约定的结局：``未兑现`` / ``已兑现`` / ``放弃``（只有"答应过的事"一节用）；
- ``对 昵称#观众编号``：答应某位观众的事，只在这位观众发言时才给主播看；
- ``游戏 名字``：只跟某个游戏有关，这个游戏开着时才给主播看；
- ``来自 来源``：不是收播整理记下的，而是别的程序经同步接口写进来的（如在 QQ 群里
  答应的事）。它和模型条目一样会被整理、会淡出，只是不会被当成人手写的行；
- ``始于 #场次`` / ``最近 #场次``：哪一场记下的、最近一次有新证据是哪一场；
- ``人工``：人写的条目，模型只能读不能改，也不会过期；``整理``：模型写的、
  暂时没有别的标签（写回时保证模型条目总带标签）；
- 搁置区的条目另记 ``原 节名``、``搁置 #场次 原因``。

文件里一个标签都没有时按旧格式读（此前由模型整份重写的笔记）：条目都算模型
写的，行尾的"（未兑现）/（已兑现）"读成约定状态。有标签的文件里没带标签的行，
是人后来手写的，算人工条目。

两份从同一版本各自改过的笔记，用 ``merge_three_way`` 按条目合并（笔记的正本放在别处、
本地离线期间也写过时用到）。

合并操作与按场次的淡出规则在 ``note_merge``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Collection, Dict, List, Optional, Tuple

__all__ = [
    "ACTIVE_SECTIONS",
    "ADDABLE_SECTIONS",
    "NoteDocument",
    "NoteItem",
    "SECTION_GOALS",
    "SECTION_JOKES",
    "SECTION_PROMISES",
    "SECTION_SELF",
    "SECTION_SHELVED",
    "SECTION_UNCERTAIN",
    "STATUS_DONE",
    "STATUS_DROPPED",
    "STATUS_OPEN",
    "mark_manual_edits",
    "merge_three_way",
    "parse_notes",
    "person_lines",
    "render_for_injection",
    "render_for_merge",
    "render_notes",
]

SECTION_PROMISES = "答应过的事"
SECTION_JOKES = "老梗与口头禅"
SECTION_GOALS = "长线目标"
SECTION_SELF = "关于我自己"
SECTION_UNCERTAIN = "不确定"
SECTION_SHELVED = "搁置"
# 开播注入的四节（不确定与搁置不注入：前者还没坐实，后者已经淡出）
ACTIVE_SECTIONS = (SECTION_PROMISES, SECTION_JOKES, SECTION_GOALS, SECTION_SELF)
# 模型能新增条目的节（搁置只由生命周期挪进去）
ADDABLE_SECTIONS = ACTIVE_SECTIONS + (SECTION_UNCERTAIN,)
_KNOWN_SECTIONS = ADDABLE_SECTIONS + (SECTION_SHELVED,)

STATUS_OPEN = "未兑现"
STATUS_DONE = "已兑现"
STATUS_DROPPED = "放弃"
_STATUSES = (STATUS_OPEN, STATUS_DONE, STATUS_DROPPED)
_MANUAL_TAG = "人工"
# 模型条目没有别的标签时写这个，免得写回文件后被当成人手写的行
_MODEL_TAG = "整理"
_TAG_SEPARATOR = " · "

_TAG_BLOCK = re.compile(r"〔([^〔〕]*)〕\s*$")
_LEGACY_STATUS = re.compile(r"[（(](未兑现|已兑现)[）)]\s*$")
_VIEWER_TAG = re.compile(r"^对\s*(.*?)#(\d+)$")
_SESSION_TAG = re.compile(r"^(始于|最近)\s*#(\d+)$")
_SHELVED_TAG = re.compile(r"^搁置\s*#(\d+)\s*(.*)$")


@dataclass
class NoteItem:
    """笔记里的一条。``manual`` 为人写的条目；其余标签字段见模块说明。"""

    text: str
    section: str
    status: str = ""
    viewer_id: Optional[int] = None
    viewer_name: str = ""
    game: str = ""
    source: str = ""
    manual: bool = False
    since: Optional[int] = None
    seen: Optional[int] = None
    origin: str = ""
    shelved_at: Optional[int] = None
    shelve_reason: str = ""
    extra: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        """条目的身份：所在节 + 正文（合并结果写回前文件被人改过时，按它把操作对回新版本）。"""
        return (self.section, self.text)

    def tags(self) -> List[str]:
        parts: List[str] = []
        if self.status:
            parts.append(self.status)
        if self.viewer_id is not None:
            parts.append(f"对 {self.viewer_name}#{self.viewer_id}")
        if self.game:
            parts.append(f"游戏 {self.game}")
        if self.source:
            parts.append(f"来自 {self.source}")
        if self.manual:
            parts.append(_MANUAL_TAG)
        if self.since is not None:
            parts.append(f"始于 #{self.since}")
        if self.seen is not None:
            parts.append(f"最近 #{self.seen}")
        if self.origin:
            parts.append(f"原 {self.origin}")
        if self.shelved_at is not None:
            parts.append(f"搁置 #{self.shelved_at} {self.shelve_reason}".rstrip())
        parts += self.extra
        if not parts and not self.manual:
            parts.append(_MODEL_TAG)
        return parts

    def render(self) -> str:
        tags = self.tags()
        return f"- {self.text}" + (f"〔{_TAG_SEPARATOR.join(tags)}〕" if tags else "")


@dataclass
class NoteDocument:
    """整份笔记：标题前的说明文字（原样保留）+ 按出现顺序排列的各节条目。"""

    preamble: List[str] = field(default_factory=list)
    items: List[NoteItem] = field(default_factory=list)
    # 人自己加的节（不在已知节名里）按出现顺序记下，渲染时排在已知节之后
    extra_sections: List[str] = field(default_factory=list)

    def section_items(self, section: str) -> List[NoteItem]:
        return [item for item in self.items if item.section == section]

    def ordered_sections(self) -> List[str]:
        return [*_KNOWN_SECTIONS, *self.extra_sections]


# ---------------------------------------------------------------------------
# 解析与渲染
# ---------------------------------------------------------------------------


def _is_legacy(text: str) -> bool:
    return "〔" not in text


def _parse_tags(item: NoteItem, raw: str) -> None:
    for token in (part.strip() for part in raw.split("·")):
        if not token:
            continue
        if token in _STATUSES:
            item.status = token
        elif token == _MANUAL_TAG:
            item.manual = True
        elif token == _MODEL_TAG:
            continue
        elif match := _VIEWER_TAG.match(token):
            item.viewer_name, item.viewer_id = match.group(1).strip(), int(match.group(2))
        elif token.startswith("游戏 "):
            item.game = token[3:].strip()
        elif token.startswith("来自 "):
            item.source = token[3:].strip()
        elif match := _SESSION_TAG.match(token):
            if match.group(1) == "始于":
                item.since = int(match.group(2))
            else:
                item.seen = int(match.group(2))
        elif token.startswith("原 "):
            item.origin = token[2:].strip()
        elif match := _SHELVED_TAG.match(token):
            item.shelved_at, item.shelve_reason = int(match.group(1)), match.group(2).strip()
        else:
            item.extra.append(token)


def parse_notes(text: str, *, legacy: Optional[bool] = None) -> NoteDocument:
    """把 MEMORY.md 全文读成条目；``legacy`` 缺省按"整份没有标签"自动判断。"""
    legacy = _is_legacy(text) if legacy is None else legacy
    doc = NoteDocument()
    section = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            if section not in _KNOWN_SECTIONS and section not in doc.extra_sections:
                doc.extra_sections.append(section)
            continue
        if not section:
            doc.preamble.append(raw_line.rstrip())
            continue
        body = line[2:].strip() if line[:2] in ("- ", "* ") else line
        item = NoteItem(text=body, section=section)
        tag_match = _TAG_BLOCK.search(body)
        if tag_match:
            item.text = body[: tag_match.start()].strip()
            _parse_tags(item, tag_match.group(1))
        elif legacy:
            status_match = _LEGACY_STATUS.search(body)
            if status_match and section == SECTION_PROMISES:
                item.text, item.status = body[: status_match.start()].strip(), status_match.group(1)
        else:
            item.manual = True
        if section not in _KNOWN_SECTIONS:
            item.manual = True
        if item.text:
            doc.items.append(item)
    return doc


def render_notes(doc: NoteDocument) -> str:
    """条目写回成文件全文：说明文字 → 已知各节 → 人自己加的节；空节不写。"""
    blocks: List[str] = []
    if doc.preamble:
        blocks.append("\n".join(doc.preamble).strip())
    for section in doc.ordered_sections():
        items = doc.section_items(section)
        if items:
            blocks.append("\n".join([f"## {section}", *(item.render() for item in items)]))
    return "\n\n".join(block for block in blocks if block)


def _injection_line(item: NoteItem, *, with_viewer: bool) -> str:
    notes: List[str] = []
    if item.status and item.status != STATUS_OPEN:
        notes.append(item.status)
    elif item.status == STATUS_OPEN:
        notes.append("还没兑现")
    if with_viewer and item.viewer_id is not None:
        notes.append(f"对 {item.viewer_name}")
    if item.game:
        notes.append(item.game)
    # 在别处记下的（如 QQ 群里答应的）注明来处，主播提起时知道是哪边的事
    if item.source:
        notes.append(f"来自 {item.source}")
    return f"- {item.text}" + (f"（{'，'.join(notes)}）" if notes else "")


def _game_visible(item: NoteItem, games: Optional[Collection[str]]) -> bool:
    return not item.game or games is None or item.game in games


def render_for_injection(
    doc: NoteDocument,
    *,
    games: Optional[Collection[str]] = None,
    include_viewer_items: bool = False,
) -> str:
    """开播注入的正文：四个注入节加人自己加的节，去掉标签，只留主播用得上的说明。

    ``games`` 给了时只留不分游戏的和这些游戏的条目（None = 全留）；对某位观众的条目
    默认不放进来（在那位观众发言时另行给出），``include_viewer_items`` 打开时一并列出。
    """
    blocks: List[str] = []
    if doc.preamble:
        blocks.append("\n".join(doc.preamble).strip())
    for section in [*ACTIVE_SECTIONS, *doc.extra_sections]:
        lines = [
            _injection_line(item, with_viewer=include_viewer_items)
            for item in doc.section_items(section)
            if _game_visible(item, games) and (include_viewer_items or item.viewer_id is None)
        ]
        if lines:
            blocks.append("\n".join([f"## {section}", *lines]))
    return "\n\n".join(block for block in blocks if block)


def person_lines(doc: NoteDocument, viewer_id: int) -> List[str]:
    """笔记里只对这位观众的条目（注入节内），渲染成"节名：正文（说明）"。"""
    lines: List[str] = []
    for section in ACTIVE_SECTIONS:
        for item in doc.section_items(section):
            if item.viewer_id == viewer_id:
                lines.append(f"{section}：" + _injection_line(item, with_viewer=False)[2:])
    return lines


def render_for_merge(doc: NoteDocument) -> Tuple[str, List[NoteItem]]:
    """给合并模型看的编号清单（编号从 1 起），连同编号对应的条目。"""
    numbered: List[NoteItem] = []
    lines: List[str] = []
    for section in doc.ordered_sections():
        for item in doc.section_items(section):
            numbered.append(item)
            notes: List[str] = []
            if item.status:
                notes.append(item.status)
            if item.viewer_id is not None:
                notes.append(f"对 {item.viewer_name}（观众 #{item.viewer_id}）")
            if item.game:
                notes.append(f"游戏 {item.game}")
            if item.source:
                notes.append(f"来自 {item.source}")
            if item.origin:
                notes.append(f"原在 {item.origin}")
            if item.manual:
                notes.append("人工写的，只读")
            suffix = f"（{'；'.join(notes)}）" if notes else ""
            lines.append(f"[{len(numbered)}] 〔{section}〕{item.text}{suffix}")
    return "\n".join(lines), numbered


def mark_manual_edits(old_text: str, new_text: str) -> Tuple[str, List[str]]:
    """对照上一次模型写下的版本：人新增或改过正文的行一律记成人工条目，返回新全文与被记成人工的正文。

    没动过的行保持原样；本来就是人工的行不重复报告。带 ``来自`` 标签的行是别的程序经同步
    接口写进来的（如 QQ 群里记下的约定），不算人改的。
    """
    old_doc = parse_notes(old_text)
    new_doc = parse_notes(new_text, legacy=_is_legacy(old_text) and _is_legacy(new_text))
    old_keys = {item.key for item in old_doc.items}
    marked: List[str] = []
    for item in new_doc.items:
        if item.key not in old_keys and not item.manual and not item.source:
            item.manual = True
            marked.append(item.text)
    return render_notes(new_doc), marked


def merge_three_way(base: str, local: str, remote: str) -> Tuple[str, List[str]]:
    """两份从 ``base`` 各自改过的笔记按条目合并，返回合并后的全文与采纳、舍弃了对方哪些改动的说明。

    条目以"所在节 + 正文"认身份（改正文、挪节在这里看作删一条再加一条），逐条比较整行：
    只有一边动过的取那一边，两边都动过且不一样的以本地为准——本地这边是收播整理与人在
    WebUI 里的改动，比对方（只追加约定）更完整。对方新加的条目排到所在节末尾。
    """
    base_doc, local_doc, remote_doc = parse_notes(base), parse_notes(local), parse_notes(remote)
    base_lines = _lines_by_key(base_doc)
    local_lines = _lines_by_key(local_doc)
    remote_items = {item.key: item for item in reversed(remote_doc.items)}
    changes: List[str] = []

    merged = NoteDocument(
        preamble=list(remote_doc.preamble if local_doc.preamble == base_doc.preamble else local_doc.preamble),
        extra_sections=list(dict.fromkeys([*local_doc.extra_sections, *remote_doc.extra_sections])),
    )
    for item in local_doc.items:
        b, r = base_lines.get(item.key), remote_items.get(item.key)
        local_line = item.render()
        if local_line != b:
            # 本地动过（新增或改了标签）：保留本地；对方也改成别的样子时记下舍弃
            if r is not None and r.render() not in (b, local_line):
                changes.append(f"两边都改过，按本地：{item.text}")
            merged.items.append(item)
        elif r is None:
            changes.append(f"采纳对方删除：{item.text}")
        else:
            if r.render() != local_line:
                changes.append(f"采纳对方改动：{item.text}")
            merged.items.append(r)
    seen = set(local_lines)
    for item in remote_doc.items:
        if item.key in seen:
            continue
        seen.add(item.key)
        b = base_lines.get(item.key)
        if b is None:
            changes.append(f"采纳对方新增：〔{item.section}〕{item.text}")
            merged.items.append(item)
        elif item.render() != b:
            changes.append(f"两边都改过，按本地（本地已删）：{item.text}")
    return render_notes(merged), changes


def _lines_by_key(doc: NoteDocument) -> Dict[Tuple[str, str], str]:
    """条目身份 → 整行渲染（同一身份出现多次时取第一次）。"""
    lines: Dict[Tuple[str, str], str] = {}
    for item in doc.items:
        lines.setdefault(item.key, item.render())
    return lines
