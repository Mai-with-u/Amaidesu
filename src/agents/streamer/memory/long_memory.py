"""长期记忆：收播时把这一场按条目合并进主播跨场次的长期笔记（MEMORY.md），开播时读出注入。

场次回顾一场一份，开播只注入最近一两场；五场前答应观众的事、反复出现的老梗、
跨场次推进的长线目标，除非主播逐场去翻，否则就丢了。长期记忆补的就是这一层：
一份分节的纯文本笔记（答应过的事、老梗与口头禅、长线目标、关于我自己，外加
不注入的"不确定"与"搁置"），每行一条、行尾带状态标签（格式见 ``notes``）。

收播合并的顺序：

1. 读笔记（连同版本号）与这一场的材料：回顾、整理原料（与回顾同一份，含备忘录、
   全场取样的发言与话题、观众纠错）、本场观众名单、人设、接入的游戏；
2. 一次 summary 档位调用，模型只给出对编号条目的操作（不重写整份）；
3. 逐条执行操作，再跑一遍生命周期：没人再提的淡出到搁置、超篇幅先搁置最久没被
   提起的（见 ``note_merge``）；
4. 带版本号写回并留底（来由、场次、逐条改动）。写回前文件被人在 WebUI 改过，就在
   新版本上把同一批操作重做一次。

本场没写成回顾（空场次、模型失败）就不动长期记忆；每场只合并一次（合并成功后
在回顾行记合并时刻）。模型输出读不懂时保留旧版本不写，也不记合并时刻。直播进行中
不写：现场要记的结论由本场备忘录承担，收播时再沉淀进来。正式场与彩排场各一份，
按场次行的类型选文件。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Collection, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.long_memory import LongMemoryConflict, LongMemoryStore
from src.modules.types.session_kind import SessionKind

from ..background import SUMMARY_PROFILE
from .note_merge import NoteOp, apply_lifecycle, apply_ops, parse_ops, resolve_ops
from .notes import parse_notes, render_for_injection, render_for_merge, render_notes

__all__ = ["LongMemoryWriter", "format_long_memory"]

logger = get_logger("LongMemoryWriter")

# 系统提示词模板键（正文见 prompts/long_memory_system.md）
_SYSTEM_TEMPLATE = "long_memory_system"
# 本场观众名单的人数上限（弹幕最多的 + 付费过的）
_VIEWER_ROWS = 30
_PAYER_ROWS = 50


def format_long_memory(
    text: str, *, games: Optional[Collection[str]] = None, include_viewer_items: bool = False
) -> str:
    """注入正文：解析笔记后只给注入的四节，去掉标签；``games`` 给了时按开着的游戏筛。

    对某位观众的条目默认不放（那位观众发言时由人物画像段另给），``include_viewer_items``
    打开时一并列出并标明对象。
    """
    return render_for_injection(parse_notes(text or ""), games=games, include_viewer_items=include_viewer_items)


class LongMemoryWriter:
    """长期记忆的整理器：笔记 + 本场材料 → 一次 LLM 给出条目操作 → 逐条执行 → 写回留底。"""

    def __init__(
        self,
        *,
        store: LongMemoryStore,
        sessions_repo: Any,
        recap_repo: Any,
        llm_service: Any,
        prompt_manager: PromptManager,
        chat_repo: Any = None,
        material_provider: Optional[Callable[[int], Awaitable[str]]] = None,
        notebook_text_provider: Optional[Callable[[int], Awaitable[str]]] = None,
        persona_text: str = "",
        games_provider: Optional[Callable[[], Collection[str]]] = None,
        max_chars: int = 2000,
        stale_sessions: int = 3,
    ) -> None:
        """``material_provider`` 按场次主键返回整理原料（与回顾同一份）；缺省时只用回顾与备忘录。

        ``notebook_text_provider`` 只在没有整理原料时补上备忘录；``games_provider`` 返回当前
        接入的游戏名（新增条目只认这些游戏）；``chat_repo`` 用来列本场观众名单。
        """
        self._store = store
        self._sessions = sessions_repo
        self._recaps = recap_repo
        self._chat = chat_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._material_provider = material_provider
        self._notebook_text_provider = notebook_text_provider
        self._persona_text = persona_text.strip()
        self._games_provider = games_provider
        self._max_chars = max(300, int(max_chars))
        self._stale_sessions = max(1, int(stale_sessions))
        self._system_prompt: str = ""

    async def read(self, kind: SessionKind) -> str:
        """读出某类场次的长期笔记全文（开播注入用，注入前再按需渲染）；读失败按没有处理。"""
        try:
            return await self._store.read(kind)
        except Exception as exc:  # noqa: BLE001 读不到只省掉这一段，不阻断开播
            logger.warning(f"读取长期记忆失败（{kind}）: {exc}", exc=True)
            return ""

    async def consolidate(self, live_session_id: int) -> Optional[str]:
        """把一场已写好回顾的直播合并进同类型的长期笔记，返回新全文；没有可合并的或失败时返回 None。"""
        sid = int(live_session_id)
        session_row = await self._sessions.get_live_session(live_session_id=sid)
        if session_row is None:
            logger.debug(f"场次 {sid} 不存在，跳过长期记忆整理")
            return None
        recap = await self._recaps.get_recap(live_session_id=sid)
        if recap is None or not recap.recap_text.strip():
            logger.info(f"场次 {sid} 没有回顾，长期记忆不动")
            return None
        if recap.long_memory_merged_at_ms is not None:
            logger.info(f"场次 {sid} 已合并进长期记忆，不重复合并")
            return None
        kind: SessionKind = session_row.kind
        old_text, version = await self._store.read_versioned(kind)
        viewers = await self._session_viewers(sid)
        games = self._games()

        listing, numbered = render_for_merge(parse_notes(old_text))
        prompt = self._material(listing, recap.recap_text, await self._session_material(sid), viewers, games, sid)
        raw_ops = await self._generate_ops(prompt, sid)
        if raw_ops is None:
            return None
        ops, rejected = resolve_ops(raw_ops, numbered)
        settled_ids = await self._sessions.list_recapped_session_ids(kind=kind)

        text: Optional[str] = None
        for attempt in range(2):
            new_text, changes = self._merge(old_text, ops, rejected, sid, viewers, games, settled_ids)
            if new_text == old_text:
                text = old_text
                logger.info(f"长期记忆无变化（场次 {sid}）")
                break
            try:
                text = await self._store.write(
                    kind, new_text, expected_version=version, reason=f"收播整理（场次 #{sid}）",
                    changes=changes, session_id=sid,
                )  # fmt: skip
                break
            except LongMemoryConflict:
                if attempt:
                    logger.warning(f"长期记忆连续两次在合并期间被改过（场次 {sid}），这场先不合并")
                    return None
                logger.info(f"长期记忆在合并期间被人改过（场次 {sid}），在新版本上重做同一批操作")
                old_text, version = await self._store.read_versioned(kind)
        if text is None:
            return None
        logger.info(f"长期记忆已合并场次 {sid}（{kind}，{len(ops)} 个操作）")
        # 文件写完紧接着记合并时刻。停止时的取消若恰好落在这两步之间（文件已替换、
        # 标记未落库），下次启动会把这场再并一次：新增与已有相同按续上处理，重复代价小
        await self._recaps.mark_long_memory_merged(live_session_id=sid)
        return text

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _merge(
        self,
        old_text: str,
        ops: List[NoteOp],
        rejected: List[str],
        sid: int,
        viewers: Dict[int, str],
        games: List[str],
        settled_ids: List[int],
    ) -> tuple[str, List[str]]:
        """在一份笔记全文上执行同一批操作与生命周期，返回新全文与改动说明。"""
        doc = parse_notes(old_text)
        changes = rejected + apply_ops(doc, ops, current=sid, viewers=viewers, games=games)
        changes += apply_lifecycle(
            doc, current=sid, settled_ids=settled_ids, stale_sessions=self._stale_sessions, max_chars=self._max_chars
        )
        return render_notes(doc), changes

    def _games(self) -> List[str]:
        if self._games_provider is None:
            return []
        try:
            return sorted(str(name) for name in self._games_provider())
        except Exception as exc:  # noqa: BLE001 拿不到游戏名单只少一条约束
            logger.warning(f"读取接入的游戏失败，长期记忆整理按没有游戏处理: {exc}", exc=True)
            return []

    async def _session_viewers(self, sid: int) -> Dict[int, str]:
        """本场观众名单（观众编号 → 昵称）：弹幕最多的一批 + 付费过的。"""
        if self._chat is None:
            return {}
        viewers: Dict[int, str] = {}
        try:
            for speaker in await self._chat.list_session_speakers(live_session_id=sid, limit=_VIEWER_ROWS):
                viewers[int(speaker.viewer_id)] = speaker.user_name or f"观众 #{speaker.viewer_id}"
            for loader in (
                self._chat.list_session_gifts,
                self._chat.list_session_super_chats,
                self._chat.list_session_guards,
            ):
                for row in await loader(live_session_id=sid, limit=_PAYER_ROWS):
                    if row.viewer_id is not None:
                        viewers.setdefault(int(row.viewer_id), row.user_name or f"观众 #{row.viewer_id}")
        except Exception as exc:  # noqa: BLE001 名单读不全只让约定少挂对象
            logger.warning(f"读取本场观众名单失败（场次 {sid}），约定先不挂对象: {exc}", exc=True)
        return viewers

    async def _session_material(self, sid: int) -> str:
        if self._material_provider is not None:
            try:
                return (await self._material_provider(sid) or "").strip()
            except Exception as exc:  # noqa: BLE001 原料读不到退回只用回顾
                logger.warning(f"读取整理原料失败（场次 {sid}），长期记忆只看回顾: {exc}", exc=True)
                return ""
        if self._notebook_text_provider is None:
            return ""
        try:
            notebook = (await self._notebook_text_provider(sid) or "").strip()
        except Exception as exc:  # noqa: BLE001 备忘录读不到只少一类原料
            logger.warning(f"读取备忘录失败，长期记忆整理不带备忘录: {exc}", exc=True)
            return ""
        return f"【主播备忘录（本场结束时的工作笔记全文）】\n{notebook}" if notebook else ""

    def _material(
        self, listing: str, recap_text: str, material: str, viewers: Dict[int, str], games: List[str], sid: int
    ) -> str:
        viewer_text = "；".join(f"#{viewer_id} {name}" for viewer_id, name in viewers.items()) or "（没有）"
        sections = [
            "【现在的笔记】\n" + (listing or "（还是空的）"),
            "【主播的人设】\n" + (self._persona_text or "（没有配置）"),
            "【接入的游戏】\n" + ("、".join(games) if games else "（没有接入游戏）"),
            "【这一场的观众名单】\n" + viewer_text,
            "【这一场的回顾】\n" + recap_text.strip(),
        ]
        if material:
            sections.append("【这一场的整理原料】\n" + material)
        return "\n\n".join(sections) + f"\n\n这一场是 #{sid}。请输出操作。"

    async def _generate_ops(self, prompt: str, sid: int) -> Optional[List[Dict[str, Any]]]:
        try:
            response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=self._get_system_prompt())
        except Exception as exc:  # noqa: BLE001 整理失败只降级，长期记忆保持旧版本
            logger.warning(f"长期记忆整理 LLM 调用异常（场次 {sid}）: {exc}", exc=True)
            return None
        if not (response.ok and response.content):
            logger.warning(f"长期记忆整理 LLM 返回失败（场次 {sid}）")
            return None
        ops = parse_ops(str(response.content))
        if ops is None:
            logger.warning(f"长期记忆整理的输出读不出操作（场次 {sid}），保留旧版本: {str(response.content)[:120]}")
        return ops

    def _get_system_prompt(self) -> str:
        """渲染系统提示词（结果缓存复用）。"""
        if not self._system_prompt:
            self._system_prompt = self._prompt_manager.render(_SYSTEM_TEMPLATE)
        return self._system_prompt
