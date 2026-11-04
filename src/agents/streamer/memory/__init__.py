"""主播记忆子包：跨场次记忆的读写零件（只有账本一条线）。

- ``render``  两档写法与常驻简报的渲染；
- ``brief``   常驻简报：开播时拼一次，整场不变；
- ``cues``    线索推送：每批按弹幕与开着的游戏算这一批想起来的事；
- ``recall``  回想工具 ``memory_recall``；
- ``settle``  下播整理：把一场正式直播整理成承诺与场次档案。

当场的工作记忆（对话时间线、前情提要）不在这里；存储表、仓储与合规删除在 ``src/modules/storage/``。
"""

from __future__ import annotations

__all__: list[str] = []
