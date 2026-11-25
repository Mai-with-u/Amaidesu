"""游戏社交事实的数据契约（``game_social_facts`` 表）。

一行 = 一条"当前游戏环境里人与人怎么相处"的可核对事实，与承诺表同构：
行级状态、证据可核、人工锁。

字段说明
--------
``SocialFact`` —— 库里的一行：六类事实之一（环境形态 / 成员关系 / 共享惯例 /
进行中的事 / 她的角色 / 待弄清），带原文依据与证据行引用。

``NewSocialFact`` —— 待写入的一行：仓储写面与结算写面共用。
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DISPLAY_FACTS_PER_KIND",
    "EvidenceHit",
    "NewSocialFact",
    "SocialFact",
    "SocialFactKind",
    "SOCIAL_FACT_KINDS",
    "SOCIAL_FACT_STATUSES",
    "MAX_ACTIVE_FACTS_PER_KIND",
    "QUOTE_OPTIONAL_KINDS",
    "is_social_fact_resident",
    "is_works_downgraded",
    "select_social_facts_for_display",
]

# 六类事实（英文小写枚举）：环境形态 / 成员关系 / 共享惯例 / 进行中的事 /
# 她的角色 / 待弄清
SocialFactKind = str

SOCIAL_FACT_KINDS: tuple[str, ...] = (
    "setting",
    "members",
    "sharing",
    "works",
    "her_role",
    "open_question",
)

# 行状态：active 现行（仍在当前存档有效）/ superseded 被取代（现实中已变更或失效，留档备查）
SOCIAL_FACT_STATUSES: tuple[str, ...] = ("active", "superseded")

# 空引文行只允许这两类（归纳类）：其余四类必须有可核对的原文依据
QUOTE_OPTIONAL_KINDS: tuple[str, ...] = ("her_role", "open_question")

# 展示层单类默认展示配额（读取侧 Top-K 切片用，不作为数据库 active 存储上限；
# 数据库完整保留全部仍有效的 active 事实，绝不因展示预算伪造 superseded）
DISPLAY_FACTS_PER_KIND = 4
# 兼容导出别名：供既有调用方与测试引用，语义等同 DISPLAY_FACTS_PER_KIND
MAX_ACTIVE_FACTS_PER_KIND = DISPLAY_FACTS_PER_KIND


class SocialFact(BaseModel):
    """``game_social_facts`` 一行：一条可核对的游戏社交事实。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行号")
    game: str = Field(..., description="游戏标识")
    save_id: str = Field(default="", description="存档编号；空串 = 该游戏不分环境，整游戏一本")
    save_name: str = Field(default="", description="存档显示名")
    kind: str = Field(..., description="六类之一")
    text: str = Field(..., description="一句事实陈述")
    quote: str = Field(default="", description="依据的原文片段；空串只允许归纳类两行")
    evidence_chat_id: Optional[int] = Field(default=None, description="弹幕出处（→ live_chat）")
    evidence_game_chat_id: Optional[int] = Field(default=None, description="游戏公屏出处（→ game_chat）")
    status: str = Field(default="active", description="active / superseded")
    superseded_by: Optional[int] = Field(default=None, description="取代本行的行号")
    human_locked: int = Field(default=0, description="运营者改过即锁：整理与工具都不得改或取代")
    created_session_id: Optional[int] = Field(default=None, description="登记场次")
    updated_session_id: Optional[int] = Field(default=None, description="最近更新场次")
    created_at_ms: int = Field(..., description="写入时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最近更新时刻（Unix epoch 毫秒）")
    stale_unconfirmed: bool = Field(
        default=False,
        description="运行时标记：进行中的事已隔两场完结正式场未再被证实，渲染降级且满额时优先让位",
    )


def is_social_fact_resident(fact: SocialFact) -> bool:
    """判定一条社交事实能否进入每轮常驻参考段【游戏社交事实】。

    运营者锁定的行始终常驻；非人工锁定的行若没有逐字原话依据（模型归纳行），或仅挂在观众弹幕上
    未经人工批准，只保留在库里供按需回忆检索，不每轮常驻注入。
    """
    if fact.status != "active":
        return False
    if fact.human_locked:
        return True
    if fact.evidence_chat_id is not None and fact.evidence_game_chat_id is None:
        return False
    if not (fact.quote or "").strip():
        return False
    return True


def is_works_downgraded(
    fact: SocialFact,
    *,
    current_session_id: Optional[int] = None,
    recent_two_session_ids: Sequence[int] = (),
    second_recent_started_ms: int = 0,
) -> bool:
    """判定一条"进行中的事"（works）是否已过两场完结正式场未再证实而应降级。"""
    if fact.stale_unconfirmed:
        return True
    if fact.kind != "works" or fact.human_locked:
        return False
    if len(recent_two_session_ids) < 2:
        return False
    touch_sid = fact.updated_session_id if fact.updated_session_id is not None else fact.created_session_id
    if current_session_id is not None and touch_sid is not None and int(touch_sid) == int(current_session_id):
        return False
    recent_set = {int(sid) for sid in recent_two_session_ids[:2]}
    if touch_sid is not None and int(touch_sid) in recent_set:
        return False
    if second_recent_started_ms > 0 and int(fact.updated_at_ms) >= int(second_recent_started_ms):
        return False
    if touch_sid is not None and second_recent_started_ms <= 0 and int(touch_sid) >= min(recent_set):
        return False
    return True


def select_social_facts_for_display(
    facts: Iterable[SocialFact],
    *,
    per_kind_limit: int = DISPLAY_FACTS_PER_KIND,
    total_limit: Optional[int] = None,
    kind_order: Sequence[str] = SOCIAL_FACT_KINDS,
    downgraded_fn: Optional[Callable[[SocialFact], bool]] = None,
    fill_remaining: bool = True,
) -> List[SocialFact]:
    """读取侧展示预算切片：从完整 active 事实中按分层轮转做公平 Top-K。

    触发条件：开播参考段渲染或下播结算材料拼装需要控制提示词体积时调用，绝不改动数据库行状态。
    执行顺序：筛 active -> 人工锁定行优先占位 -> 未降级条目按类别顺序逐轮每类各取 1 条（单类不超过
    per_kind_limit），避免靠前类别耗尽总配额而饿死后排类别 -> 若开启 fill_remaining 且总预算仍有空位，
    按全局优先级回填未降级溢出条目 -> 仍有空位时再按同样轮转与回填规则补已降级条目 -> 输出按统一展示序排列。
    """
    active = [fact for fact in facts if fact.status == "active"]
    if not active:
        return []
    rank = {kind: idx for idx, kind in enumerate(kind_order)}
    cap_per_kind = max(1, int(per_kind_limit))
    cap_total = max(1, int(total_limit)) if total_limit is not None else len(active)

    def _is_downgraded(item: SocialFact) -> bool:
        return bool(downgraded_fn(item)) if downgraded_fn is not None else bool(item.stale_unconfirmed)

    def _sort_key(item: SocialFact) -> Tuple[int, int, int, int, int]:
        return (
            0 if item.human_locked else 1,
            1 if _is_downgraded(item) else 0,
            rank.get(item.kind, len(rank)),
            -int(item.updated_at_ms),
            -int(item.id),
        )

    # 非锁定条目按“新鲜未降级 / 已降级”分层入桶，桶内按最新在前排序
    fresh_by_kind: dict[str, List[SocialFact]] = {}
    downgraded_by_kind: dict[str, List[SocialFact]] = {}
    for item in active:
        if item.human_locked:
            continue
        target = downgraded_by_kind if _is_downgraded(item) else fresh_by_kind
        target.setdefault(item.kind, []).append(item)
    for buckets in (fresh_by_kind, downgraded_by_kind):
        for bucket in buckets.values():
            bucket.sort(key=lambda f: (-int(f.updated_at_ms), -int(f.id)))

    ordered_kinds = list(dict.fromkeys([*kind_order, *(item.kind for item in active)]))
    chosen_ids: set[int] = set()
    chosen: List[SocialFact] = []
    kind_counts: dict[str, int] = {}

    def _take(item: SocialFact) -> None:
        chosen.append(item)
        chosen_ids.add(int(item.id))
        kind_counts[item.kind] = kind_counts.get(item.kind, 0) + 1

    def _round_robin(buckets: dict[str, List[SocialFact]]) -> None:
        # 逐轮每类至多取 1 条并受单类上限约束，确保后排类别（如 sharing/setting）先分得保底展示名额
        cursors = {kind: 0 for kind in ordered_kinds}
        while len(chosen) < cap_total:
            progressed = False
            for kind in ordered_kinds:
                if len(chosen) >= cap_total:
                    break
                if kind_counts.get(kind, 0) >= cap_per_kind:
                    continue
                bucket = buckets.get(kind, ())
                cursor = cursors[kind]
                while cursor < len(bucket) and int(bucket[cursor].id) in chosen_ids:
                    cursor += 1
                if cursor < len(bucket):
                    _take(bucket[cursor])
                    cursors[kind] = cursor + 1
                    progressed = True
            if not progressed:
                break

    # 人工锁定行优先占位：锁定事实永不被普通条目挤出，同时计入所属类别占用
    locked_all = sorted((item for item in active if item.human_locked), key=_sort_key)
    for item in locked_all:
        if len(chosen) >= cap_total:
            break
        _take(item)

    # 未降级条目先跨类轮转保底，未满且允许回填时再按优先级回填未降级溢出条目
    _round_robin(fresh_by_kind)
    if fill_remaining and len(chosen) < cap_total:
        fresh_leftover = sorted(
            (item for item in active if int(item.id) not in chosen_ids and not _is_downgraded(item)),
            key=_sort_key,
        )
        for item in fresh_leftover:
            if len(chosen) >= cap_total:
                break
            _take(item)

    # 仅在未降级条目全部装完且总预算仍有空位时，才补入已降级旧进展
    if len(chosen) < cap_total:
        _round_robin(downgraded_by_kind)
    if fill_remaining and len(chosen) < cap_total:
        downgraded_leftover = sorted((item for item in active if int(item.id) not in chosen_ids), key=_sort_key)
        for item in downgraded_leftover:
            if len(chosen) >= cap_total:
                break
            _take(item)

    chosen.sort(key=_sort_key)
    return chosen


class EvidenceHit(BaseModel):
    """按"本场 + 消息号"查到的一条依据行：弹幕观众行（chat）或游戏公屏非她本人的行（game_chat）。"""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="chat = 弹幕观众行（live_chat）/ game_chat = 游戏公屏玩家行或占位行")
    row_id: int = Field(..., description="命中行的行号")
    content: str = Field(default="", description="命中行原文；游戏公屏占位行为空串（没有存原话）")
    game: str = Field(default="", description="游戏公屏行所在游戏；弹幕为空串")
    save_id: str = Field(default="", description="游戏公屏行所在存档；弹幕为空串")
    save_name: str = Field(default="", description="游戏公屏行所在存档显示名；弹幕为空串")


class NewSocialFact(BaseModel):
    """待写入的一行游戏社交事实（仓储写面与结算写面共用）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., description="六类之一")
    text: str = Field(..., description="一句事实陈述")
    quote: str = Field(default="", description="依据的原文片段")
    game: str = Field(default="", description="游戏标识")
    save_id: str = Field(default="", description="存档编号；空串 = 整游戏一本")
    save_name: str = Field(default="", description="存档显示名")
    evidence_chat_id: Optional[int] = Field(default=None, description="弹幕出处（→ live_chat）")
    evidence_game_chat_id: Optional[int] = Field(default=None, description="游戏公屏出处（→ game_chat）")
    supersedes_id: Optional[int] = Field(default=None, description="被本行取代的行号")
    human_locked: int = Field(default=0, description="运营者锁：只由运营者写入路径置位")
    created_session_id: Optional[int] = Field(default=None, description="登记场次")
    updated_session_id: Optional[int] = Field(default=None, description="最近更新场次")
