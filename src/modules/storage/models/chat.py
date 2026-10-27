"""直播明细域数据契约（live_chat / gifts / super_chats / guards 表的读模型）

设计要点
--------

- 四张明细表读取结果的**持久化契约**：被主播 Agent 行为侧（场次回顾、话题
  摘要、会话历史）、storage 仓储（逐行反序列化）、Dashboard 服务层（回看
  时间线、观众档案、复盘聚合）与模拟器回放引擎共同消费，依赖方向一律向下
  （agents / simulator → modules、dashboard → storage）。
- ``extra="forbid"``：与代码库其他 Pydantic 模型保持一致，严格拒绝未知字段。
- 四张明细表均带 ``simulated`` 贯穿列；付费三表金额单位 = 平台最小虚拟货币
  单位（B 站金瓜子），口径说明见 ``storage/schema.py``。
- 时间字段统一毫秒（Unix epoch 毫秒）；例外是 SC 置顶周期 ``start_time`` /
  ``end_time``，存平台原值（Unix 秒）。

聚合行（非表行，查询时 GROUP BY 派生）：

- ``SessionSpeaker`` —— 场内单个观众的发言聚合（发言最多的观众榜）。
- ``ViewerSessionParticipation`` —— 单个观众在一场里的发言聚合（参与场次榜）。
- ``SessionSpeechMoment`` —— 主播发言时刻（节奏/沉默计算的最小输入）。
- ``DailyDanmakuCount`` —— 单个本地日期的观众弹幕量（互动分析折线）。
- ``ChatDateCount`` —— 单个本地日期的可回放弹幕条数（回放日期选择器）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "LiveChatMessage",
    "GiftRecord",
    "SuperChatRecord",
    "GuardRecord",
    "SessionSpeaker",
    "ViewerSessionParticipation",
    "SessionSpeechMoment",
    "DailyDanmakuCount",
    "ChatDateCount",
]


class LiveChatMessage(BaseModel):
    """``live_chat`` 一行：消息流明细（观众弹幕 / 主播发言 / 联动对象发言）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键")
    live_session_id: int = Field(..., description="所属场次主键")
    timestamp_ms: int = Field(..., description="消息时刻（Unix epoch 毫秒）")
    platform: str = Field(default="", description="消息平台")
    sender_role: str = Field(..., description="说话人角色（viewer / assistant / partner）")
    sender_id: Optional[str] = Field(default=None, description="说话人外部 ID（观众行；主播行为主播名）")
    sender_name: Optional[str] = Field(default=None, description="说话人昵称")
    viewer_id: Optional[int] = Field(default=None, description="归到的内部观众 ID；未建档为 None")
    id_kind: str = Field(default="", description="sender_id 的 ID 种类（account / open / 空）")
    content: str = Field(..., description="消息正文")
    message_type: str = Field(..., description="消息类型（danmaku / enter / …）")
    message_id: Optional[str] = Field(default=None, description="观众行的消息 ID（主播回复关联键）")
    reply_to_message_id: Optional[str] = Field(default=None, description="主播行回复的那条弹幕的消息 ID")
    simulated: bool = Field(default=False, description="是否模拟数据（贯穿列）")


class GiftRecord(BaseModel):
    """``gifts`` 一行：礼物明细（金额 = 平台最小虚拟货币单位）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键")
    live_session_id: int = Field(..., description="所属场次主键")
    timestamp_ms: int = Field(..., description="礼物时刻（Unix epoch 毫秒）")
    platform: str = Field(default="", description="礼物平台")
    user_id: str = Field(..., description="送礼时的观众外部 ID（原始值）")
    user_name: str = Field(..., description="送礼时的观众昵称")
    viewer_id: Optional[int] = Field(default=None, description="归到的内部观众 ID；未建档为 None")
    id_kind: str = Field(default="", description="user_id 的 ID 种类（account / open / 空）")
    gift_id: int = Field(default=0, description="平台礼物 ID")
    gift_name: str = Field(..., description="礼物名")
    quantity: int = Field(..., description="礼物件数")
    unit_price: int = Field(default=0, description="单价（平台最小虚拟货币单位）")
    total_price: int = Field(default=0, description="标价合计（平台最小虚拟货币单位；实付另记 paid_price）")
    paid_price: int = Field(default=0, description="实付金额（平台最小虚拟货币单位）")
    currency: str = Field(default="", description="币种（带平台前缀；银瓜子为免费币种）")
    guard_level: int = Field(default=0, description="送礼时的舰队等级快照")
    fans_medal_level: int = Field(default=0, description="送礼时的粉丝牌等级快照")
    fans_medal_name: str = Field(default="", description="送礼时的粉丝牌名快照")
    combo_id: str = Field(default="", description="连击组 ID")
    combo_count: int = Field(default=0, description="连击次数")
    combo_gift: bool = Field(default=False, description="是否连击礼物")
    blind_gift_id: int = Field(default=0, description="盲盒礼物 ID")
    msg_id: str = Field(default="", description="平台消息 ID")
    raw_data: Optional[str] = Field(default=None, description="完整原始 JSON（兜底，可修复解析后重放补数）")
    simulated: bool = Field(default=False, description="是否模拟数据（贯穿列）")


class SuperChatRecord(BaseModel):
    """``super_chats`` 一行：醒目留言明细（金额 = 平台最小虚拟货币单位）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键")
    live_session_id: int = Field(..., description="所属场次主键")
    timestamp_ms: int = Field(..., description="SC 时刻（Unix epoch 毫秒）")
    platform: str = Field(default="", description="SC 平台")
    user_id: str = Field(..., description="发送时的观众外部 ID（原始值）")
    user_name: str = Field(..., description="发送时的观众昵称")
    viewer_id: Optional[int] = Field(default=None, description="归到的内部观众 ID；未建档为 None")
    id_kind: str = Field(default="", description="user_id 的 ID 种类（account / open / 空）")
    message: str = Field(..., description="SC 正文")
    total_price: int = Field(default=0, description="金额（平台最小虚拟货币单位）")
    currency: str = Field(default="", description="币种（带平台前缀）")
    start_time: int = Field(default=0, description="置顶周期开始（平台原值，Unix 秒）")
    end_time: int = Field(default=0, description="置顶周期结束（平台原值，Unix 秒）")
    guard_level: int = Field(default=0, description="发送时的舰队等级快照")
    fans_medal_level: int = Field(default=0, description="发送时的粉丝牌等级快照")
    fans_medal_name: str = Field(default="", description="发送时的粉丝牌名快照")
    message_id: str = Field(default="", description="平台消息 ID")
    raw_data: Optional[str] = Field(default=None, description="完整原始 JSON（兜底）")
    simulated: bool = Field(default=False, description="是否模拟数据（贯穿列）")


class GuardRecord(BaseModel):
    """``guards`` 一行：大航海开通/续费购买事件（每次上舰/续费一条）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键")
    live_session_id: int = Field(..., description="所属场次主键")
    timestamp_ms: int = Field(..., description="购买时刻（Unix epoch 毫秒）")
    platform: str = Field(default="", description="平台")
    user_id: str = Field(..., description="购买时的观众外部 ID（原始值）")
    user_name: str = Field(..., description="购买时的观众昵称")
    viewer_id: Optional[int] = Field(default=None, description="归到的内部观众 ID；未建档为 None")
    id_kind: str = Field(default="", description="user_id 的 ID 种类（account / open / 空）")
    guard_level: int = Field(default=0, description="舰队等级")
    guard_num: int = Field(default=0, description="购买周期数（平台原值）")
    guard_unit: str = Field(default="", description="购买周期单位（平台原值，如月）")
    total_price: int = Field(default=0, description="金额（平台最小虚拟货币单位）")
    currency: str = Field(default="", description="币种（带平台前缀）")
    fans_medal_level: int = Field(default=0, description="购买时的粉丝牌等级快照")
    fans_medal_name: str = Field(default="", description="购买时的粉丝牌名快照")
    msg_id: str = Field(default="", description="平台消息 ID")
    raw_data: Optional[str] = Field(default=None, description="完整原始 JSON（兜底）")
    simulated: bool = Field(default=False, description="是否模拟数据（贯穿列）")


# ---------------------------------------------------------------------------
# 聚合行（GROUP BY 派生，非表行）
# ---------------------------------------------------------------------------


class SessionSpeaker(BaseModel):
    """场内单个观众的发言聚合（发言最多的观众榜，画像更新的候选来源）。"""

    model_config = ConfigDict(extra="forbid")

    viewer_id: int = Field(..., description="内部观众 ID（已归到观众的行才参与统计）")
    platform: str = Field(..., description="观众平台（场内 MAX 聚合）")
    user_name: Optional[str] = Field(default=None, description="观众本场留下的昵称之一（聚合近似，权威在 viewers）")
    message_count: int = Field(..., description="本场弹幕条数")
    last_ms: int = Field(..., description="本场最后发言时刻（Unix epoch 毫秒）")


class ViewerSessionParticipation(BaseModel):
    """单个观众在一场里的发言聚合（参与过的场次，最近参与在前）。"""

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键")
    title: Optional[str] = Field(default=None, description="场次标题（场次行已删时为 None）")
    message_count: int = Field(..., description="该场发言条数")
    first_ms: int = Field(..., description="该场首次发言时刻（Unix epoch 毫秒）")
    last_ms: int = Field(..., description="该场最后发言时刻（Unix epoch 毫秒）")


class SessionSpeechMoment(BaseModel):
    """主播发言时刻（节奏/沉默计算的最小输入）。"""

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键")
    timestamp_ms: int = Field(..., description="发言时刻（Unix epoch 毫秒）")


class DailyDanmakuCount(BaseModel):
    """单个本地日期的观众弹幕量（互动分析折线数据点）。"""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(..., description="本地日期（YYYY-MM-DD）")
    count: int = Field(..., description="当日弹幕条数")


class ChatDateCount(BaseModel):
    """单个本地日期的可回放弹幕条数（回放日期选择器条目）。"""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(..., description="本地日期（YYYY-MM-DD）")
    danmaku_count: int = Field(..., description="当日弹幕条数")
