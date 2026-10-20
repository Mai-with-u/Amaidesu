"""LAPLACE 事件模型与 ``room.message.*`` 载荷映射

LAPLACE Chat（https://laplace.live/chat）是 B 站直播聊天面板；
LAPLACE Event Bridge（https://laplace.live/chat/event-bridge）把它的
实时事件广播给本地客户端。本模块消费 Event Bridge 转发的事件。

事件字段是 camelCase；本模块只声明消费用到的子集，``extra="ignore"``
丢弃 bridge 注入的 ``source`` 与其余未消费字段。

映射约定（与 ``RoomMessagePayload`` 契约一致）：

- ``platform`` 固定 ``bilibili``（Laplace 是传输层，不是平台）
- ``timestamp_ms`` 取 ``timestampNormalized``（毫秒），缺失才回退当前时刻
- 付费事件保留 ``raw_data``；SC 的举报 ``token`` 在落库前脱敏为 ``***``
- SC 金额优先用 ``priceNormalized``（人民币元）×1000 转金瓜子；只有该字段
  缺失或非正数时才回退 ``price``。``SuperChatInfo.total_price`` 的单位是金瓜子
- 礼物 ``price`` 是含舰队折扣的实付总价，源不提供标价，因此
  ``total_price`` 与 ``paid_price`` 同值、贡献按实付价统计；
  ``unit_price`` 为反推近似值
- 盲盒礼物 ``blind_gift_id`` 取 ``blindGift.blind_gift_config_id``；
  ``original_gift_id`` 是开出的实际礼物，不写入该字段
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.modules.events.payloads.room import (
    GiftInfo,
    GuardInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.guard_levels import DEFAULT_GUARD_NAME, GUARD_LEVEL_NAMES

_PLATFORM = "bilibili"
_CURRENCY_GOLD = "bilibili_gold_coin"
_CURRENCY_SILVER = "bilibili_silver_coin"
_RMB_TO_GOLD_COIN = 1000


class _FansMedal(BaseModel):
    """Laplace 粉丝勋章快照（只取等级与名称）。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    level: int = 0
    name: str = ""


class _LaplaceEventBase(BaseModel):
    """五类事件共享字段（只声明消费用到的子集）。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str = ""
    id: Optional[str] = ""
    origin: int = 0
    uid: int = 0
    username: Optional[str] = ""
    timestamp: float = 0.0
    timestamp_normalized: float = Field(default=0.0, alias="timestampNormalized")
    medal: Optional[_FansMedal] = None


class _MessageEvent(_LaplaceEventBase):
    """普通弹幕（Laplace ``message``）。"""

    message: Optional[str] = ""
    id_str: Optional[str] = Field(default="", alias="idStr")


class _GiftEvent(_LaplaceEventBase):
    """礼物（Laplace ``gift``）。"""

    message: Optional[str] = ""
    price: float = 0.0
    price_normalized: float = Field(default=0.0, alias="priceNormalized")
    coin_type: Optional[str] = Field(default="", alias="coinType")
    guard_type: int = Field(default=0, alias="guardType")
    gift_amount: int = Field(default=1, alias="giftAmount")
    gift_id: int = Field(default=0, alias="giftId")
    gift_name: Optional[str] = Field(default="", alias="giftName")
    blind_gift: Optional[Dict[str, Any]] = Field(default=None, alias="blindGift")


class _SuperChatEvent(_LaplaceEventBase):
    """醒目留言（Laplace ``superchat``）。"""

    message: Optional[str] = ""
    price: float = 0.0
    price_normalized: float = Field(default=0.0, alias="priceNormalized")
    duration: float = 0.0
    guard_type: int = Field(default=0, alias="guardType")
    sc_id: int = Field(default=0, alias="scId")
    deleted: bool = False


class _ToastEvent(_LaplaceEventBase):
    """大航海开通/续费（Laplace ``toast``）。"""

    stable_key: Optional[str] = Field(default="", alias="stableKey")
    message: Optional[str] = ""
    price: float = 0.0
    price_normalized: float = Field(default=0.0, alias="priceNormalized")
    toast_type: int = Field(default=0, alias="toastType")
    toast_amount: int = Field(default=1, alias="toastAmount")
    toast_amount_unit: Optional[str] = Field(default="", alias="toastAmountUnit")
    toast_name: Optional[str] = Field(default="", alias="toastName")


class _InteractionEvent(_LaplaceEventBase):
    """进场/关注/分享（Laplace ``interaction``）。"""

    action: int = 0
    guard_type: int = Field(default=0, alias="guardType")


_MODEL_BY_TYPE: Dict[str, type[_LaplaceEventBase]] = {
    "message": _MessageEvent,
    "gift": _GiftEvent,
    "superchat": _SuperChatEvent,
    "toast": _ToastEvent,
    "interaction": _InteractionEvent,
}


def _as_int(value: Any, default: int = 0) -> int:
    """把 JSON 数值归一化为 int；非法/小数返回默认值。"""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, float) and not value.is_integer():
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """把 JSON 数值归一化为 float；非法返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sc_total_gold(price: Any, price_normalized: Any) -> int:
    """SC 金额统一为金瓜子。

    ``priceNormalized`` 是 LAPLACE 定义的“实际货币单位价格”（人民币元），
    优先用它换算：1 元 = 1000 金瓜子。金额大小与单位无关，不能用
    ``price < 1000`` 之类的大小判断猜单位；只有该字段缺失或非正数时，
    才回退到源 ``price``（通常已是金瓜子）。
    """
    normalized = _as_float(price_normalized)
    if normalized > 0:
        return int(round(normalized * _RMB_TO_GOLD_COIN))
    return max(_as_int(price), 0)


class LaplaceEventMapper:
    """把 Laplace 事件 dict 映射为 ``room.message.*`` 载荷。

    Args:
        room_id: 只接收该 ``origin`` 的事件；0 = 不过滤。
        handle_enter: 是否处理进房事件。
        handle_gift: 是否处理礼物事件。
        handle_superchat: 是否处理 SC 事件。
        handle_guard: 是否处理上舰/续费事件。
        dedupe_cache_size: 去重缓存上限；0 = 关闭去重。
    """

    def __init__(
        self,
        *,
        room_id: int = 0,
        handle_enter: bool = True,
        handle_gift: bool = True,
        handle_superchat: bool = True,
        handle_guard: bool = True,
        dedupe_cache_size: int = 2000,
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._room_id = room_id
        self._handle_enter = handle_enter
        self._handle_gift = handle_gift
        self._handle_superchat = handle_superchat
        self._handle_guard = handle_guard
        self._dedupe_cache_size = dedupe_cache_size
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._uid_missing_warned_types: set[str] = set()

    def to_payload(self, raw: Dict[str, Any]) -> Optional[RoomMessagePayload]:
        """解析并映射单条事件；不支持/非法/重复事件返回 None。"""
        event_type = str(raw.get("type") or "")
        model_cls = _MODEL_BY_TYPE.get(event_type)
        if model_cls is None:
            self.logger.debug(f"未支持的 Laplace 事件类型（已忽略）: {event_type or '<empty>'}")
            return None

        try:
            event = model_cls.model_validate(raw)
        except ValidationError as exc:
            self.logger.warning(
                f"Laplace 事件解析失败（已丢弃）: type={event_type}, id={raw.get('id')!r}, "
                f"errors={exc.errors(include_url=False)[:3]}"
            )
            return None

        try:
            return self._to_payload(event, raw)
        except Exception as exc:  # noqa: BLE001 - 单事件隔离，不能让坏事件终止采集
            self.logger.warning(
                f"Laplace 事件映射失败（已丢弃）: type={event_type}, id={event.id!r}, "
                f"uid={event.uid!r}, error={type(exc).__name__}: {exc}",
                exc=exc,
            )
            return None

    def _to_payload(self, event: _LaplaceEventBase, raw: Dict[str, Any]) -> Optional[RoomMessagePayload]:
        if self._room_id and event.origin != self._room_id:
            self.logger.debug(f"Laplace 事件房间不匹配（已忽略）: origin={event.origin}, 期望={self._room_id}")
            return None
        if not event.uid:
            detail = f"type={event.type}, id={event.id!r}, username={event.username!r}"
            if event.type not in self._uid_missing_warned_types:
                self._uid_missing_warned_types.add(event.type)
                self.logger.warning(
                    f"Laplace 事件缺少 uid（已丢弃）: {detail}；后续同类事件降为 debug。"
                    "若使用匿名直连，请检查 LAPLACE「进阶」登录同步；"
                    "开放平台来源的 uid 恒 0，当前不可用"
                )
            else:
                self.logger.debug(f"Laplace 事件缺少 uid（已丢弃）: {detail}")
            return None
        if not self._should_handle(event):
            return None
        if self._is_duplicate(event, raw):
            self.logger.debug(f"Laplace 重复事件（已忽略）: type={event.type}, key={self._dedupe_key(event, raw)}")
            return None

        if isinstance(event, _MessageEvent):
            return self._message_payload(event)
        if isinstance(event, _GiftEvent):
            return self._gift_payload(event, raw)
        if isinstance(event, _SuperChatEvent):
            return self._super_chat_payload(event, raw)
        if isinstance(event, _ToastEvent):
            return self._guard_payload(event, raw)
        if isinstance(event, _InteractionEvent):
            return self._enter_payload(event)
        return None

    def _should_handle(self, event: _LaplaceEventBase) -> bool:
        if isinstance(event, _GiftEvent):
            return self._handle_gift
        if isinstance(event, _SuperChatEvent):
            return self._handle_superchat and not event.deleted
        if isinstance(event, _ToastEvent):
            if event.toast_type not in (1, 2, 3):
                self.logger.debug(f"非大航海 toast（已忽略）: toastType={event.toast_type}")
                return False
            return self._handle_guard
        if isinstance(event, _InteractionEvent):
            return self._handle_enter and event.action == 1
        return True

    def _message_payload(self, event: _MessageEvent) -> RoomMessagePayload:
        return RoomMessagePayload(
            message_type="danmaku",
            platform=_PLATFORM,
            user=self._user(event),
            content=event.message or "",
            message_id=event.id_str or event.id or "",
            timestamp_ms=self._timestamp_ms(event),
        )

    def _gift_payload(self, event: _GiftEvent, raw: Dict[str, Any]) -> RoomMessagePayload:
        price = max(_as_int(event.price), 0)
        count = max(_as_int(event.gift_amount), 1)
        medal_level, medal_name = self._medal_fields(event)
        return RoomMessagePayload(
            message_type="gift",
            platform=_PLATFORM,
            user=self._user(event),
            content="",
            message_id=event.id or "",
            gift=GiftInfo(
                name=event.gift_name or "礼物",
                count=count,
                gift_id=_as_int(event.gift_id),
                # 源只提供折扣后总价；unit_price 是反推近似值，不保证 unit × count == total
                unit_price=round(price / count),
                total_price=price,
                paid_price=price,
                currency=self._gift_currency(event, price),
                guard_level=_as_int(event.guard_type),
                fans_medal_level=medal_level,
                fans_medal_name=medal_name,
                blind_gift_id=self._blind_gift_id(event.blind_gift),
                msg_id=event.id or "",
                raw_data=self._raw_json(raw),
            ),
            timestamp_ms=self._timestamp_ms(event),
        )

    def _super_chat_payload(self, event: _SuperChatEvent, raw: Dict[str, Any]) -> RoomMessagePayload:
        timestamp_ms = self._timestamp_ms(event)
        start_time = timestamp_ms // 1000
        duration = max(_as_int(event.duration), 0)
        medal_level, medal_name = self._medal_fields(event)
        sc_id = _as_int(event.sc_id)
        message_id = str(sc_id) if sc_id else (event.id or "")
        return RoomMessagePayload(
            message_type="super_chat",
            platform=_PLATFORM,
            user=self._user(event),
            content=event.message or "",
            message_id=message_id,
            sc=SuperChatInfo(
                total_price=_sc_total_gold(event.price, event.price_normalized),
                currency=_CURRENCY_GOLD,
                start_time=start_time,
                end_time=start_time + duration,
                guard_level=_as_int(event.guard_type),
                fans_medal_level=medal_level,
                fans_medal_name=medal_name,
                message_id=message_id,
                raw_data=self._raw_json(raw, redact_token=True),
            ),
            timestamp_ms=timestamp_ms,
        )

    def _guard_payload(self, event: _ToastEvent, raw: Dict[str, Any]) -> RoomMessagePayload:
        guard_level = _as_int(event.toast_type)
        msg_id = event.stable_key or event.id or ""
        return RoomMessagePayload(
            message_type="guard",
            platform=_PLATFORM,
            user=self._user(event),
            content=self._guard_content(event),
            message_id=msg_id,
            guard=GuardInfo(
                guard_level=guard_level,
                guard_num=max(_as_int(event.toast_amount), 0),
                guard_unit=event.toast_amount_unit or "",
                total_price=max(_as_int(event.price), 0),
                currency=_CURRENCY_GOLD,
                # toast 事件没有粉丝牌字段，源限制下保持默认；raw_data 保留原始事件
                fans_medal_level=0,
                fans_medal_name="",
                msg_id=msg_id,
                raw_data=self._raw_json(raw),
            ),
            timestamp_ms=self._timestamp_ms(event),
        )

    def _enter_payload(self, event: _InteractionEvent) -> RoomMessagePayload:
        return RoomMessagePayload(
            message_type="enter",
            platform=_PLATFORM,
            user=self._user(event),
            content="",
            message_id=event.id or "",
            timestamp_ms=self._timestamp_ms(event),
        )

    def _guard_content(self, event: _ToastEvent) -> str:
        message = (event.message or "").strip()
        if message and "<%" not in message:
            return message
        guard_name = event.toast_name or GUARD_LEVEL_NAMES.get(event.toast_type, DEFAULT_GUARD_NAME)
        return f"{event.username or '未知用户'} 开通了{guard_name}"

    def _gift_currency(self, event: _GiftEvent, price: int) -> str:
        coin = (event.coin_type or "").strip().lower()
        if coin == "gold":
            return _CURRENCY_GOLD
        if coin == "silver":
            return _CURRENCY_SILVER
        if not coin:
            return _CURRENCY_SILVER if price <= 0 else _CURRENCY_GOLD
        self.logger.warning(f"未知 gift.coinType={event.coin_type!r}，币种置空（不计入付费统计）")
        return ""

    @staticmethod
    def _blind_gift_id(blind_gift: Optional[Dict[str, Any]]) -> int:
        """取盲盒配置 ID；非盲盒返回 0。

        LAPLACE 的 ``blindGift`` 对应 B 站 ``SEND_GIFT_V2.BlindGift``：
        ``blind_gift_config_id`` 是盲盒配置 ID，``original_gift_id`` 是开出的
        实际礼物 ID。``GiftInfo.blind_gift_id`` 语义是前者，不能用后者顶替。
        旧键名仅作上游字段变动时的兜底。
        """
        if not isinstance(blind_gift, dict):
            return 0
        for key in ("blind_gift_config_id", "blind_gift_id", "blindGiftId", "gift_id"):
            value = blind_gift.get(key)
            if value:
                return _as_int(value)
        return 0

    @staticmethod
    def _medal_fields(event: _LaplaceEventBase) -> Tuple[int, str]:
        medal = event.medal
        if medal is None:
            return 0, ""
        return max(_as_int(medal.level), 0), medal.name or ""

    @staticmethod
    def _user(event: _LaplaceEventBase) -> RoomMessageUser:
        return RoomMessageUser(id=str(event.uid), name=event.username or "未知用户")

    @staticmethod
    def _timestamp_ms(event: _LaplaceEventBase) -> int:
        value = _as_int(event.timestamp_normalized)
        if value > 10**14:  # 纳秒兜底（正常字段已是毫秒）
            value //= 1_000_000
        return value if value > 0 else now_ms()

    def _gift_dedupe_key(self, event: _GiftEvent, raw: Dict[str, Any]) -> str:
        """礼物的内容指纹。

        LAPLACE 的 ``Gift.id`` 由“时间戳:房间:UID”构成，同一用户同一时间戳
        的多条礼物会共用 id，不能单独作为去重键。这里把能区分事件的字段拼
        成指纹：只有内容完全相同的重播才会被丢弃；连击更新只要数量、价格或
        时间戳有差异就会保留。``combo_total_coin`` 若被上游保留在原始事件
        中，也纳入指纹。
        """
        combo_total = raw.get("combo_total_coin", raw.get("comboTotalCoin"))
        parts = (
            event.id or "",
            event.origin,
            event.uid,
            event.gift_id,
            event.gift_amount,
            event.price,
            event.coin_type or "",
            event.guard_type,
            event.timestamp_normalized,
            event.gift_name or "",
            event.message or "",
            self._blind_gift_id(event.blind_gift),
            combo_total if combo_total is not None else "",
        )
        return ":".join(str(part) for part in parts)

    def _dedupe_key(self, event: _LaplaceEventBase, raw: Dict[str, Any]) -> str:
        if isinstance(event, _MessageEvent):
            key = event.id_str or event.id
        elif isinstance(event, _SuperChatEvent):
            key = str(event.sc_id) if event.sc_id else (event.id or "")
        elif isinstance(event, _ToastEvent):
            key = event.stable_key or event.id
        elif isinstance(event, _GiftEvent):
            key = self._gift_dedupe_key(event, raw)
        else:
            key = event.id
        return f"{event.type}:{key}" if key else ""

    def _is_duplicate(self, event: _LaplaceEventBase, raw: Dict[str, Any]) -> bool:
        if self._dedupe_cache_size <= 0:
            return False
        key = self._dedupe_key(event, raw)
        if not key:
            return False
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        while len(self._seen) > self._dedupe_cache_size:
            self._seen.popitem(last=False)
        return False

    def _raw_json(self, raw: Dict[str, Any], *, redact_token: bool = False) -> str:
        data = dict(raw)
        if redact_token and "token" in data:
            data["token"] = "***"
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            self.logger.warning(f"Laplace 原始事件序列化失败（raw_data 置空）: {type(exc).__name__}: {exc}")
            return ""


__all__ = ["LaplaceEventMapper"]
