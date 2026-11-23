"""Minecraft 游戏公屏门控（纯逻辑，构造注入单调毫秒时钟，零真实 sleep）。

将高噪声的游戏公屏分级节流，既不漏掉身边同伴、点名与私聊，又避免全服刷屏把游戏 Agent
唤醒空转或塞满上下文：

- **事实标签格式化**：按客观信号贴上 ``[游戏公屏·私聊]``、``[游戏公屏·点名你]``、``[游戏公屏·身边{d}格]``
  （无距离数据时按 ``nearby_players`` 降级为 ``[游戏公屏·身边]``）或 ``[游戏公屏·远处]``，原样保留采集器
  附带的未核验 ``note`` 说明；
- **2 秒短合并窗与优先级截留**：单窗默认上限 8 条，按「私聊 > 点名 ``type="self"`` > 距离 ≤24 格
  或近 2 分钟互动同伴 > 其余抽样」排序截留；同人同内容与单人高频连发自动折叠计数；
- **持续刷屏唤醒最小冷却**：连续频繁唤醒或窗内出现高频折叠/超限时启用最小冷却（默认 6000ms），
  把持续刷屏期间的新消息继续并入同一待发窗；
- **分级唤醒判定**：私聊 / 点名 / 身边 / 近期互动同伴到窗产出 ``should_wake=True`` 的 ``[游戏公屏合批]``；
  独立到达的远处非点名游戏公屏进入有界旁听滑窗（``should_wake=False``），仅在行动步调用
  ``drain_eavesdrop()`` 时顺带取出，绝不主动唤醒；
- **近期人类原话滚动窗**：同步维护最近 8 条游戏公屏原话与最近 4 条主播建议，供集中整理与重开上下文复用。
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import AbstractSet, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.minecraft.chat_attribution import format_chat_sender
from src.modules.events.payloads.game_chat import GameChatPayload

__all__ = [
    "DEFAULT_BATCH_WINDOW_MS",
    "DEFAULT_MAX_BATCH_ITEMS",
    "DEFAULT_MAX_EAVESDROP_ITEMS",
    "DEFAULT_MAX_RECENT_CHATS",
    "DEFAULT_MAX_RECENT_SUGGESTIONS",
    "DEFAULT_NEARBY_DISTANCE",
    "DEFAULT_PARTNER_WINDOW_MS",
    "DEFAULT_RECENT_CHAT_TTL_MS",
    "DEFAULT_WAKE_COOLDOWN_MS",
    "PRIORITY_EAVESDROP",
    "PRIORITY_NEARBY_OR_PARTNER",
    "PRIORITY_PRIVATE",
    "PRIORITY_SELF_MENTION",
    "ChatBatch",
    "ChatIngestResult",
    "MinecraftChatGate",
]

DEFAULT_BATCH_WINDOW_MS = 2_000
DEFAULT_MAX_BATCH_ITEMS = 8
DEFAULT_NEARBY_DISTANCE = 24.0
DEFAULT_PARTNER_WINDOW_MS = 120_000
DEFAULT_WAKE_COOLDOWN_MS = 6_000
DEFAULT_MAX_EAVESDROP_ITEMS = 16
DEFAULT_MAX_RECENT_CHATS = 8
DEFAULT_MAX_RECENT_SUGGESTIONS = 4
# 近期人类原话滚动窗中游戏公屏对话的默认存活时长（10 分钟）：超过此时间的陈旧聊天自动过期淘汰
DEFAULT_RECENT_CHAT_TTL_MS = 600_000
# 单窗内同一发言人最多保留几条不同内容，超出部分折叠为计数以防单人刷屏占满窗口
_MAX_DISTINCT_PER_SENDER = 3
# 持续刷屏判定窗口与连续唤醒门槛：30 秒内已连续唤醒满 3 次即开启最小唤醒冷却
_SPAM_TRACK_WINDOW_MS = 30_000
_SPAM_BURST_THRESHOLD = 3
_SELF_MENTION_TYPE = "self"

PRIORITY_PRIVATE = 0
PRIORITY_SELF_MENTION = 1
PRIORITY_NEARBY_OR_PARTNER = 2
PRIORITY_EAVESDROP = 3


def _monotonic_ms() -> int:
    """缺省单调毫秒时钟。"""
    return int(time.monotonic() * 1000)


def _format_relative_age(delta_ms: int) -> str:
    """把毫秒时间差渲染为紧凑中文相对时长（如“刚刚”“3分钟前”“1小时前”）。"""
    safe_ms = max(0, int(delta_ms))
    if safe_ms < 60_000:
        return "刚刚"
    total_minutes = safe_ms // 60_000
    if total_minutes < 60:
        return f"{total_minutes}分钟前"
    hours, rem_minutes = divmod(total_minutes, 60)
    if rem_minutes > 0:
        return f"{hours}小时{rem_minutes}分钟前"
    return f"{hours}小时前"


@dataclass
class _RecentChatEntry:
    """近期游戏公屏滚动窗中的单条双向对话记录（玩家游戏公屏或己方回复，附带注入时钟时间戳）。"""

    formatted_line: str
    sender: str
    raw_content: str
    timestamp_ms: int
    is_self: bool = False

    def render_with_age(self, now_ms: int) -> str:
        """附带相对时间前缀渲染双向游戏公屏行，帮助模型分辨刚刚发生与较早发生的对话。"""
        age = _format_relative_age(now_ms - self.timestamp_ms)
        if self.is_self:
            return f"[你回过·{age}] {self.raw_content}"
        if self.formatted_line.startswith("[") and "]" in self.formatted_line:
            bracket_end = self.formatted_line.index("]")
            return f"{self.formatted_line[:bracket_end]}·{age}{self.formatted_line[bracket_end:]}"
        return f"[游戏公屏·{age}] {self.formatted_line}"


def _sender_key(sender: str) -> str:
    """发言人归一化键（去空白、大小写不敏感）。"""
    return (sender or "").strip().casefold()


def _content_key(content: str) -> str:
    """消息正文去重键（折叠连续空白、大小写不敏感）。"""
    return " ".join((content or "").split()).casefold()


def _extract_distance(payload: GameChatPayload) -> Optional[float]:
    """兼容读取可选的 ``sender_distance`` 属性：缺失、None 或非法数值时返回 None。"""
    raw = getattr(payload, "sender_distance", None)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if math.isfinite(value) and value >= 0.0:
            return value
    return None


def _has_self_mention(payload: GameChatPayload) -> bool:
    """判断消息结构化点名列表中是否带有 ``type="self"`` 的自提及条目。"""
    return any(getattr(mention, "type", "") == _SELF_MENTION_TYPE for mention in (payload.mentions or ()))


def _format_fact_tag(
    *,
    private: bool,
    mentioned_self: bool,
    distance: Optional[float],
    nearby_distance: float,
    in_nearby_set: bool,
) -> str:
    """按客观事实生成游戏公屏标签：私聊 > 点名 > 身边距离（或降级身边） > 远处。"""
    if private:
        return "[游戏公屏·私聊]"
    if mentioned_self:
        return "[游戏公屏·点名你]"
    if distance is not None and distance <= nearby_distance:
        return f"[游戏公屏·身边{int(round(distance))}格]"
    if distance is None and in_nearby_set:
        return "[游戏公屏·身边]"
    return "[游戏公屏·远处]"


@dataclass(frozen=True)
class ChatIngestResult:
    """单条游戏公屏进入门控后的即时分类回执（供接线层决定是否挂起刷出定时器）。"""

    accepted: bool
    should_schedule_flush: bool
    routed_to: str
    priority: int
    formatted_line: str
    due_in_ms: Optional[int] = None


@dataclass(frozen=True)
class ChatBatch:
    """到窗产出或行动步顺带取出的游戏公屏批次。

    ``should_wake=True`` 为 ``[游戏公屏合批]``，触发唤醒推理；
    ``should_wake=False`` 为 ``[游戏公屏旁听]``，仅在下次行动步顺带附入，绝不主动唤醒。
    """

    should_wake: bool
    text: str
    lines: Tuple[str, ...]
    count: int
    dropped_count: int = 0
    has_private: bool = False
    has_self_mention: bool = False


@dataclass
class _PendingChatItem:
    """合并窗或旁听滑窗内的一条聚合记录（支持同人同内容与高频连发折叠计数）。"""

    seq: int
    priority: int
    tag: str
    sender: str
    sender_norm: str
    note: str
    content: str
    content_norm: str
    private: bool
    mentioned_self: bool
    first_ts_ms: int
    last_ts_ms: int
    sender_display: str = ""
    repeat_count: int = 1
    collapsed_extra: int = 0
    upstream_suppressed: int = 0

    def render(self) -> str:
        """渲染成带事实标签、常用称呼、未核验说明与折叠计数的单行游戏公屏文本。"""
        # 发言人抬头优先使用已锚定常用称呼的显示名；无别名时退回原名
        speaker = self.sender_display or self.sender or "未知发言人"
        if self.note:
            # 发言人已带「（常叫：…）」时合并到同一对括号内，与归属层保持同源整洁格式
            if speaker.endswith("）") and "（" in speaker:
                speaker = f"{speaker[:-1]}；{self.note}）"
            else:
                speaker = f"{speaker}（{self.note}）"
        line = f"{self.tag} {speaker}：{self.content}"
        suffixes: List[str] = []
        if self.repeat_count > 1:
            suffixes.append(f"连发 ×{self.repeat_count}")
        if self.collapsed_extra > 0:
            suffixes.append(f"同人另发 {self.collapsed_extra} 条已折叠")
        if self.upstream_suppressed > 0:
            suffixes.append(f"上游已合并 {self.upstream_suppressed} 条")
        if suffixes:
            line = f"{line}（{'；'.join(suffixes)}）"
        return line


class MinecraftChatGate:
    """玩家游戏公屏分级门控：2 秒短合并窗、优先级截留、刷屏冷却、远处旁听与近期原话滚动窗。"""

    def __init__(
        self,
        *,
        clock: Optional[Callable[[], int]] = None,
        batch_window_ms: int = DEFAULT_BATCH_WINDOW_MS,
        max_batch_items: int = DEFAULT_MAX_BATCH_ITEMS,
        nearby_distance: float = DEFAULT_NEARBY_DISTANCE,
        partner_window_ms: int = DEFAULT_PARTNER_WINDOW_MS,
        wake_cooldown_ms: int = DEFAULT_WAKE_COOLDOWN_MS,
        spam_burst_threshold: int = _SPAM_BURST_THRESHOLD,
        max_eavesdrop_items: int = DEFAULT_MAX_EAVESDROP_ITEMS,
        max_recent_chats: int = DEFAULT_MAX_RECENT_CHATS,
        max_recent_suggestions: int = DEFAULT_MAX_RECENT_SUGGESTIONS,
        recent_chat_ttl_ms: int = DEFAULT_RECENT_CHAT_TTL_MS,
    ) -> None:
        self._clock = clock if clock is not None else _monotonic_ms
        self._batch_window_ms = max(0, int(batch_window_ms))
        self._max_batch_items = max(1, int(max_batch_items))
        self._nearby_distance = max(0.0, float(nearby_distance))
        self._partner_window_ms = max(0, int(partner_window_ms))
        self._wake_cooldown_ms = max(0, int(wake_cooldown_ms))
        self._spam_burst_threshold = max(1, int(spam_burst_threshold))
        self._max_eavesdrop_items = max(1, int(max_eavesdrop_items))
        self._max_recent_chats = max(1, int(max_recent_chats))
        self._max_recent_suggestions = max(1, int(max_recent_suggestions))
        self._recent_chat_ttl_ms = max(0, int(recent_chat_ttl_ms))

        self._seq = 0
        # 活跃唤醒合并窗与到期时刻（None 表示当前没有打开唤醒窗）
        self._wake_window: List[_PendingChatItem] = []
        self._window_due_ms: Optional[int] = None
        # 独立到达的远处非点名游戏公屏进入有界旁听滑窗，绝不主动唤醒
        self._eavesdrop: List[_PendingChatItem] = []
        self._eavesdrop_dropped = 0
        # 近期 2 分钟互动同伴记录：sender_norm -> 最近互动时刻（毫秒）
        self._partner_interactions: Dict[str, int] = {}
        # 持续刷屏检测：记录近期唤醒时刻与是否处于刷屏保护态
        self._recent_wake_times: Deque[int] = deque()
        self._last_wake_ms: Optional[int] = None
        self._spam_cooldown_active = False
        # 近期游戏公屏双向对话滚动窗（玩家游戏公屏 + 己方回复，带时间戳与 TTL 自动过期）与最近 4 条主播建议
        self._recent_chats: Deque[_RecentChatEntry] = deque(maxlen=self._max_recent_chats)
        self._recent_suggestions: Deque[str] = deque(maxlen=self._max_recent_suggestions)

    def clear_pending_windows(self) -> None:
        """清空待发唤醒合并窗、旁听滑窗与刷屏冷却计时，保留近期人类原话滚动窗与互动同伴记录。

        供进入或退出自主领航主控态时调用，防止上一环节滞留的过期窗口在下次重进时脏投递或误绕过旁听。
        """
        self._wake_window.clear()
        self._window_due_ms = None
        self._eavesdrop.clear()
        self._eavesdrop_dropped = 0
        self._recent_wake_times.clear()
        self._last_wake_ms = None
        self._spam_cooldown_active = False

    def clear(self) -> None:
        """清空合并窗、旁听滑窗、互动同伴记录与近期原话滚动窗（换档或下播时调用）。"""
        self.clear_pending_windows()
        self._partner_interactions.clear()
        self._recent_chats.clear()
        self._recent_suggestions.clear()

    def note_partner_interaction(self, sender: str) -> None:
        """登记近期与某位玩家发生了对话或互动：接下来 2 分钟内其游戏公屏视为可唤醒同伴消息。"""
        key = _sender_key(sender)
        if not key:
            return
        now = int(self._clock())
        self._prune_partners(now)
        self._partner_interactions[key] = now

    def is_recent_partner(self, sender: str, *, now_ms: Optional[int] = None) -> bool:
        """判断发言人是否属于近 2 分钟内互动过的同伴。"""
        key = _sender_key(sender)
        if not key:
            return False
        now = int(self._clock()) if now_ms is None else int(now_ms)
        self._prune_partners(now)
        last = self._partner_interactions.get(key)
        return last is not None and (now - last) <= self._partner_window_ms

    def ingest(
        self,
        payload: GameChatPayload,
        *,
        nearby_players: AbstractSet[str] = frozenset(),
        aliases_by_uuid: Optional[Mapping[str, Sequence[str]]] = None,
        aliases_by_name: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> ChatIngestResult:
        """接收一条游戏聊天事件，贴好事实标签后按优先级分流至唤醒合并窗或远处旁听滑窗。"""
        if payload.kind != "player":
            return ChatIngestResult(
                accepted=False,
                should_schedule_flush=False,
                routed_to="ignored",
                priority=PRIORITY_EAVESDROP,
                formatted_line="",
            )
        cleaned_content = " ".join((payload.content or "").split())
        if not cleaned_content:
            return ChatIngestResult(
                accepted=False,
                should_schedule_flush=False,
                routed_to="ignored",
                priority=PRIORITY_EAVESDROP,
                formatted_line="",
            )

        now = int(self._clock())
        sender = (payload.sender or "").strip()
        sender_norm = _sender_key(sender)
        note = " ".join((payload.note or "").split())
        private = bool(payload.private)
        mentioned_self = _has_self_mention(payload)
        distance = _extract_distance(payload)
        nearby_norms = {_sender_key(name) for name in nearby_players if name and name.strip()}
        in_nearby_set = bool(sender_norm) and sender_norm in nearby_norms
        is_nearby = (distance <= self._nearby_distance) if distance is not None else in_nearby_set
        is_partner = self.is_recent_partner(sender, now_ms=now)

        # 分级优先级：私聊(0) > 点名(1) > 身边≤24格或近2分钟互动同伴(2) > 其余远处非点名游戏公屏(3)
        if private:
            priority = PRIORITY_PRIVATE
        elif mentioned_self:
            priority = PRIORITY_SELF_MENTION
        elif is_nearby or is_partner:
            priority = PRIORITY_NEARBY_OR_PARTNER
        else:
            priority = PRIORITY_EAVESDROP

        tag = _format_fact_tag(
            private=private,
            mentioned_self=mentioned_self,
            distance=distance,
            nearby_distance=self._nearby_distance,
            in_nearby_set=in_nearby_set,
        )
        self._seq += 1
        # 有登记常用称呼时按 UUID 优先、唯一账号名兜底格式化为「Name（常叫：Alias）」，无别名平滑退回原名
        sender_display = format_chat_sender(
            sender,
            sender_id=str(getattr(payload, "sender_id", "") or ""),
            aliases_by_uuid=aliases_by_uuid,
            aliases_by_name=aliases_by_name,
        )
        item = _PendingChatItem(
            seq=self._seq,
            priority=priority,
            tag=tag,
            sender=sender,
            sender_norm=sender_norm,
            note=note,
            content=cleaned_content,
            content_norm=_content_key(cleaned_content),
            private=private,
            mentioned_self=mentioned_self,
            first_ts_ms=now,
            last_ts_ms=now,
            sender_display=sender_display,
            upstream_suppressed=max(0, int(payload.suppressed or 0)),
        )
        formatted_line = item.render()
        self._remember_recent_chat(formatted_line, sender, cleaned_content, timestamp_ms=now)

        # 独立到达的远处非点名游戏公屏：若当前没有活跃唤醒窗，直接进入旁听滑窗，绝不主动唤醒；
        # 若当前已有唤醒窗正在攒批，可作为最低优先级抽样候补随窗参与截留（不挤掉任何高优先级消息）。
        if priority == PRIORITY_EAVESDROP and self._window_due_ms is None:
            self._append_eavesdrop(item)
            return ChatIngestResult(
                accepted=True,
                should_schedule_flush=False,
                routed_to="eavesdrop",
                priority=priority,
                formatted_line=formatted_line,
                due_in_ms=None,
            )

        self._merge_into_bucket(self._wake_window, item)
        opened_new_window = False
        if self._window_due_ms is None:
            opened_new_window = True
            self._window_due_ms = self._compute_window_due_ms(now)
        due_in = max(0, self._window_due_ms - now)
        return ChatIngestResult(
            accepted=True,
            should_schedule_flush=opened_new_window,
            routed_to="wake_window",
            priority=priority,
            formatted_line=formatted_line,
            due_in_ms=due_in,
        )

    def next_flush_delay_ms(self) -> Optional[int]:
        """返回距离当前活跃唤醒合并窗到点还剩多少毫秒；无活跃窗返回 ``None``，已到点返回 ``0``。"""
        if self._window_due_ms is None or not self._wake_window:
            return None
        return max(0, self._window_due_ms - int(self._clock()))

    def flush_due(self, *, force: bool = False) -> Optional[ChatBatch]:
        """到窗（或强制）刷出 ``[游戏公屏合批]``，按优先级截留至最多 8 条并更新刷屏冷却状态。"""
        if not self._wake_window or self._window_due_ms is None:
            return None
        now = int(self._clock())
        if not force and now < self._window_due_ms:
            return None

        items = self._wake_window
        self._wake_window = []
        self._window_due_ms = None

        # 仅保留优先级更高者在前（私聊 > 点名 > 身边/同伴 > 其余抽样），同级按首次到达先后稳定保序；
        # 选出上限条数后再按原到达顺序展示，方便模型按时间顺读对话。
        ranked = sorted(items, key=lambda entry: (entry.priority, entry.seq))
        selected = sorted(ranked[: self._max_batch_items], key=lambda entry: entry.seq)
        dropped_count = max(0, len(items) - len(selected))

        had_burst = dropped_count > 0 or any(entry.repeat_count >= 3 or entry.collapsed_extra > 0 for entry in items)
        self._record_wake_event(now, had_burst=had_burst)

        rendered_lines = tuple(entry.render() for entry in selected)
        header = f"[游戏公屏合批] 收到 {len(rendered_lines)} 条玩家消息"
        if dropped_count > 0:
            header += f"（另有 {dropped_count} 条低优先级消息已省略）"
        text = "\n".join([header, *(f"- {line}" for line in rendered_lines)])
        return ChatBatch(
            should_wake=True,
            text=text,
            lines=rendered_lines,
            count=len(rendered_lines),
            dropped_count=dropped_count,
            has_private=any(entry.private for entry in selected),
            has_self_mention=any(entry.mentioned_self for entry in selected),
        )

    def drain_eavesdrop(self) -> Optional[ChatBatch]:
        """在行动步顺带取出有界旁听滑窗中的远处非点名游戏公屏（``should_wake=False``，绝不主动唤醒）。"""
        if not self._eavesdrop:
            return None
        items = self._eavesdrop
        dropped = self._eavesdrop_dropped
        self._eavesdrop = []
        self._eavesdrop_dropped = 0

        rendered_lines = tuple(entry.render() for entry in items)
        header = f"[游戏公屏旁听] 远处非点名游戏公屏 {len(rendered_lines)} 条（顺带知悉，无需刻意回复）"
        if dropped > 0:
            header += f"（较早 {dropped} 条已滑出）"
        text = "\n".join([header, *(f"- {line}" for line in rendered_lines)])
        return ChatBatch(
            should_wake=False,
            text=text,
            lines=rendered_lines,
            count=len(rendered_lines),
            dropped_count=dropped,
            has_private=False,
            has_self_mention=False,
        )

    def record_self_chat(self, text: str, now_ms: Optional[int] = None) -> None:
        """记入己方发出的游戏公屏句，与玩家游戏公屏配对构成双向近期对话（避免折叠/状态恢复后忘掉自己回过话）。"""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        ts = int(self._clock()) if now_ms is None else int(now_ms)
        self._prune_recent_chats(ts)
        if self._recent_chats and self._recent_chats[-1].is_self and self._recent_chats[-1].raw_content == cleaned:
            self._recent_chats[-1].timestamp_ms = ts
            return
        self._recent_chats.append(
            _RecentChatEntry(
                formatted_line=f"[你回过] {cleaned}",
                sender="",
                raw_content=cleaned,
                timestamp_ms=ts,
                is_self=True,
            )
        )

    def record_streamer_suggestion(self, text: str, *, timestamp_ms: Optional[int] = None) -> None:
        """记入近期主播建议滚动窗（默认保留最近 4 条，去空与相邻重复）。"""
        _ = timestamp_ms
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        if self._recent_suggestions and self._recent_suggestions[-1] == cleaned:
            return
        self._recent_suggestions.append(cleaned)

    def recent_player_chats(self, *, now_ms: Optional[int] = None) -> Tuple[str, ...]:
        """返回尚未过期的近期格式化玩家游戏公屏原话（旧 → 新，不含己方回复）。"""
        now = int(self._clock()) if now_ms is None else int(now_ms)
        self._prune_recent_chats(now)
        return tuple(entry.formatted_line for entry in self._recent_chats if not entry.is_self)

    def recent_chat_dialogues(self, *, now_ms: Optional[int] = None) -> Tuple[str, ...]:
        """返回尚未过期的近期双向游戏公屏对话（玩家游戏公屏 + 己方回复，每条附带相对时间前缀，旧 → 新）。"""
        now = int(self._clock()) if now_ms is None else int(now_ms)
        self._prune_recent_chats(now)
        return tuple(entry.render_with_age(now) for entry in self._recent_chats)

    def recent_streamer_suggestions(self) -> Tuple[str, ...]:
        """返回最近至多 4 条主播建议原话（旧 → 新）。"""
        return tuple(self._recent_suggestions)

    def recent_human_texts(self, *, now_ms: Optional[int] = None) -> Tuple[str, ...]:
        """返回未过期游戏公屏双向正文与主播建议原文元组，供短期记忆检索做近端原文去重。

        带未核验 ``note`` 的短游戏公屏额外附入 ``发言人：正文`` 形态，确保与短期记忆条目精确对上号。
        """
        now = int(self._clock()) if now_ms is None else int(now_ms)
        self._prune_recent_chats(now)
        texts: List[str] = []
        for entry in self._recent_chats:
            texts.append(entry.raw_content)
            texts.append(entry.formatted_line)
            if entry.is_self:
                texts.append(f"你回过：{entry.raw_content}")
            elif entry.sender:
                texts.append(f"{entry.sender}：{entry.raw_content}")
        texts.extend(self._recent_suggestions)
        return tuple(texts)

    def format_recent_human_lines(self, *, now_ms: Optional[int] = None) -> str:
        """把未过期的近期游戏公屏双向对话与主播建议渲染成紧凑文本块，供任务状态与集中整理保真。"""
        now = int(self._clock()) if now_ms is None else int(now_ms)
        dialogues = self.recent_chat_dialogues(now_ms=now)
        suggestions = self.recent_streamer_suggestions()
        if not dialogues and not suggestions:
            return ""
        lines: List[str] = ["[近期人类原话]"]
        if suggestions:
            lines.append("近期主播建议：")
            lines.extend(f"- {item}" for item in suggestions)
        if dialogues:
            lines.append("近期玩家游戏公屏：")
            lines.extend(f"- {item}" for item in dialogues)
        return "\n".join(lines)

    @property
    def in_spam_cooldown(self) -> bool:
        """当前是否处于持续刷屏保护冷却态（诊断与单测用）。"""
        if not self._spam_cooldown_active or self._last_wake_ms is None:
            return False
        return (int(self._clock()) - self._last_wake_ms) < self._wake_cooldown_ms * 2

    def _compute_window_due_ms(self, now: int) -> int:
        """计算新开唤醒窗的到期时刻：常态为 2 秒短合并窗，持续刷屏态启用最小唤醒冷却。"""
        normal_due = now + self._batch_window_ms
        if self._last_wake_ms is None:
            return normal_due
        # 长时间静默（超过两倍冷却期）自动解除刷屏保护态
        if (now - self._last_wake_ms) >= self._wake_cooldown_ms * 2:
            self._spam_cooldown_active = False
            return normal_due
        if self._spam_cooldown_active:
            return max(normal_due, self._last_wake_ms + self._wake_cooldown_ms)
        return normal_due

    def _record_wake_event(self, now: int, *, had_burst: bool) -> None:
        """更新唤醒频次历史：短时间内连续唤醒达门槛或发生单窗刷屏折叠即激活冷却保护。"""
        self._last_wake_ms = now
        self._recent_wake_times.append(now)
        cutoff = now - _SPAM_TRACK_WINDOW_MS
        while self._recent_wake_times and self._recent_wake_times[0] < cutoff:
            self._recent_wake_times.popleft()
        if had_burst or len(self._recent_wake_times) >= self._spam_burst_threshold:
            self._spam_cooldown_active = True

    def _prune_partners(self, now: int) -> None:
        """清理超过 2 分钟窗口的旧互动同伴记录。"""
        expired = [key for key, ts in self._partner_interactions.items() if (now - ts) > self._partner_window_ms]
        for key in expired:
            self._partner_interactions.pop(key, None)

    def _prune_recent_chats(self, now_ms: int) -> None:
        """淘汰超过存活窗口（默认 10 分钟）的陈旧游戏公屏记录，防止几十分钟前的旧话长期挂在任务状态里。"""
        if self._recent_chat_ttl_ms <= 0 or not self._recent_chats:
            return
        cutoff = now_ms - self._recent_chat_ttl_ms
        if all(entry.timestamp_ms >= cutoff for entry in self._recent_chats):
            return
        fresh = [entry for entry in self._recent_chats if entry.timestamp_ms >= cutoff]
        self._recent_chats = deque(fresh, maxlen=self._max_recent_chats)

    def _remember_recent_chat(
        self,
        formatted_line: str,
        sender: str,
        raw_content: str,
        *,
        timestamp_ms: int,
    ) -> None:
        """维护近期游戏公屏滚动窗，淘汰过期记录并对紧邻相同文本刷新时间戳不重复占坑。"""
        self._prune_recent_chats(timestamp_ms)
        if (
            self._recent_chats
            and not self._recent_chats[-1].is_self
            and self._recent_chats[-1].formatted_line == formatted_line
        ):
            self._recent_chats[-1].timestamp_ms = timestamp_ms
            return
        self._recent_chats.append(
            _RecentChatEntry(
                formatted_line=formatted_line,
                sender=sender,
                raw_content=raw_content,
                timestamp_ms=timestamp_ms,
                is_self=False,
            )
        )

    def _append_eavesdrop(self, item: _PendingChatItem) -> None:
        """写入远处旁听滑窗：同人同内容折叠计数，超有界上限时滑出最老条并计数。"""
        self._merge_into_bucket(self._eavesdrop, item)
        while len(self._eavesdrop) > self._max_eavesdrop_items:
            self._eavesdrop.pop(0)
            self._eavesdrop_dropped += 1

    @staticmethod
    def _merge_into_bucket(bucket: List[_PendingChatItem], incoming: _PendingChatItem) -> None:
        """合并同人同内容或单人高频连发消息，防止同人刷屏挤爆窗口配额。

        高优先级消息（特别是私聊与点名）绝不被前序公开刷屏吞并丢弃，也绝不把公开条目
        错改标为 ``[游戏公屏·私聊]``；超限时仅淘汰并折叠同人优先级最低的条目。
        """
        same_sender_items: List[_PendingChatItem] = []
        for existing in bucket:
            if existing.sender_norm != incoming.sender_norm:
                continue
            same_sender_items.append(existing)
            # 私聊与公开消息绝不混并，避免公开消息被错标成私聊或私聊退化为公开
            if existing.content_norm == incoming.content_norm and existing.private == incoming.private:
                existing.repeat_count += 1
                existing.last_ts_ms = incoming.last_ts_ms
                existing.upstream_suppressed += incoming.upstream_suppressed
                if incoming.priority < existing.priority:
                    existing.priority = incoming.priority
                    existing.tag = incoming.tag
                existing.mentioned_self = existing.mentioned_self or incoming.mentioned_self
                if incoming.note and not existing.note:
                    existing.note = incoming.note
                if incoming.sender_display and (
                    not existing.sender_display
                    or ("（常叫：" in incoming.sender_display and "（常叫：" not in existing.sender_display)
                ):
                    existing.sender_display = incoming.sender_display
                return

        # 同一发言人在同一短窗内连发超过上限条不同内容时：
        # 若新消息优先级高于同人现有最低优条目，保留新消息原文并淘汰同人最低优条目计入折叠数；
        # 否则仅将新消息折叠计入同人现有最低优（且不跨私聊/公开属性）条目，绝不篡改已有标签。
        if incoming.sender_norm and len(same_sender_items) >= _MAX_DISTINCT_PER_SENDER:
            worst = max(same_sender_items, key=lambda entry: (entry.priority, entry.seq))
            if incoming.priority < worst.priority:
                bucket.remove(worst)
                same_sender_items.remove(worst)
                folded_count = 1 + worst.collapsed_extra
                # 优先把被淘汰的公开条目计数挂在同人剩余的非私聊条目上，保持私聊与公开语义清晰
                public_peers = [entry for entry in same_sender_items if not entry.private]
                carrier = public_peers[-1] if public_peers else incoming
                carrier.collapsed_extra += folded_count
                carrier.upstream_suppressed += worst.upstream_suppressed
                bucket.append(incoming)
                return

            same_privacy_peers = [entry for entry in same_sender_items if entry.private == incoming.private]
            target = (
                max(same_privacy_peers, key=lambda entry: (entry.priority, entry.seq)) if same_privacy_peers else worst
            )
            target.collapsed_extra += 1
            target.last_ts_ms = incoming.last_ts_ms
            target.upstream_suppressed += incoming.upstream_suppressed
            return

        bucket.append(incoming)
