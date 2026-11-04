"""游戏存档：记忆条目挂在哪个存档里。

同一款游戏可以有很多个互不相干的世界（Minecraft 的不同存档、不同服务器）。"屋顶盖好了"
"下次把桥修完"这类话只在说它的那个存档里成立，换了存档再提就是在说别的世界里没有的东西。
所以写记忆时记下存档，读记忆时分清是不是眼下这个存档。

写的一侧（下播整理）：一场里各游戏待过哪些存档、什么时候待的，来自记下了存档的游戏事件；
一句台词是在哪个存档里说的，按台词时刻对上存档段，由代码认，不让模型猜。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from src.modules.storage.models.events import GameSaveSpan
from src.modules.types.game_save import GameSave

__all__ = ["SaveTimeline"]


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
