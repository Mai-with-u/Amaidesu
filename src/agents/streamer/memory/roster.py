"""观众画像：收播时为本场最活跃的几位常客更新一句印象。

直播是一对多广播，绝大多数弹幕是"666""来了"，真正能让主播认出一个人的
信号集中在少数常客身上，而且大半是结构化的（付费、牌子、来了多少场）。
所以画像不再靠每 60 秒从弹幕里抠"事实"，而是在收播时：

1. 候选 = 本场弹幕最多的观众（``live_chat`` 按人聚合），且 ``viewers`` 累计
   互动量达门槛——不够格的不建档，自然也不会被注入；
2. 每位候选的原料 = 旧印象 + 该观众本场在上次整理之后说过的话（原话）
   + 付费与身份记录（累计付费、最近明细、付费时的牌子/舰队快照，直接查表取得）；
3. 一次 LLM 调用融合成新印象，写回 ``viewer_profiles``，并把已整理到的
   时间点推进到当下。

单场最多更新 ``batch_max`` 人，失败逐人降级（该人印象不动、已整理时刻不推，下场再来）。
"""

from __future__ import annotations

from typing import Any, List

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.utils.time_utils import now_ms
from src.modules.types.currency import to_cny
from src.modules.types.guard_levels import GUARD_LEVEL_NAMES

from ..background import SUMMARY_PROFILE

__all__ = ["RegularsRosterWriter", "collect_payment_material"]

logger = get_logger("RegularsRosterWriter")

# 印象系统提示词模板键（正文见 prompts/profile_system.md）
_PROFILE_SYSTEM_TEMPLATE = "profile_system"

# 原料上限：候选扫描倍数、每人本场原话条数、单条长度、付费明细条数
_CANDIDATE_SCAN_FACTOR = 3
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
        for row in await chat_repo.list_viewer_gifts(viewer_id=viewer_id, limit=rows):
            entries.append(
                (
                    int(row["timestamp_ms"]),
                    f"送出礼物 {row['gift_name']}×{row['quantity']}",
                    int(row["fans_medal_level"] or 0),
                    int(row["guard_level"] or 0),
                    str(row["fans_medal_name"] or ""),
                )
            )
        for row in await chat_repo.list_viewer_super_chats(viewer_id=viewer_id, limit=rows):
            entries.append(
                (
                    int(row["timestamp_ms"]),
                    f"发送 SC「{(row['message'] or '')[:30]}」",
                    int(row["fans_medal_level"] or 0),
                    int(row["guard_level"] or 0),
                    str(row["fans_medal_name"] or ""),
                )
            )
        for row in await chat_repo.list_viewer_guards(viewer_id=viewer_id, limit=rows):
            guard_name = GUARD_LEVEL_NAMES.get(int(row["guard_level"] or 0), "大航海")
            entries.append(
                (
                    int(row["timestamp_ms"]),
                    f"开通{guard_name}（{row['guard_num']}{row['guard_unit']}）",
                    int(row["fans_medal_level"] or 0),
                    int(row["guard_level"] or 0),
                    str(row["fans_medal_name"] or ""),
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


class RegularsRosterWriter:
    """观众画像的收播更新器：挑候选 → 逐人取原料 → LLM 融合 → 写回印象。"""

    def __init__(
        self,
        *,
        chat_repo: Any,
        viewer_repo: Any,
        profile_repo: Any,
        llm_service: Any,
        prompt_manager: PromptManager,
        min_interactions: int = 3,
        batch_max: int = 5,
        profile_max_length: int = 400,
    ) -> None:
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
        """为本场达门槛的最活跃常客更新印象，返回更新人数。

        候选按本场弹幕数倒序扫描，扫描条数是批次上限的几倍——不够格的
        被门槛筛掉后仍能凑满一批；凑满 ``batch_max`` 即停。
        """
        try:
            speakers = await self._chat.list_session_speakers(
                live_session_id=int(live_session_id), limit=self._batch_max * _CANDIDATE_SCAN_FACTOR
            )
        except Exception as exc:
            logger.warning(f"读取本场发言人失败（场次 {live_session_id}）: {exc}")
            return 0
        updated = 0
        for row in speakers:
            if updated >= self._batch_max:
                break
            viewer_id = int(row["viewer_id"])
            if not await self._meets_threshold(viewer_id):
                continue
            try:
                if await self._update_one(int(live_session_id), viewer_id, str(row["user_name"] or "")):
                    updated += 1
            except Exception as exc:
                logger.warning(f"常客印象更新失败（viewer {viewer_id}，跳过该人）: {exc}")
        if updated:
            logger.info(f"观众画像已更新 {updated} 人（场次 {live_session_id}）")
        return updated

    async def _meets_threshold(self, viewer_id: int) -> bool:
        """``viewers`` 累计互动量达门槛才建档；统计行缺失视为不达标。"""
        try:
            stats = await self._viewers.get_viewer(viewer_id=viewer_id)
        except Exception as exc:
            logger.warning(f"读取观众统计失败（viewer {viewer_id}）: {exc}")
            return False
        if stats is None:
            return False
        return int(stats["interaction_count"] or 0) >= self._min_interactions

    async def _update_one(self, live_session_id: int, viewer_id: int, nickname: str) -> bool:
        """单人更新：旧印象 + 上次整理之后的本场原话 + 付费记录 → LLM → 写回并推进已整理时刻。"""
        existing = await self._profiles.get_profile(viewer_id=viewer_id)
        old_text = str(existing["profile_text"]) if existing is not None else ""
        watermark = int(existing["last_compressed_at_ms"]) if existing is not None else 0

        messages = await self._chat.list_session_viewer_messages(
            live_session_id=live_session_id,
            viewer_id=viewer_id,
            since_ms=watermark,
            limit=_MESSAGE_ROWS,
        )
        if not messages:
            return False  # 上次整理之后没新话（同一场重复收尾），不空转

        prompt_parts = [
            f"观众：{nickname or f'观众 #{viewer_id}'}",
            f"【旧印象】\n{old_text}" if old_text else "【旧印象】（无，首次建档）",
            "【这场说过的话】",
            *[f"- {str(row['content'] or '').strip()[:_MESSAGE_LINE_CHARS]}" for row in messages],
        ]
        structured_lines = await collect_payment_material(self._chat, viewer_id)
        if structured_lines:
            prompt_parts.append("【付费与身份】")
            prompt_parts.extend(structured_lines)
        prompt = "\n".join(prompt_parts)

        response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=self._get_system_prompt())
        if not (getattr(response, "success", False) and getattr(response, "content", None)):
            logger.warning(f"常客印象 LLM 返回失败（viewer {viewer_id}）")
            return False
        profile_text = str(response.content).strip()[: self._profile_max_length]
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
