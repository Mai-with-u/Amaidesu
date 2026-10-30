"""长期记忆：收播时把这一场合并进主播跨场次的长期笔记（MEMORY.md），开播时整份注入。

场次回顾一场一份，开播只注入最近一两场；五场前答应观众的事、反复出现的老梗、
跨场次推进的长线目标，除非主播逐场去翻，否则就丢了。长期记忆补的就是这一层：
一份有字数上限的纯文本，由主播"自己"整理（收播后一次 summary 档位调用），按节组织：
答应过的事、老梗与口头禅、长线目标、关于我自己。

时机与原料：

- 收播整理里排在场次回顾之后——原料是旧笔记 + 本场回顾 + 本场备忘录全文快照；
  本场没写成回顾（空场次、模型失败）就不动长期记忆。启动补写的场次同样补一次。
- 每场只合并一次：合并成功后在回顾行记下合并时刻，已记的场次不再合并。整理被
  打断后由启动补写重跑时，同一份回顾不会在长期笔记里并两遍。
- 直播进行中不写：现场要记的结论由本场备忘录承担，收播时再沉淀进来。

上限的处理：模型输出超过上限时让它压缩一次；仍超出就保留旧版本不写——宁可这一场
没合并进去，也不截断成半句话。正式场与彩排场各一份，按场次行的类型选文件。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.long_memory import LongMemoryStore
from src.modules.types.session_kind import SessionKind

from ..background import SUMMARY_PROFILE

__all__ = ["LongMemoryWriter", "format_long_memory"]

logger = get_logger("LongMemoryWriter")

# 系统提示词模板键（正文见 prompts/long_memory_system.md）
_SYSTEM_TEMPLATE = "long_memory_system"


def format_long_memory(text: str) -> str:
    """注入段正文：原样去首尾空白（文件本身就是给人和模型读的成品）。"""
    return (text or "").strip()


class LongMemoryWriter:
    """长期记忆的整理器：旧笔记 + 本场回顾 + 本场备忘录 → 一次 LLM 合并 → 写回文件。"""

    def __init__(
        self,
        *,
        store: LongMemoryStore,
        sessions_repo: Any,
        recap_repo: Any,
        llm_service: Any,
        prompt_manager: PromptManager,
        notebook_text_provider: Optional[Callable[[int], Awaitable[str]]] = None,
        max_chars: int = 2000,
    ) -> None:
        """``notebook_text_provider`` 按场次主键返回这场落盘的备忘录全文（与回顾读同一份）；None 或空串 = 本场没写。"""
        self._store = store
        self._sessions = sessions_repo
        self._recaps = recap_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._notebook_text_provider = notebook_text_provider
        self._max_chars = max(300, int(max_chars))
        self._system_prompt: str = ""

    async def read(self, kind: SessionKind) -> str:
        """读出某类场次的长期记忆（开播注入用）；读失败按没有处理。"""
        try:
            return format_long_memory(await self._store.read(kind))
        except Exception as exc:  # noqa: BLE001 读不到只省掉这一段，不阻断开播
            logger.warning(f"读取长期记忆失败（{kind}）: {exc}", exc=True)
            return ""

    async def consolidate(self, live_session_id: int) -> Optional[str]:
        """把一场已写好回顾的直播合并进同类型的长期记忆，返回新全文；没有可合并的或失败时返回 None。

        流程：读场次行（取类型）→ 读本场回顾（没有或已合并过就不动）→ 读旧笔记与备忘录快照 →
        LLM 合并 → 超上限压缩一次 → 写回 → 记合并时刻。
        """
        session_row = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session_row is None:
            logger.debug(f"场次 {live_session_id} 不存在，跳过长期记忆整理")
            return None
        recap = await self._recaps.get_recap(live_session_id=int(live_session_id))
        if recap is None or not recap.recap_text.strip():
            logger.info(f"场次 {live_session_id} 没有回顾，长期记忆不动")
            return None
        if recap.long_memory_merged_at_ms is not None:
            logger.info(f"场次 {live_session_id} 已合并进长期记忆，不重复合并")
            return None
        kind: SessionKind = session_row.kind
        old_text = await self.read(kind)
        notebook = await self._notebook_text(int(live_session_id)) if self._notebook_text_provider is not None else ""

        prompt = self._material(old_text, recap.recap_text, notebook)
        text = await self._generate(prompt, live_session_id)
        if text is None:
            return None
        if len(text) > self._max_chars:
            text = await self._generate(self._compress_prompt(text), live_session_id)
            if text is None:
                return None
            if len(text) > self._max_chars:
                logger.warning(
                    f"长期记忆压缩后仍超上限（{len(text)} > {self._max_chars} 字，场次 {live_session_id}），保留旧版本"
                )
                return None
        if text == old_text:
            logger.info(f"长期记忆无变化（场次 {live_session_id}）")
        else:
            text = await self._store.write(kind, text)
            logger.info(f"长期记忆已合并场次 {live_session_id}（{kind}，{len(text)} 字）")
        # 文件写完紧接着记合并时刻。停止时的取消若恰好落在这两步之间（文件已替换、
        # 标记未落库），下次启动会把这场再并一次：重复的是同一份回顾，比漏并代价小
        await self._recaps.mark_long_memory_merged(live_session_id=int(live_session_id))
        return text

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _notebook_text(self, live_session_id: int) -> str:
        assert self._notebook_text_provider is not None  # noqa: S101 调用方已 guard
        try:
            return (await self._notebook_text_provider(live_session_id) or "").strip()
        except Exception as exc:  # noqa: BLE001 备忘录读不到只少一类原料
            logger.warning(f"读取备忘录快照失败，长期记忆整理不带备忘录: {exc}", exc=True)
            return ""

    @staticmethod
    def _material(old_text: str, recap_text: str, notebook: str) -> str:
        sections = [
            "【现在的长期笔记】\n" + (old_text or "（还是空的）"),
            "【刚结束这一场的回顾】\n" + recap_text.strip(),
        ]
        if notebook:
            sections.append("【这一场的备忘录】\n" + notebook)
        return "\n\n".join(sections) + "\n\n请输出更新后的整份长期笔记。"

    def _compress_prompt(self, draft: str) -> str:
        return (
            f"下面这份长期笔记有 {len(draft)} 字，超过了 {self._max_chars} 字的上限。"
            f"请在不丢掉未兑现承诺的前提下删减合并，压到 {self._max_chars} 字以内，再输出整份笔记。\n\n{draft}"
        )

    async def _generate(self, prompt: str, live_session_id: int) -> Optional[str]:
        try:
            response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=self._get_system_prompt())
        except Exception as exc:  # noqa: BLE001 整理失败只降级，长期记忆保持旧版本
            logger.warning(f"长期记忆整理 LLM 调用异常（场次 {live_session_id}）: {exc}", exc=True)
            return None
        if not (response.ok and response.content):
            logger.warning(f"长期记忆整理 LLM 返回失败（场次 {live_session_id}）")
            return None
        text = str(response.content).strip()
        return text or None

    def _get_system_prompt(self) -> str:
        """渲染系统提示词（含上限变量，结果缓存复用）。"""
        if not self._system_prompt:
            self._system_prompt = self._prompt_manager.render(_SYSTEM_TEMPLATE, max_chars=self._max_chars)
        return self._system_prompt
