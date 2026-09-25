"""
事件 Payload 定义：room.message.* 直播间行为流

定义 5 类直播间"行为流"事件 Payload（弹幕/礼物/SC/上舰/进房）。
对应存储 ``live_chat`` / ``gifts`` / ``super_chats`` / ``guards`` 表。

契约约定：
- 统一 ``RoomMessagePayload`` + ``message_type`` 判别（与存储 ``live_chat.message_type`` 一致）
- ``room.message.#`` 通配订阅时按 ``message_type`` 分发
- 礼物/SC/上舰放结构化字段（``gift``/``sc``/``guard``），不混进 ``content``
- 所有时间字段统一毫秒（``timestamp_ms``）
- 金额统一平台最小虚拟货币单位（B 站 = 金瓜子，1000 金瓜子 = 1 元），
  取值口径 = 标价（实付另记 ``paid_price``）；币种用 ``currency``
  带平台前缀标识（``bilibili_gold_coin`` / ``bilibili_silver_coin``）
- ``platform`` 是身份键组成部分（与 ``user_id`` 组成 ``(platform, user_id)``
  复合键），由采集器作为装配期常量统一注入，发布方逐条手填的反例不允许

注意：
- ``room.state.*`` 是**预留层**，本模块不定义其事件（行为/状态分层）
"""

from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


class RoomMessageUser(BaseModel):
    """
    直播间消息发送者信息（嵌套子结构）

    Attributes:
        id: 用户唯一 ID（平台 user_id）
        name: 用户昵称（显示名）
    """

    id: str = Field(..., description="用户唯一 ID（平台 user_id）")
    name: str = Field(..., description="用户昵称（显示名）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"id": "12345", "name": "观众A"},
        }
    )


class GiftInfo(BaseModel):
    """
    礼物信息（嵌套子结构）

    金额字段单位 = 平台最小虚拟货币单位（B 站金瓜子）；``total_price`` 取
    标价口径（观众认知中的礼物价值），打折/优惠场景的实付额记 ``paid_price``。
    连击消息以 ``combo_id`` 标识同一次连击（数量规则去重键）。

    Attributes:
        name: 礼物名称（如 "小星星"）
        count: 礼物数量（一次可送多个；存储层映射 ``gifts.quantity``）
        gift_id: 礼物 ID（平台礼物主键，比名称稳定）
        unit_price: 标价单价（金瓜子；免费礼物为银瓜子数）
        total_price: 标价总额 = unit_price × count
        paid_price: 实付总额（无优惠时与 total_price 相等）
        currency: 币种标识（bilibili_gold_coin / bilibili_silver_coin）
        combo_id: 连击标识（同一次连击共用，去重键）
        combo_count: 连击累计数
        combo_gift: 是否连击礼物
        blind_gift_id: 盲盒礼物 ID（非盲盒为 0）
        guard_level: 身份快照——送礼时的舰队等级
        fans_medal_level: 身份快照——粉丝勋章等级
        fans_medal_name: 身份快照——粉丝勋章名
        msg_id: 平台消息 ID
        raw_data: 完整原始 JSON（付费事件不可复刻，兜底供解析修复后重放补数）
    """

    name: str = Field(..., description="礼物名称")
    count: int = Field(default=1, ge=1, description="礼物数量")
    gift_id: int = Field(default=0, description="礼物 ID（平台礼物主键）")
    unit_price: int = Field(default=0, ge=0, description="标价单价（平台最小虚拟货币单位）")
    total_price: int = Field(default=0, ge=0, description="标价总额（unit_price × count）")
    paid_price: int = Field(default=0, ge=0, description="实付总额（无优惠时等于 total_price）")
    currency: str = Field(default="", description="币种标识（如 bilibili_gold_coin / bilibili_silver_coin）")
    combo_id: str = Field(default="", description="连击标识（同一次连击共用）")
    combo_count: int = Field(default=0, ge=0, description="连击累计数")
    combo_gift: bool = Field(default=False, description="是否连击礼物")
    blind_gift_id: int = Field(default=0, description="盲盒礼物 ID（非盲盒为 0）")
    guard_level: int = Field(default=0, ge=0, description="身份快照：送礼时舰队等级")
    fans_medal_level: int = Field(default=0, ge=0, description="身份快照：粉丝勋章等级")
    fans_medal_name: str = Field(default="", description="身份快照：粉丝勋章名")
    msg_id: str = Field(default="", description="平台消息 ID")
    raw_data: str = Field(
        default="", description="完整原始 JSON（存储兜底：付费事件不可复刻，解析修复后重放补数；事件消费方忽略）"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "小星星",
                "count": 1,
                "gift_id": 31036,
                "unit_price": 1000,
                "total_price": 1000,
                "paid_price": 1000,
                "currency": "bilibili_gold_coin",
            },
        }
    )


class SuperChatInfo(BaseModel):
    """
    SuperChat 信息（嵌套子结构）

    Attributes:
        total_price: SC 金额（平台最小虚拟货币单位；B 站 ``rmb`` 元 ×1000 = 金瓜子）
        currency: 币种标识（bilibili_gold_coin）
        start_time: 置顶开始时间（Unix 秒，平台原值）
        end_time: 置顶结束时间（Unix 秒，平台原值）
        guard_level: 身份快照——SC 时的舰队等级
        fans_medal_level: 身份快照——粉丝勋章等级
        fans_medal_name: 身份快照——粉丝勋章名
        message_id: 平台消息 ID
        raw_data: 完整原始 JSON（存储兜底字段：付费事件不可复刻，解析修复后重放补数；经 payload 从采集器运抵落库，事件消费方——Planner/展示——忽略)
    """

    total_price: int = Field(default=0, ge=0, description="SC 金额（平台最小虚拟货币单位，B 站金瓜子）")
    currency: str = Field(default="bilibili_gold_coin", description="币种标识")
    start_time: int = Field(default=0, ge=0, description="置顶开始时间（Unix 秒，平台原值）")
    end_time: int = Field(default=0, ge=0, description="置顶结束时间（Unix 秒，平台原值）")
    guard_level: int = Field(default=0, ge=0, description="身份快照：SC 时舰队等级")
    fans_medal_level: int = Field(default=0, ge=0, description="身份快照：粉丝勋章等级")
    fans_medal_name: str = Field(default="", description="身份快照：粉丝勋章名")
    message_id: str = Field(default="", description="平台消息 ID")
    raw_data: str = Field(
        default="", description="完整原始 JSON（存储兜底：付费事件不可复刻，解析修复后重放补数；事件消费方忽略）"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"total_price": 50000, "currency": "bilibili_gold_coin"},
        }
    )


class GuardInfo(BaseModel):
    """
    上舰（大航海）信息（嵌套子结构）

    ``total_price`` = 本次开通总金瓜子（B 站 ``GUARD.price`` 官方语义即本次
    开通总额，舰长 138 元 = 138000 金瓜子）；周期存原值（``guard_num`` /
    ``guard_unit``），到期判断留给应用层。

    Attributes:
        guard_level: 舰队等级（1 总督 / 2 提督 / 3 舰长，0 非舰队）
        guard_num: 周期数量（平台原值）
        guard_unit: 周期单位（平台原值，"月" / "*3天" 等怪值原样保留）
        total_price: 本次开通总金瓜子
        currency: 币种标识（bilibili_gold_coin）
        fans_medal_level: 身份快照——上舰时粉丝勋章等级
        fans_medal_name: 身份快照——粉丝勋章名
        msg_id: 平台消息 ID
        raw_data: 完整原始 JSON（存储兜底字段：付费事件不可复刻，解析修复后重放补数；经 payload 从采集器运抵落库，事件消费方——Planner/展示——忽略)
    """

    guard_level: int = Field(default=0, ge=0, description="舰队等级（1 总督 / 2 提督 / 3 舰长）")
    guard_num: int = Field(default=0, ge=0, description="周期数量（平台原值）")
    guard_unit: str = Field(default="", description="周期单位（平台原值）")
    total_price: int = Field(default=0, ge=0, description="本次开通总金瓜子")
    currency: str = Field(default="bilibili_gold_coin", description="币种标识")
    fans_medal_level: int = Field(default=0, ge=0, description="身份快照：粉丝勋章等级")
    fans_medal_name: str = Field(default="", description="身份快照：粉丝勋章名")
    msg_id: str = Field(default="", description="平台消息 ID")
    raw_data: str = Field(
        default="", description="完整原始 JSON（存储兜底：付费事件不可复刻，解析修复后重放补数；事件消费方忽略）"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "guard_level": 3,
                "guard_num": 1,
                "guard_unit": "月",
                "total_price": 138000,
                "currency": "bilibili_gold_coin",
            },
        }
    )


@register_event(CoreEvents.ROOM_MESSAGE_DANMAKU)
@register_event(CoreEvents.ROOM_MESSAGE_GIFT)
@register_event(CoreEvents.ROOM_MESSAGE_SUPER_CHAT)
@register_event(CoreEvents.ROOM_MESSAGE_GUARD)
@register_event(CoreEvents.ROOM_MESSAGE_ENTER)
@register_event(CoreEvents.ROOM_MESSAGE_PARTNER_SPEECH)
class RoomMessagePayload(BasePayload):
    """
    直播间行为流事件 Payload（统一形状 + message_type 判别）

    事件名：
    - ``room.message.danmaku`` — 弹幕（应填充 ``content``）
    - ``room.message.gift`` — 礼物（应填充 ``gift``）
    - ``room.message.super_chat`` — SC（应填充 ``content`` + ``sc``）
    - ``room.message.guard`` — 上舰（舰长/提督/总督，付费消息；``content`` 填
      人读描述供下游识别做优先回应，``guard`` 填结构化明细）
    - ``room.message.enter`` — 进房（无内容）
    - ``room.message.partner_speech`` — 联动对象发言（房间里第三个说话者：
      非弹幕、非主播；落 live_chat 时 ``sender_role="partner"``，不计观众统计）

    发布者：直播接入层（弹幕/礼物/SC/进房接收 → 结构化）
    订阅者：Planner（订 ``room.message.danmaku`` 高价值醒来）、后台记账器（订 ``room.*`` 写 live_sessions 状态）

    Attributes:
        live_session_id: 场次主键（live_sessions.id）。发布方（采集器/模拟器）不填，
            由事件总线的场次盖章拦截器统一注入当前进行中场次；0 表示未归属。
        message_id: 消息唯一 ID（平台消息 ID 或发布方生成）。落库 live_chat.message_id，
            与主播发言行的 reply_to_message_id 构成"回复了哪条弹幕"的关联键。
        message_type: 消息类型（Literal 与存储 live_chat.message_type 一致；通配订阅 ``room.message.#`` 时按此分发）
        platform: 平台标识（身份键组成部分，与 user.id 组成 (platform, user_id)
            复合键；平台名 bilibili/douyin 等 + 调试保留字 console——
            console 是无 simulated 标记的调试输入，靠 platform 隔离身份；
            模拟器数据归 bilibili（platform=bilibili + simulated=1），
            由 simulated 区分真假）。
            采集器作为装配期常量统一注入；空串表示未归属平台。
        user: 发送者信息
        content: 文本内容（弹幕/SC 文本；其他类型为空）
        gift: 礼物信息（仅 ``gift`` 类型有值）
        sc: SC 信息（仅 ``super_chat`` 类型有值）
        guard: 上舰信息（仅 ``guard`` 类型有值）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    # 判别字段：EventBus 在 emit 期校验"事件名末段 == 该字段值"，
    # 六重注册共享一类，挂错事件名（如 gift 弹幕发成 danmaku）直接报错
    _DISCRIMINANT_FIELD: ClassVar[str] = "message_type"

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    message_id: str = Field(
        default="",
        description="消息唯一 ID（平台消息 ID 或发布方生成）；与主播发言 reply_to_message_id 构成回复关联键",
    )
    message_type: Literal["danmaku", "gift", "super_chat", "guard", "enter", "partner_speech"] = Field(
        ...,
        description="消息类型。通配订阅 room.message.# 时按此字段分发（与存储 live_chat.message_type 枚举一致）",
    )
    platform: str = Field(
        default="",
        description="平台标识（身份键组成部分；bilibili/console 等，采集器装配期统一注入）",
    )
    user: RoomMessageUser = Field(..., description="发送者信息")
    content: str = Field(default="", description="文本内容（弹幕/SC 文本；其他类型为空字符串）")
    gift: Optional[GiftInfo] = Field(default=None, description="礼物信息（仅 gift 类型）")
    sc: Optional[SuperChatInfo] = Field(default=None, description="SuperChat 信息（仅 super_chat 类型）")
    guard: Optional[GuardInfo] = Field(default=None, description="上舰信息（仅 guard 类型）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒）",
    )
    simulated: bool = Field(
        default=False,
        description="数据溯源标记：True=模拟/回放源（SimulatorService 生成或回放），统计与入库需过滤",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": 1,
                "message_id": "9f2c8a1b",
                "message_type": "danmaku",
                "platform": "bilibili",
                "user": {"id": "12345", "name": "观众A"},
                "content": "主播好可爱！",
                "gift": None,
                "sc": None,
                "guard": None,
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = [
    "RoomMessageUser",
    "GiftInfo",
    "SuperChatInfo",
    "GuardInfo",
    "RoomMessagePayload",
]
