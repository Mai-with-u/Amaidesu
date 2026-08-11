"""RoomState - 直播间态势规则层

计算直播间的"态势快照"（态势 = heat + topics + SC 队列），供 AmaidesuDecider
的 Planner 注入 prompt 使用。**纯规则、零 LLM 调用**（低频 LLM 话题摘要是
Task 8 的职责，本模块只预留 ``topic_summary`` 字段）。

设计要点：
- **可注入时钟**：``update()`` / ``get_snapshot()`` 均接受可选 ``now_ms`` 参数，
  测试中传入确定性时间戳即可重现任意热度场景，**禁止**依赖 ``time.sleep``。
- **60 秒滑动窗口**：仅保留最近 60 秒内的弹幕用于热度 / 词频计算。
- **字符级词频**：中文无分词库依赖，按字符频次聚类即可得到粗粒度话题（够用即可，
  更精细的 NLP 留给 Task 8 的 LLM 摘要）。

热度阈值（基于弹幕速率 msg/s，窗口 60s）：
    < 0.1 msg/s (即 < 1 条/10s) → "low"    冷场
    < 0.5 msg/s (即 < 1 条/2s)  → "medium" 正常节奏
    >= 0.5 msg/s                → "high"   高热
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from src.modules.time_utils import now_ms as _real_now_ms

__all__ = ["RoomStateSnapshot", "RoomState"]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 热度计算窗口（毫秒）
HEAT_WINDOW_MS: int = 60_000

#: 计算速率时采用的观察时段上下界（毫秒）
#: - 下界 2s：与 "1 条/2s" 阈值参考期一致，避免单条/瞬时报点导致虚高
#: - 上界 60s：即 HEAT_WINDOW_MS
HEAT_SPAN_MIN_MS: int = 2_000

#: 低热阈值：低于此速率（条/秒）视为 cold
HEAT_LOW_THRESHOLD_MPS: float = 0.1
#: 高热阈值：大于等于此速率（条/秒）视为 hot
HEAT_HIGH_THRESHOLD_MPS: float = 0.5

#: topics 返回的最多关键词数
TOPIC_TOP_K: int = 5

#: 中文停用词表（高频无意义字/语气词）。后续可扩展。
_STOPWORDS: frozenset[str] = frozenset(
    "的了吗啊呀呢吧哦呐哈你我他她它是最和在有无要都也就还又把这被让给跟和与及个么不没很太非常"
    "啦呗嘛嘞喔噢唔诶哎嗯哇嘿嘻哈"
)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class RoomStateSnapshot:
    """直播间态势快照

    Attributes:
        heat: 热度等级，"low" | "medium" | "high"（基于弹幕速率）
        topics: 话题关键词列表（字符级词频聚类，按频次降序）
        sc_queue: 待处理的 SC/礼物/上舰消息列表
        last_update_ms: 本次快照生成时刻（Unix 毫秒）
        topic_summary: 低频 LLM 话题摘要（可选，Task 8 填充，默认空串）
        topic_summary_at_ms: 话题摘要的生成时刻（Unix 毫秒，0 表示尚未生成）
    """

    heat: str
    topics: List[str]
    sc_queue: List[dict[str, Any]]
    last_update_ms: int
    topic_summary: str = ""
    topic_summary_at_ms: int = 0


# ---------------------------------------------------------------------------
# RoomState
# ---------------------------------------------------------------------------


@dataclass
class _WindowEntry:
    """滑动窗口内的一条弹幕记录（轻量内部结构）"""

    ts_ms: int
    text: str


class RoomState:
    """直播间态势规则层

    非线程安全；仅在 AmaidesuDecider 的单一 asyncio 事件循环内使用。
    通过方法调用驱动（``update`` / ``push_sc`` / ``drain_sc``），
    **不订阅 EventBus**——它是 Decider 内部子组件。
    """

    def __init__(self) -> None:
        # 弹幕滑动窗口：(ts_ms, text)
        self._window: List[_WindowEntry] = []
        # SC / 礼物 / 上舰 待处理队列
        self._sc_queue: List[dict[str, Any]] = []
        # 话题摘要（由 RoomStateLoop 低频 LLM 填充，内存态）
        self._topic_summary: str = ""
        self._topic_summary_at_ms: int = 0
        # 最近一条弹幕到达时刻（不受滑动窗口裁剪影响，用于冷场判定）
        self._last_message_ms: Optional[int] = None
        # 最近一次主播主动发言时刻（与弹幕并列但互不影响；
        # 供 ProactiveTrigger 的 min_interval 频率限制使用）
        self._last_speech_ms: Optional[int] = None
        # 累计主动发言次数（用于 max_per_hour 频率限制统计）
        self._speech_count: int = 0

    # ------------------------------------------------------------------
    # 时钟注入
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_now(now_ms: Optional[int]) -> int:
        """解析可选的 now_ms，None 时退回真实时钟

        Args:
            now_ms: 调用方注入的时刻；None 表示使用真实时钟

        Returns:
            Unix 毫秒时间戳
        """
        return now_ms if now_ms is not None else _real_now_ms()

    # ------------------------------------------------------------------
    # 弹幕更新
    # ------------------------------------------------------------------

    def update(self, message, *, now_ms: Optional[int] = None) -> None:
        """记录一条弹幕消息，维护热度窗口。

        Args:
            message: 标准化消息（仅需具备 ``text`` 字段；
                ``NormalizedMessage`` 或 duck-typed 对象均可）
            now_ms: 消息到达时刻（Unix 毫秒）。测试中传入确定性时间戳；
                None 时使用真实时钟。
        """
        ts = self._resolve_now(now_ms)
        text = getattr(message, "text", "") or ""
        self._window.append(_WindowEntry(ts_ms=ts, text=text))
        self._last_message_ms = ts
        self._trim(ts)

    def _trim(self, now_ms: int) -> None:
        """裁剪窗口，仅保留 [now_ms - HEAT_WINDOW_MS, now_ms] 内的条目"""
        cutoff = now_ms - HEAT_WINDOW_MS
        # 从头部删除（按时间升序追加，旧条目在前）
        while self._window and self._window[0].ts_ms < cutoff:
            self._window.pop(0)

    # ------------------------------------------------------------------
    # 热度计算
    # ------------------------------------------------------------------

    def _compute_heat(self, now_ms: int) -> str:
        """根据窗口内弹幕密度计算热度等级

        速率 = 窗口内条数 / 观察时段，观察时段取 ``min(now-最旧条目, 60s)`` 并夹在
        ``[HEAT_SPAN_MIN_MS, HEAT_WINDOW_MS]``。需要至少 2 条消息才能建立速率，
        否则视为 low（单条消息无法体现"热度"）。

        Returns:
            "low" | "medium" | "high"
        """
        self._trim(now_ms)
        count = len(self._window)
        if count < 2:
            return "low"
        oldest = self._window[0].ts_ms
        span_ms = now_ms - oldest
        if span_ms < HEAT_SPAN_MIN_MS:
            span_ms = HEAT_SPAN_MIN_MS
        elif span_ms > HEAT_WINDOW_MS:
            span_ms = HEAT_WINDOW_MS
        rate_mps = count / (span_ms / 1000.0)
        if rate_mps < HEAT_LOW_THRESHOLD_MPS:
            return "low"
        if rate_mps < HEAT_HIGH_THRESHOLD_MPS:
            return "medium"
        return "high"

    # ------------------------------------------------------------------
    # 话题提取（字符级词频）
    # ------------------------------------------------------------------

    def _extract_topics(self, now_ms: int) -> List[str]:
        """从窗口内弹幕提取话题关键词（字符级频次，停用词过滤）

        Args:
            now_ms: 当前时刻，用于裁剪窗口

        Returns:
            按频次降序排列的前 TOPIC_TOP_K 个字符
        """
        self._trim(now_ms)
        freq: dict[str, int] = {}
        for entry in self._window:
            for ch in entry.text:
                # 只保留 CJK 汉字：过滤标点 / emoji / 数字 / 字母与停用词
                # （实测日志中出现过 "？。！✨" 等高频噪声霸榜话题关键词）
                if ch.isspace() or not ("\u4e00" <= ch <= "\u9fff") or ch in _STOPWORDS:
                    continue
                freq[ch] = freq.get(ch, 0) + 1
        if not freq:
            return []
        # 按频次降序，平手时按字典序稳定排序
        ranked: List[Tuple[str, int]] = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        return [ch for ch, _ in ranked[:TOPIC_TOP_K]]

    # ------------------------------------------------------------------
    # 话题摘要（由 RoomStateLoop 填充）
    # ------------------------------------------------------------------

    def set_topic_summary(self, summary: str, *, now_ms: Optional[int] = None) -> None:
        """存储低频 LLM 话题摘要（内存态，不持久化）

        Args:
            summary: 摘要文本
            now_ms: 生成时刻（Unix 毫秒）；None 时使用真实时钟
        """
        ts = self._resolve_now(now_ms)
        self._topic_summary = summary
        self._topic_summary_at_ms = ts

    # ------------------------------------------------------------------
    # 冷场判定
    # ------------------------------------------------------------------

    def is_cold(self, cold_timeout_ms: int, *, now_ms: Optional[int] = None) -> bool:
        """判断房间是否处于冷场状态（超过 cold_timeout_ms 无新弹幕）

        Args:
            cold_timeout_ms: 冷场判定阈值（毫秒）
            now_ms: 当前时刻；None 时使用真实时钟

        Returns:
            True 表示冷场（应暂停 LLM 摘要以控制成本）
        """
        if self._last_message_ms is None:
            return True
        ts = self._resolve_now(now_ms)
        return (ts - self._last_message_ms) > cold_timeout_ms

    # ------------------------------------------------------------------
    # 主动发言时刻跟踪（供 ProactiveTrigger 使用）
    # ------------------------------------------------------------------

    def record_speech(self, now_ms: Optional[int] = None) -> None:
        """记录一次主播主动发言（由 AmaidesuDecider 在发布 Intent 后调用）

        与 ``update()``（弹幕）并列：``update`` 更新 ``_last_message_ms``，
        ``record_speech`` 更新 ``_last_speech_ms``，两者互不影响。
        冷场判定仍只看弹幕（``_last_message_ms``），主动发言不重置冷场计时器。

        Args:
            now_ms: 发言时刻（Unix 毫秒）。None 时使用真实时钟。
        """
        ts = self._resolve_now(now_ms)
        self._last_speech_ms = ts
        self._speech_count += 1

    @property
    def last_speech_ms(self) -> Optional[int]:
        """最近一次主动发言时刻（Unix 毫秒），未发言时为 None

        注意：与 ``_last_message_ms``（弹幕时刻）并列，二者独立。
        ProactiveTrigger 用此字段实现 ``min_interval_ms`` 频率限制，
        避免主播刚刚说完话立刻又被触发。
        """
        return self._last_speech_ms

    @property
    def last_message_ms(self) -> Optional[int]:
        """最近一条弹幕到达时刻（Unix 毫秒），尚未收到弹幕时为 None

        供 RoomStateLoop 判断"上次摘要后是否有新弹幕"：
        无新弹幕时摘要内容不会变化，可跳过 LLM 刷新。
        """
        return self._last_message_ms

    @property
    def speech_count(self) -> int:
        """累计主动发言次数（自进程启动以来）

        供 ProactiveTrigger 的 ``max_per_hour`` 频率限制统计使用。
        """
        return self._speech_count

    # ------------------------------------------------------------------
    # SC / 礼物 / 上舰 队列
    # ------------------------------------------------------------------

    def push_sc(self, sc_msg: dict[str, Any], *, now_ms: Optional[int] = None) -> None:
        """压入一条待处理 SC / 礼物 / 上舰消息

        Args:
            sc_msg: SC 消息字典（结构由调用方约定，通常含 message_id/text/amount）
            now_ms: 保留参数，便于日志对齐；不影响队列逻辑
        """
        self._sc_queue.append(sc_msg)

    def drain_sc(self) -> List[dict[str, Any]]:
        """取出并清空 SC 队列

        Returns:
            按 push 顺序排列的 SC 消息列表（可能为空）
        """
        drained = self._sc_queue
        self._sc_queue = []
        return drained

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> RoomStateSnapshot:
        """生成当前态势快照

        Args:
            now_ms: 快照时刻（Unix 毫秒）；None 时使用真实时钟

        Returns:
            RoomStateSnapshot（SC 队列为副本，外部修改不影响内部状态）
        """
        ts = self._resolve_now(now_ms)
        return RoomStateSnapshot(
            heat=self._compute_heat(ts),
            topics=self._extract_topics(ts),
            sc_queue=list(self._sc_queue),
            last_update_ms=ts,
            topic_summary=self._topic_summary,
            topic_summary_at_ms=self._topic_summary_at_ms,
        )
