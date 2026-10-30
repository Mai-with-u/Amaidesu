"""观众画像：收播时为本场来过的常客轮流更新一句印象。

直播是一对多广播，绝大多数弹幕是"666""来了"，真正能让主播认出一个人的
信号集中在少数常客身上，而且大半是结构化的（付费、牌子、来了多少场）。
所以画像不做逐条弹幕解析，而是在收播时批量整理：

1. 候选 = 本场发过弹幕的观众（按条数取前一批）加上本场付过费的观众，且
   ``viewers`` 累计互动量达门槛——不够格的不建档，自然也不会被注入；上次整理
   之后这场没说上几句、也没付费的跳过；
2. 排序轮换：还没建档的先来，其次是印象最久没更新的——每场只整理
   ``batch_max`` 人，若总按弹幕数排，永远是那几位话最多的，排在后面的常客和
   只送礼不说话的观众一直建不了档；
3. 每位候选的原料 = 旧印象 + 该观众本场在上次整理之后说过的话（原话）
   + 付费与身份记录（累计付费、最近明细、付费时的牌子/舰队快照，直接查表取得）；
   一次 LLM 调用融合成新印象，写回 ``viewer_profiles``，并把已整理到的时间点推进到当下。

失败逐人降级（该人印象不动、已整理时刻不推，下场再来）。

画像不跨场次类型更新：正式场只更新真实观众，彩排场只更新模拟观众。开发者
在彩排里接真实房间、或用控制台调试，都不会改写真实观众的印象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.utils.time_utils import now_ms
from src.modules.types.currency import to_cny
from src.modules.types.guard_levels import GUARD_LEVEL_NAMES
from src.modules.types.session_kind import SESSION_KIND_REHEARSAL

from ..background import SUMMARY_PROFILE
from .recap import clip_whole_sentences

__all__ = ["RegularsRosterWriter", "collect_payment_material"]

logger = get_logger("RegularsRosterWriter")

# 印象系统提示词模板键（正文见 prompts/profile_system.md）
_PROFILE_SYSTEM_TEMPLATE = "profile_system"

# 候选与原料上限：扫描的发言人数、每类付费明细行数、上次整理后至少说了几句才值得更新、
# 每人本场原话条数、单条长度、付费明细条数
_SPEAKER_SCAN_ROWS = 50
_PAYER_SCAN_ROWS = 200
_MIN_NEW_MESSAGES = 2
_MESSAGE_ROWS = 30
_MESSAGE_LINE_CHARS = 80
_PAYMENT_MATERIAL_ROWS = 3


async def collect_payment_material(chat_repo: Any, viewer_id: int, *, rows: int = _PAYMENT_MATERIAL_ROWS) -> List[str]:
    """直接查付费明细三表，产出印象原料行（汇总 / 最近明细 / 付费时身份快照）。

    全部异常降级为缺行——结构化原料缺失只让印象少一分厚重，不阻断生成。
    金额展示经币种换算常量（金瓜子 → 元）。
    """
    if chat_repo is None:
        return []
    lines: List[str] = []
    try:
        summary = await chat_repo.summarize_viewer_contributions(viewer_id=viewer_id)
        gold = int(summary.get("gift_total_amount", 0)) + int(summary.get("sc_total_amount", 0))
        yuan = to_cny(gold)
        if yuan:
            lines.append(f"- 累计付费约 {yuan:.0f} 元（礼物与 SC 标价合计）")
    except Exception as exc:
        logger.debug(f"付费汇总读取失败（印象原料缺该项）: {exc}")

    # 三表明细合并取最近 N 条（时间倒序），并从最近一条取付费时的身份快照
    entries: List[tuple[int, str, int, int, str]] = []  # (ts, 描述, fans_medal_level, guard_level, medal_name)
    try:
        for gift in await chat_repo.list_viewer_gifts(viewer_id=viewer_id, limit=rows):
            entries.append(
                (
                    gift.timestamp_ms,
                    f"送出礼物 {gift.gift_name}×{gift.quantity}",
                    gift.fans_medal_level or 0,
                    gift.guard_level or 0,
                    gift.fans_medal_name or "",
                )
            )
        for sc in await chat_repo.list_viewer_super_chats(viewer_id=viewer_id, limit=rows):
            entries.append(
                (
                    sc.timestamp_ms,
                    f"发送 SC「{(sc.message or '')[:30]}」",
                    sc.fans_medal_level or 0,
                    sc.guard_level or 0,
                    sc.fans_medal_name or "",
                )
            )
        for guard in await chat_repo.list_viewer_guards(viewer_id=viewer_id, limit=rows):
            guard_name = GUARD_LEVEL_NAMES.get(guard.guard_level or 0, "大航海")
            entries.append(
                (
                    guard.timestamp_ms,
                    f"开通{guard_name}（{guard.guard_num}{guard.guard_unit}）",
                    guard.fans_medal_level or 0,
                    guard.guard_level or 0,
                    guard.fans_medal_name or "",
                )
            )
    except Exception as exc:
        logger.debug(f"付费明细读取失败（印象原料缺明细行）: {exc}")
        return lines

    entries.sort(key=lambda item: item[0], reverse=True)
    for entry in entries[:rows]:
        lines.append(f"- 最近付费：{entry[1]}")
    if entries:
        latest = entries[0]
        identity_parts: List[str] = []
        if latest[3]:
            identity_parts.append(GUARD_LEVEL_NAMES.get(latest[3], "舰队成员"))
        if latest[2] or latest[4]:
            medal = f"{latest[4]}{latest[2]} 级牌" if latest[4] else f"{latest[2]} 级牌"
            identity_parts.append(medal)
        if identity_parts:
            lines.append(f"- 最近一次付费时的身份：{'、'.join(identity_parts)}")
    return lines


@dataclass
class _Candidate:
    """一位可更新画像的观众：旧印象、上次整理之后的本场原话、上次整理之后这场有没有付费。"""

    viewer_id: int
    nickname: str
    old_text: str
    watermark: int
    messages: List[Any]
    paid: bool


class RegularsRosterWriter:
    """观众画像的收播更新器：挑候选 → 逐人取原料 → LLM 融合 → 写回印象。"""

    def __init__(
        self,
        *,
        sessions_repo: Any,
        chat_repo: Any,
        viewer_repo: Any,
        profile_repo: Any,
        llm_service: Any,
        prompt_manager: PromptManager,
        min_interactions: int = 3,
        batch_max: int = 5,
        profile_max_length: int = 400,
    ) -> None:
        """``sessions_repo`` 用来读场次类型（以场次行为准，人手更正后立即生效）。"""
        self._sessions = sessions_repo
        self._chat = chat_repo
        self._viewers = viewer_repo
        self._profiles = profile_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._min_interactions = max(1, int(min_interactions))
        self._batch_max = max(1, int(batch_max))
        self._profile_max_length = max(100, int(profile_max_length))
        self._system_prompt: str = ""

    async def update_for_session(self, live_session_id: int) -> int:
        """为本场来过、达门槛的常客轮流更新印象，返回更新人数。

        候选 = 本场发言人（按弹幕数取前一批）+ 本场付过费的；按"没建档的先来、其次印象最久
        没更新的"排序，取 ``batch_max`` 人。场次行不存在（已删除）时不更新；彩排场只看
        模拟观众，正式场只看真实观众。
        """
        sid = int(live_session_id)
        try:
            session_row = await self._sessions.get_live_session(live_session_id=sid)
        except Exception as exc:
            logger.warning(f"读取场次类型失败（场次 {sid}），不更新画像: {exc}")
            return 0
        if session_row is None:
            logger.debug(f"场次 {sid} 不存在，跳过画像更新")
            return 0
        want_simulated = session_row.kind == SESSION_KIND_REHEARSAL
        try:
            names, payments = await self._session_people(sid)
        except Exception as exc:
            logger.warning(f"读取本场发言人与付费观众失败（场次 {sid}）: {exc}")
            return 0
        candidates: List[_Candidate] = []
        for viewer_id, nickname in names.items():
            if not await self._meets_threshold(viewer_id, want_simulated=want_simulated):
                continue
            try:
                candidate = await self._candidate(sid, viewer_id, nickname, payments.get(viewer_id))
            except Exception as exc:
                logger.warning(f"读取常客原料失败（viewer {viewer_id}，跳过该人）: {exc}")
                continue
            if candidate is not None:
                candidates.append(candidate)
        # 没建档的先来（已整理时刻为 0），其次印象最久没更新的；同样旧的话多的先来
        candidates.sort(key=lambda item: (item.watermark, -len(item.messages)))
        updated = 0
        for candidate in candidates[: self._batch_max]:
            try:
                if await self._update_one(candidate):
                    updated += 1
            except Exception as exc:
                logger.warning(f"常客印象更新失败（viewer {candidate.viewer_id}，跳过该人）: {exc}")
        if updated:
            logger.info(f"观众画像已更新 {updated} 人（场次 {sid}，候选 {len(candidates)} 人）")
        return updated

    async def _session_people(self, sid: int) -> Tuple[Dict[int, str], Dict[int, int]]:
        """本场发言人与付费观众（观众编号 → 昵称），以及每位付费观众本场最后一次付费的时刻。"""
        names: Dict[int, str] = {}
        for speaker in await self._chat.list_session_speakers(live_session_id=sid, limit=_SPEAKER_SCAN_ROWS):
            names[int(speaker.viewer_id)] = speaker.user_name or ""
        payments: Dict[int, int] = {}
        for loader in (
            self._chat.list_session_gifts,
            self._chat.list_session_super_chats,
            self._chat.list_session_guards,
        ):
            for row in await loader(live_session_id=sid, limit=_PAYER_SCAN_ROWS):
                if row.viewer_id is None:
                    continue
                viewer_id = int(row.viewer_id)
                names.setdefault(viewer_id, row.user_name or "")
                payments[viewer_id] = max(payments.get(viewer_id, 0), int(row.timestamp_ms or 0))
        return names, payments

    async def _candidate(
        self, sid: int, viewer_id: int, nickname: str, last_paid_ms: Optional[int]
    ) -> Optional[_Candidate]:
        """上次整理之后这场说了够多的话、或付过费，才算本场候选；否则返回 None（不空转）。"""
        existing = await self._profiles.get_profile(viewer_id=viewer_id)
        watermark = existing.last_compressed_at_ms if existing is not None else 0
        messages = await self._chat.list_session_viewer_messages(
            live_session_id=sid, viewer_id=viewer_id, since_ms=watermark, limit=_MESSAGE_ROWS
        )
        paid = last_paid_ms is not None and last_paid_ms > watermark
        if len(messages) < _MIN_NEW_MESSAGES and not paid:
            return None
        return _Candidate(
            viewer_id=viewer_id,
            nickname=nickname,
            old_text=existing.profile_text if existing is not None else "",
            watermark=watermark,
            messages=list(messages),
            paid=paid,
        )

    async def _meets_threshold(self, viewer_id: int, *, want_simulated: bool) -> bool:
        """``viewers`` 累计互动量达门槛、且真假与场次类型相符才建档；统计行缺失视为不达标。"""
        try:
            stats = await self._viewers.get_viewer(viewer_id=viewer_id)
        except Exception as exc:
            logger.warning(f"读取观众统计失败（viewer {viewer_id}）: {exc}")
            return False
        if stats is None or bool(stats.simulated) != want_simulated:
            return False
        return (stats.interaction_count or 0) >= self._min_interactions

    async def _update_one(self, candidate: _Candidate) -> bool:
        """单人更新：旧印象 + 上次整理之后的本场原话 + 付费记录 → LLM → 写回并推进已整理时刻。"""
        viewer_id = candidate.viewer_id
        prompt_parts = [
            f"观众：{candidate.nickname or f'观众 #{viewer_id}'}",
            f"【旧印象】\n{candidate.old_text}" if candidate.old_text else "【旧印象】（无，首次建档）",
            "【这场说过的话】",
            *[f"- {(message.content or '').strip()[:_MESSAGE_LINE_CHARS]}" for message in candidate.messages],
        ]
        if not candidate.messages:
            prompt_parts.append("（这场没说话，只有付费）")
        structured_lines = await collect_payment_material(self._chat, viewer_id)
        if structured_lines:
            prompt_parts.append("【付费与身份】")
            prompt_parts.extend(structured_lines)
        prompt = "\n".join(prompt_parts)

        response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=self._get_system_prompt())
        if not (response.ok and response.content):
            logger.warning(f"常客印象 LLM 返回失败（viewer {viewer_id}）")
            return False
        profile_text = clip_whole_sentences(str(response.content), self._profile_max_length)
        if not profile_text:
            return False
        await self._profiles.upsert_profile(
            viewer_id=viewer_id, profile_text=profile_text, last_compressed_at_ms=now_ms()
        )
        logger.debug(f"常客印象已更新（viewer {viewer_id}）: {profile_text[:50]}")
        return True

    def _get_system_prompt(self) -> str:
        """渲染印象系统提示词（含长度变量，结果缓存复用）。"""
        if not self._system_prompt:
            self._system_prompt = self._prompt_manager.render(
                _PROFILE_SYSTEM_TEMPLATE, profile_max_length=self._profile_max_length
            )
        return self._system_prompt
