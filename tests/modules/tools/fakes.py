"""工具域测试替身：报存档与情境的游戏工具面。

全库只此一份：注册表测"收哪些游戏的存档与情境"、主播测"按眼下存档读记忆"都用它。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation

__all__ = ["SavedGameProvider"]


class SavedGameProvider(BaseToolProvider):
    """分存档的游戏工具面替身：一个动作工具（``<名字>_act``）+ 可拨动的"眼下在哪个存档"。

    ``save`` 为 None 表示这个游戏报不出存档（旧版游戏）；``GameSave()`` 表示还没进存档。
    测试直接改 ``save`` 模拟换存档、回主菜单；``raise_on_save`` 模拟读存档出错，
    ``visible_to`` 给动作工具设可见名单（缺省只对主播可见）。``situation`` 是"眼下离哪些记住的
    地点多远"，None 表示报不出情境；``raise_on_situation`` 模拟读情境出错。
    """

    category = "game"

    def __init__(
        self,
        name: str = "minecraft",
        save: Optional[GameSave] = None,
        *,
        visible_to: Optional[List[str]] = None,
        raise_on_save: bool = False,
        situation: Optional[GameSituation] = None,
        raise_on_situation: bool = False,
    ) -> None:
        self._name = name
        self.save = save
        self.situation = situation
        self._visible_to = visible_to
        self._raise_on_save = raise_on_save
        self._raise_on_situation = raise_on_situation

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self) -> List[ToolSpec]:
        return [ToolSpec(name="act", description="动作", kind="sync", provider=self._name, visible_to=self._visible_to)]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    def guide_for(self, agent_name: str) -> str:
        return "玩法"

    def current_save(self) -> Optional[GameSave]:
        if self._raise_on_save:
            raise RuntimeError("读不到存档")
        return self.save

    def current_situation(self) -> Optional[GameSituation]:
        if self._raise_on_situation:
            raise RuntimeError("读不到情境")
        return self.situation
