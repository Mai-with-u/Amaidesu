"""游戏存档身份：游戏 Agent 报给主播与存储的"角色眼下在哪个存档里"。

同一款游戏可以有很多个互不相干的世界（Minecraft 的不同存档、不同服务器）。主播记忆里
和世界有关的事（建筑、进度、在那个世界里答应的事）只在同一个存档里才成立，所以要记下
存档、读的时候分清。框架与主播只认两样东西：

- ``id``：游戏给的不透明编号，只做相等比较，不解析、不展示；
- ``name``：给人看的名字（存档名、服务器名），只用来显示，可能重名、会改名。

``id`` 为空串表示这个游戏分存档，但角色眼下不在任何认得出的存档里（还在主菜单、还没
认出世界）。不分存档的游戏（或游戏版本太旧、报不出存档）根本不给 ``GameSave``。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GameSave"]


class GameSave(BaseModel):
    """一个游戏存档的身份：编号只做相等比较，名字只显示。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default="", description="存档编号（不透明串）；空串 = 分存档但眼下不在认得出的存档里")
    name: str = Field(default="", description="给人看的存档名（只显示，不当键）")

    @property
    def known(self) -> bool:
        """是否认出了眼下的存档。"""
        return bool(self.id)

    @property
    def label(self) -> str:
        """给人看的称呼：有名字用名字，没名字用编号前 8 位，存档不明为空串（调用方自己加书名号）。"""
        if self.name:
            return self.name
        return f"编号 {self.id[:8]}" if self.id else ""
