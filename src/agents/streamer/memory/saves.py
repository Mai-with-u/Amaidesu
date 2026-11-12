"""游戏存档：记忆条目挂在哪个存档里。

同一款游戏可以有很多个互不相干的世界（Minecraft 的不同存档、不同服务器）。"屋顶盖好了"
"下次把桥修完"这类话只在说它的那个存档里成立，换了存档再提就是在说别的世界里没有的东西。
所以写记忆时记下存档，读记忆时分清是不是眼下这个存档。

写的一侧（下播整理）：一场里各游戏待过哪些存档、什么时候待的，来自记下了存档的游戏事件；
一句台词是在哪个存档里说的，按台词时刻对上存档段，由代码认，不让模型猜。

读的一侧（常驻简报、线索卡、回想）：一条挂在游戏 G、记着存档 s 的记忆，对照 G 眼下所在的
存档（游戏工具面报的 ``current_saves``）分四种：

| G 眼下 | 记忆记着存档 | 记忆没记存档（旧数据、当时没认出） |
|---|---|---|
| 报不出存档（旧版游戏、没启用 G） | 照旧（不分存档） | 照旧 |
| 还没进存档（主菜单、还没认出） | 别的存档 | 不确定 |
| 在存档 b 里 | s 是 b：本存档；否则别的存档 | 不确定 |

没挂游戏的记忆一律照旧。"别的存档"的事不当成眼前的事推给她：线索卡不催她提起，
简报折成一行、回想逐条注明在哪个存档；"不确定"的照给并注明，不丢不藏。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.modules.storage.models.events import GameSaveSpan
from src.modules.types.game_save import GameSave

__all__ = ["SavePlace", "SaveTimeline", "canonical_save_names", "place_note", "place_of", "session_note"]


class SavePlace(Enum):
    """一条记忆相对游戏眼下所在存档的位置。"""

    # 不分存档：没挂游戏，或这个游戏眼下报不出存档——与没有存档隔离时一样处理
    UNSPLIT = "unsplit"
    SAME = "same"
    OTHER = "other"
    # 这个游戏分存档，但这条记忆没记下是哪个存档
    UNSURE = "unsure"


def place_of(game: Optional[str], save_id: str, current: Mapping[str, GameSave]) -> SavePlace:
    """一条挂在 ``game``、记着存档 ``save_id`` 的记忆，相对眼下存档的位置（判定表见模块说明）。

    眼下还没进存档时，记着存档的记忆一律算别的存档：分不清是不是同一个世界，就不当成眼前的事。
    """
    if not game:
        return SavePlace.UNSPLIT
    now = current.get(game)
    if now is None:
        return SavePlace.UNSPLIT
    if not save_id:
        return SavePlace.UNSURE
    if now.known and now.id == save_id:
        return SavePlace.SAME
    return SavePlace.OTHER


def canonical_save_names(items: Iterable[Tuple[Optional[str], str, str]]) -> Dict[Tuple[str, str], str]:
    """同一存档号先后出现过多个名字时，按时间顺序归并成最后一次非空的名字。

    同一个世界在服务器重命名或版本演进后可能留下不同的 ``save_name``，但 ``save_id`` 不变；
    按 ``(game, save_id)`` 扫描从旧到新的 ``(game, save_id, save_name)``，让新名字覆盖旧名字、
    空名字不冲掉已有名字，后续折叠行与整理基底统一显示归并后的名字，避免把同一个世界当成两个。
    """
    resolved: Dict[Tuple[str, str], str] = {}
    for game, save_id, save_name in items:
        if not game or not save_id:
            continue
        name = (save_name or "").strip()
        if name:
            resolved[(game, save_id)] = name
        else:
            resolved.setdefault((game, save_id), "")
    return resolved


def place_note(game: Optional[str], save_id: str, save_name: str, current: Mapping[str, GameSave]) -> str:
    """给她看的一句"这是哪个存档里的事"；不分存档时为空串（不加注）。

    确认就是眼下存档且眼下存档有名字时，直接用眼下存档的现名，避免旧记录留着改名前的旧称呼；
    眼下还没进存档（掉线或还在主菜单）时如实写"眼下还没进存档"，不写死成"不是眼下这个存档"，
    免得角色短暂掉线时以为换了世界。
    """
    place = place_of(game, save_id, current)
    now = current.get(game) if game else None
    if place is SavePlace.SAME:
        label = now.label if now is not None and now.name else GameSave(id=save_id, name=save_name).label
        return f"在 {game}「{label}」里，就是眼下这个存档"
    label = GameSave(id=save_id, name=save_name).label
    if place is SavePlace.OTHER:
        if now is not None and not now.known:
            return f"在 {game}「{label}」里（{game} 眼下还没进存档）"
        return f"在 {game}「{label}」里，不是眼下这个存档"
    if place is SavePlace.UNSURE:
        return f"在 {game} 里，不确定是哪个存档"
    return ""


def session_note(spans: Sequence[GameSaveSpan], current: Mapping[str, GameSave]) -> str:
    """一场过去的直播里，眼下分存档的游戏只在别的存档里玩过时的提示；其余为空串。

    回想翻出那场她说过的话（"屋顶盖好了"）时，看得出那是在别的世界里说的。那场也进过眼下
    这个存档、或那场没记下存档的，不加注：说不准那句话是在哪边说的。
    """
    timeline = SaveTimeline(spans)
    notes: List[str] = []
    for game in sorted(current):
        visited = timeline.saves_of(game)
        if not visited or any(place_of(game, save.id, current) is SavePlace.SAME for save in visited):
            continue
        labels = "".join(f"「{save.label}」" for save in visited)
        notes.append(f"那场 {game} 在{labels}里，不是眼下这个存档")
    return "；".join(notes)


class SaveTimeline:
    """一场里各游戏先后待过的存档段：按台词时刻认出一句话是在哪个存档里说的。"""

    def __init__(self, spans: Sequence[GameSaveSpan] = ()) -> None:
        self._spans: Dict[str, List[GameSaveSpan]] = {}
        for span in sorted(spans, key=lambda item: item.started_ms):
            self._spans.setdefault(span.game, []).append(span)

    def saves_of(self, game: str) -> List[GameSave]:
        """这个游戏这一场待过的存档，按第一次进去的先后排，同一存档只列一次（名字取最后报的）。"""
        names: Dict[str, str] = {}
        for span in self._spans.get(game, []):
            names[span.save_id] = span.save_name or names.get(span.save_id, "")
        return [GameSave(id=save_id, name=name) for save_id, name in names.items()]

    def save_at(self, game: Optional[str], at_ms: int) -> GameSave:
        """一句挂在 ``game`` 上的话是在哪个存档里说的；认不出时给空存档（读的时候按"不确定"处理）。

        - 没挂游戏，或这个游戏这一场一条带存档的事件都没记下（旧版游戏、短场次）→ 认不出；
        - 这一场只待过一个存档 → 就是它；
        - 待过几个 → 台词时刻之前最近开始的那一段；台词比所有段都早（进世界后还没记下事件
          就说了）→ 最早那一段。
        """
        spans = self._spans.get(game or "", [])
        if not spans:
            return GameSave()
        chosen = spans[0]
        for span in spans:
            if span.started_ms > at_ms:
                break
            chosen = span
        # 名字取这个存档最后一次报的（中途改过名时显示新名）
        latest = {save.id: save.name for save in self.saves_of(chosen.game)}
        return GameSave(id=chosen.save_id, name=latest.get(chosen.save_id, chosen.save_name))
