"""游戏生物名册域数据契约（game_creatures 表的读写模型）

``game_creatures`` 一行 = 游戏 Agent 名册里的一只生物：角色自己的生物（女仆、狗、马），
或主播起过名的生物。按"游戏 + 世界 + 身份键"区分，世界与游戏由仓储方法参数给出，不在行模型里。
写入与读取都归游戏 Agent（跨场次记得主播起的名字与谁死了），以后 Dashboard 可以查看和手改。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GameCreatureRecord"]


class GameCreatureRecord(BaseModel):
    """名册里的一只生物（一行）。"""

    model_config = ConfigDict(extra="forbid")

    creature_key: str = Field(..., description="身份键：uuid:<UUID>，游戏没报 UUID 时为 look:<种类>|<皮肤>|<命名牌名>")
    type: str = Field(..., description="实体类型注册 ID，例如 touhou_little_maid:maid")
    kind: str = Field(default="", description="种类叫法（女仆、狼）；认不出时为空")
    name: str = Field(default="", description="命名牌上的名字；没有为空")
    skin: str = Field(default="", description="皮肤名（车万女仆）；没有为空")
    owner: str = Field(default="", description="驯服关系（你的 / 自家人的 / 别人的）；野生为空")
    nickname: str = Field(default="", description="主播起的名字；没起为空")
    uuid: str = Field(default="", description="实体 UUID；游戏没报时为空")
    last_seen_ms: int = Field(default=0, description="最后一次在观察里看到它的时刻；没看到过为 0")
    died_ms: Optional[int] = Field(default=None, description="死亡时刻；活着为 None")
    death_note: str = Field(default="", description="死因（死亡播报去掉名字后的部分）；不知道为空")
    died_session_id: Optional[int] = Field(default=None, description="死的那一场的场次主键；不在场次里死的为 None")
    next_session_id: Optional[int] = Field(default=None, description="死后开的第一场的场次主键；还没开过为 None")
