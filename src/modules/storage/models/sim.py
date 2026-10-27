"""模拟器域数据契约（sim_personas / sim_gifts 两表的读模型）

模拟器运行时两表的持久化契约：常驻人设（sim_personas）与礼物目录
（sim_gifts）。消费方为模拟器的人设池与礼物生成器（启动期从库加载到
内存缓存）与 Dashboard 模拟器管理页。

表内布尔列以 INTEGER 0/1 存储，模型侧收敛为 ``bool``；时间字段统一
毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["SimPersona", "SimGift"]


class SimPersona(BaseModel):
    """``sim_personas`` 一行：一位常驻模拟观众人设。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键（自增）")
    user_id: str = Field(..., description="人设的模拟用户 ID（业务主键，唯一）")
    user_nickname: str = Field(..., description="昵称")
    role: str = Field(..., description="角色（粉丝/路人等，取值见模拟器角色枚举）")
    personality: str = Field(..., description="性格描述")
    speaking_style: str = Field(..., description="说话风格描述")
    fans_medal_level: int = Field(default=0, description="粉丝牌等级")
    guard_level: int = Field(default=0, description="舰队等级")
    is_active: bool = Field(default=True, description="是否启用（表内 INTEGER 0/1）")
    messages_generated: int = Field(default=0, description="该人设累计生成的消息条数")
    created_at_ms: int = Field(..., description="创建时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最近更新时刻（Unix epoch 毫秒）")


class SimGift(BaseModel):
    """``sim_gifts`` 一行：一条礼物目录条目。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键（自增）")
    gift_id: str = Field(..., description="礼物 ID（业务主键，唯一）")
    gift_name: str = Field(..., description="礼物名")
    category: str = Field(..., description="礼物类别（normal / sc 等）")
    weight: int = Field(default=1, description="随机抽取权重")
    data_type: str = Field(..., description="数据类型（gift / super_chat 等，决定发放行为）")
    sc_amount_rmb: Optional[int] = Field(default=None, description="SC 类礼物的金额（人民币元）；非 SC 为 None")
    unit_price: int = Field(default=0, description="礼物标价（金瓜子）")
    created_at_ms: int = Field(..., description="创建时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最近更新时刻（Unix epoch 毫秒）")
