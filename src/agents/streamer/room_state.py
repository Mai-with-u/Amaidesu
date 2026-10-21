"""RoomState - 直播间态势规则层（后台记账职责）

计算直播间的"态势快照"（态势 = heat + topics + SC 队列），供 Streamer Agent
的 Planner 注入 prompt 使用。**纯规则、零 LLM 调用**。

设计要点：
- **可注入时钟**:`update()` / `get_snapshot()` 均接受可选 ``now_ms`` 参数,
  测试中传入确定性时间戳即可重现任意热度场景,**禁止**依赖 ``time.sleep``。
- **60 秒滑动窗口**:仅保留最近 60 秒内的弹幕用于热度 / 词频计算。
- **字符级词频**:中文无分词库依赖,按字符频次聚类即可得到粗粒度话题(够用即可,
  更精细的 NLP 留给 background.py 压缩 worker 中的 LLM 摘要)。
- **持久化去耦合**:RoomState 不直接写数据库,纯内存滑动窗口 + 末次快照缓存;
  后台记账由 background.py 周期读取 get_snapshot() → 写 ``live_sessions`` 表
  (双任务模型的轻循环)。

热度阈值(基于弹幕速率 msg/s,窗口 60s):
    < 0.1 msg/s (即 < 1 条/10s) → "low"    冷场
    < 0.5 msg/s (即 < 1 条/2s)  → "medium" 正常节奏
    >= 0.5 msg/s                → "high"   高热
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.utils.time_utils import now_ms as _real_now_ms

__all__ = ["RoomStateSnapshot", "RoomState"]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 热度计算窗口(毫秒)
HEAT_WINDOW_MS: int = 60_000

#: 计算速率时采用的观察时段上下界(毫秒)
#: - 下界 2s:与 "1 条/2s" 阈值参考期一致,避免单条/瞬时报点导致虚高
#: - 上界 60s:即 HEAT_WINDOW_MS
HEAT_SPAN_MIN_MS: int = 2_000

#: 低热阈值:低于此速率(条/秒)视为 cold
HEAT_LOW_THRESHOLD_MPS: float = 0.1
#: 高热阈值:大于等于此速率(条/秒)视为 hot
HEAT_HIGH_THRESHOLD_MPS: float = 0.5

#: topics 返回的最多关键词数
TOPIC_TOP_K: int = 5

#: 观众进入增量的观察窗口（毫秒）：累计口径的导数，近似"最近有没有人进来"
AUDIENCE_WINDOW_MS: int = 600_000

#: 观众进入样本的容量上限（防御性：正常推送频率下远达不到）
_AUDIENCE_SAMPLE_CAP: int = 64

#: 进房记录的渲染窗口（毫秒）：activity_line 只报最近这段时间进房的观众
ENTER_WINDOW_MS: int = 600_000

#: 同一观众进房记录的限频（毫秒）：窗口内反复进出不重复记，欢迎不追着同一人刷
ENTER_DEDUP_MS: int = 1_800_000

#: activity_line 最多点名的进房观众数（控制决策上下文占用）
ENTER_RENDER_CAP: int = 5

#: 中文停用词表(高频无意义字/语气词)。后续可扩展。
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
        heat: 热度等级,"low" | "medium" | "high"(基于弹幕速率)
        topics: 话题关键词列表(字符级词频聚类,按频次降序)
        last_update_ms: 本次快照生成时刻(Unix 毫秒)
        topic_summary: 低频 LLM 话题摘要(可选,RoomStateLoop 填充,默认空串)
        topic_summary_at_ms: 话题摘要的生成时刻(Unix 毫秒,0 表示尚未生成)
        audience_total: 本场累计观看人次(room.state.watched_count 驱动;
            0 表示推送未到过,语义为"未知")
        audience_total_at_ms: 最近一次观看数推送到达时刻(Unix 毫秒,0 表示未收到过)
    """

    heat: str
    topics: List[str]
    last_update_ms: int
    topic_summary: str = ""
    topic_summary_at_ms: int = 0
    audience_total: int = 0
    audience_total_at_ms: int = 0


# ---------------------------------------------------------------------------
# RoomState
# ---------------------------------------------------------------------------


@dataclass
class _WindowEntry:
    """滑动窗口内的一条弹幕记录(轻量内部结构)"""

    ts_ms: int
    text: str


class RoomState:
    """直播间态势规则层。

    非线程安全;仅在 Streamer Agent 的单一 asyncio 事件循环内使用。
    通过方法调用驱动(``update``),
    **不订阅 EventBus**——它是 Agent 内部子组件。
    """

    def __init__(self) -> None:
        # 弹幕滑动窗口:(ts_ms, text)
        self._window: List[_WindowEntry] = []
        # 话题摘要(由 background.py 低频 LLM 填充,内存态)
        self._topic_summary: str = ""
        self._topic_summary_at_ms: int = 0
        # 最近一条弹幕到达时刻(不受滑动窗口裁剪影响,用于冷场判定)
        self._last_message_ms: Optional[int] = None
        # 最近一次主播主动发言时刻(与弹幕并列但互不影响;
        # 供 ProactiveTrigger 的 min_interval 频率限制使用)
        self._last_speech_ms: Optional[int] = None
        # 自启动以来的累计弹幕条数(不受滑动窗口裁剪影响)
        self._total_message_count: int = 0
        # 本场累计观看人次(room.state.watched_count 状态推送驱动;推送未到过
        # 时保持 0,语义为"未知"而非"没人看")
        self._audience_total: int = 0
        self._audience_total_at_ms: int = 0
        # 观看数推送样本 (ts, 累计人次):增量观察窗口的原料(累计口径只增不减,
        # 窗口两端之差 = 近 N 分钟新增进入)
        self._audience_samples: List[Tuple[int, int]] = []
        # 进房记录 (ts, user_id, 昵称)：room.message.enter 驱动，欢迎话术的原料
        self._enters: List[Tuple[int, str, str]] = []

    # ------------------------------------------------------------------
    # 时钟注入
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_now(now_ms: Optional[int]) -> int:
        """解析可选的 now_ms,None 时退回真实时钟

        Args:
            now_ms: 调用方注入的时刻;None 表示使用真实时钟

        Returns:
            Unix 毫秒时间戳
        """
        return now_ms if now_ms is not None else _real_now_ms()

    # ------------------------------------------------------------------
    # 弹幕更新
    # ------------------------------------------------------------------

    def update(self, message: RoomMessagePayload, *, now_ms: Optional[int] = None) -> None:
        """记录一条弹幕消息,维护热度窗口。

        Args:
            message: 直播间消息(仅需具备 ``content`` 字段;
                ``RoomMessagePayload`` 或 duck-typed 对象均可)
            now_ms: 消息到达时刻(Unix 毫秒)。测试中传入确定性时间戳;
                None 时使用真实时钟。
        """
        ts = self._resolve_now(now_ms)
        text = getattr(message, "content", "") or ""
        self._window.append(_WindowEntry(ts_ms=ts, text=text))
        self._last_message_ms = ts
        self._total_message_count += 1
        self._trim(ts)

    def _trim(self, now_ms: int) -> None:
        """裁剪窗口,仅保留 [now_ms - HEAT_WINDOW_MS, now_ms] 内的条目"""
        cutoff = now_ms - HEAT_WINDOW_MS
        # 从头部删除(按时间升序追加,旧条目在前)
        while self._window and self._window[0].ts_ms < cutoff:
            self._window.pop(0)

    # ------------------------------------------------------------------
    # 热度计算
    # ------------------------------------------------------------------

    def _compute_heat(self, now_ms: int) -> str:
        """根据窗口内弹幕密度计算热度等级

        速率 = 窗口内条数 / 观察时段,观察时段取 ``min(now-最旧条目, 60s)`` 并夹在
        ``[HEAT_SPAN_MIN_MS, HEAT_WINDOW_MS]``。需要至少 2 条消息才能建立速率,
        否则视为 low(单条消息无法体现"热度")。

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
    # 话题提取(字符级词频)
    # ------------------------------------------------------------------

    def _extract_topics(self, now_ms: int) -> List[str]:
        """从窗口内弹幕提取话题关键词(字符级频次,停用词过滤)

        Args:
            now_ms: 当前时刻,用于裁剪窗口

        Returns:
            按频次降序排列的前 TOPIC_TOP_K 个字符
        """
        self._trim(now_ms)
        freq: dict[str, int] = {}
        for entry in self._window:
            for ch in entry.text:
                # 只保留 CJK 汉字:过滤标点 / emoji / 数字 / 字母与停用词
                # (实测日志中出现过 "？。！✨" 等高频噪声霸榜话题关键词)
                if ch.isspace() or not ("\u4e00" <= ch <= "\u9fff") or ch in _STOPWORDS:
                    continue
                freq[ch] = freq.get(ch, 0) + 1
        if not freq:
            return []
        # 按频次降序,平手时按字典序稳定排序
        ranked: List[Tuple[str, int]] = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        return [ch for ch, _ in ranked[:TOPIC_TOP_K]]

    # ------------------------------------------------------------------
    # 话题摘要(由 background.py 填充)
    # ------------------------------------------------------------------

    def set_topic_summary(self, summary: str, *, now_ms: Optional[int] = None) -> None:
        """存储低频 LLM 话题摘要(内存态,不持久化)

        Args:
            summary: 摘要文本
            now_ms: 生成时刻(Unix 毫秒);None 时使用真实时钟
        """
        ts = self._resolve_now(now_ms)
        self._topic_summary = summary
        self._topic_summary_at_ms = ts

    def consume_topic_summary(self) -> str:
        """取出并清空当前话题摘要(决策面消费语义:读过即已读,不重复注入)

        摘要被一次决策窗取走后即清空;后台摘要循环只在有新弹幕时才生成新摘要,
        因此同一段"观众问好并表达惊喜"不会在多轮请求里反复出现。需要整体快照
        (只读、不清空)时用 ``get_snapshot``。

        Returns:
            当前摘要文本(取走后为空,直到后台写入新摘要)
        """
        summary = self._topic_summary
        self._topic_summary = ""
        self._topic_summary_at_ms = 0
        return summary

    # ------------------------------------------------------------------
    # 进房记录（room.message.enter 驱动）
    # ------------------------------------------------------------------

    def record_enter(self, user_id: str, user_name: str, *, now_ms: Optional[int] = None) -> None:
        """记录一位观众进房（内存态；同一观众在限频窗口内重复进房不重复记）。

        Args:
            user_id: 观众唯一 ID（限频的判定键）
            user_name: 昵称（activity_line 点名用）
            now_ms: 进房时刻（Unix 毫秒）；None 时使用真实时钟
        """
        ts = self._resolve_now(now_ms)
        for entry_ts, entry_id, _ in reversed(self._enters):
            if entry_id == user_id:
                if ts - entry_ts < ENTER_DEDUP_MS:
                    return
                break
        self._enters.append((ts, user_id, user_name))
        # 记录只为限频与渲染服务：超出限频窗口的旧记录裁掉，长播不无限增长
        cutoff = ts - ENTER_DEDUP_MS
        while self._enters and self._enters[0][0] < cutoff:
            self._enters.pop(0)

    def _recent_enter_names(self, now_ms: int) -> List[str]:
        """渲染窗口内最近进房的观众昵称（按进房先后，最多 ENTER_RENDER_CAP 位）。"""
        names = [name for ts, _, name in self._enters if ts >= now_ms - ENTER_WINDOW_MS]
        return names[-ENTER_RENDER_CAP:]

    # ------------------------------------------------------------------
    # 观众规模(状态推送驱动)
    # ------------------------------------------------------------------

    def set_audience_total(self, count: int, *, now_ms: Optional[int] = None) -> None:
        """更新本场累计观看人次(内存态;由 ``room.state.watched_count`` 驱动)

        Args:
            count: 累计观看人次(UV 口径,只增不减)
            now_ms: 推送到达时刻(Unix 毫秒);None 时使用真实时钟
        """
        ts = self._resolve_now(now_ms)
        self._audience_total = count
        self._audience_total_at_ms = ts
        self._audience_samples.append((ts, count))
        if len(self._audience_samples) > _AUDIENCE_SAMPLE_CAP:
            # 容量裁剪保留最近样本;窗口计算只需要两端,中间值丢弃不影响增量
            self._audience_samples = self._audience_samples[-_AUDIENCE_SAMPLE_CAP:]
        cutoff = ts - AUDIENCE_WINDOW_MS
        while len(self._audience_samples) > 1 and self._audience_samples[1][0] < cutoff:
            # 头部裁剪到窗口:窗口外只留一条作为增量基线(下一条即窗口内最早样本)
            self._audience_samples.pop(0)

    def _audience_increment(self, now_ms: int) -> Optional[int]:
        """观察窗口内的观众进入增量(累计口径两端之差)。

        Returns:
            增量人次;样本不足两端(少于 2 条)时返回 None,语义为"未知"。
        """
        samples = [(t, c) for t, c in self._audience_samples if t >= now_ms - AUDIENCE_WINDOW_MS]
        if len(samples) < 2:
            return None
        return samples[-1][1] - samples[0][1]

    def activity_line(self, *, now_ms: Optional[int] = None) -> str:
        """房间活跃度一句话（planner 参考段用，校准互动密度与发言策略）。

        事实陈述不带指令：近 10 分钟新增观看（累计口径的窗口增量；推送样本
        不足时如实说未知）+ 近 1 分钟弹幕量与热度等级 + 近 10 分钟进房的
        观众名单（限频去重后取最近几位，欢迎话术的点名原料）。全部有源：
        观看增量来自 ``room.state.watched_count`` 推送，弹幕量来自滑动窗口，
        进房名单来自 ``room.message.enter``。
        """
        ts = self._resolve_now(now_ms)
        self._trim(ts)
        increment = self._audience_increment(ts)
        if increment is None:
            audience_text = "近 10 分钟新增观看未知（推送样本不足）"
        else:
            audience_text = f"近 10 分钟新增观看 {increment}"
        enter_names = self._recent_enter_names(ts)
        enter_text = f"；近 10 分钟进房：{'、'.join(enter_names)}" if enter_names else ""
        return f"{audience_text}；近 1 分钟弹幕 {len(self._window)} 条（热度 {self._compute_heat(ts)}）{enter_text}"

    # ------------------------------------------------------------------
    # 冷场判定
    # ------------------------------------------------------------------

    def is_cold(self, cold_timeout_ms: int, *, now_ms: Optional[int] = None) -> bool:
        """判断房间是否处于冷场状态(超过 cold_timeout_ms 无新弹幕)

        Args:
            cold_timeout_ms: 冷场判定阈值(毫秒)
            now_ms: 当前时刻;None 时使用真实时钟

        Returns:
            True 表示冷场(应暂停 LLM 摘要以控制成本)
        """
        if self._last_message_ms is None:
            return True
        ts = self._resolve_now(now_ms)
        return (ts - self._last_message_ms) > cold_timeout_ms

    # ------------------------------------------------------------------
    # 主动发言时刻跟踪(供 ProactiveTrigger 使用)
    # ------------------------------------------------------------------

    def record_speech(self, now_ms: Optional[int] = None) -> None:
        """记录一次主播主动发言(由 StreamerAgent 在成功发言后调用)

        与 ``update()``(弹幕)并列:``update`` 更新 ``_last_message_ms``,
        ``record_speech`` 更新 ``_last_speech_ms``,两者互不影响。
        冷场判定仍只看弹幕(``_last_message_ms``),主动发言不重置冷场计时器。

        Args:
            now_ms: 发言时刻(Unix 毫秒)。None 时使用真实时钟。
        """
        ts = self._resolve_now(now_ms)
        self._last_speech_ms = ts

    @property
    def last_speech_ms(self) -> Optional[int]:
        """最近一次主动发言时刻(Unix 毫秒),未发言时为 None

        注意:与 ``_last_message_ms``(弹幕时刻)并列,二者独立。
        ProactiveTrigger 用此字段实现 ``min_interval_ms`` 频率限制,
        避免主播刚刚说完话立刻又被触发。
        """
        return self._last_speech_ms

    @property
    def last_message_ms(self) -> Optional[int]:
        """最近一条弹幕到达时刻(Unix 毫秒),尚未收到弹幕时为 None

        供 background.py 判断"上次摘要后是否有新弹幕":
        无新弹幕时摘要内容不会变化,可跳过 LLM 刷新。
        """
        return self._last_message_ms

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> RoomStateSnapshot:
        """生成当前态势快照

        Args:
            now_ms: 快照时刻(Unix 毫秒);None 时使用真实时钟

        Returns:
            RoomStateSnapshot(供决策面只读消费)
        """
        ts = self._resolve_now(now_ms)
        return RoomStateSnapshot(
            heat=self._compute_heat(ts),
            topics=self._extract_topics(ts),
            last_update_ms=ts,
            topic_summary=self._topic_summary,
            topic_summary_at_ms=self._topic_summary_at_ms,
            audience_total=self._audience_total,
            audience_total_at_ms=self._audience_total_at_ms,
        )

    # ------------------------------------------------------------------
    # 累计弹幕计数(供后台记账写入 live_sessions)
    # ------------------------------------------------------------------

    @property
    def total_message_count(self) -> int:
        """自启动以来的累计弹幕条数(全窗口外也累计,不受滑动窗口裁剪影响)。"""
        return self._total_message_count
