"""streamer_note —— 主播单槽跨轮工作便签工具（仅对 streamer 可见）。

职责：
- 进程内单槽内存态工作便签：记录主播/房管此刻正在等什么或下一步打算，供紧接的后续轮次衔接；
- 自然过期（默认 5 分钟）与字数上限（默认 120 字），避免旧计划长期滞留污染上下文；
- 传空字符串即清空；新开播或下播清场时清空，不跨场残留；
- 只记等待意图与下一步打算，不记炉子/物品/位置等世界事实（世界事实一律以【游戏那边此刻】最新读数为准）。

注册形态：provider="streamer"、声明名 note → 全名 ``streamer_note``，可见名单 ``["streamer"]``。
"""

from __future__ import annotations

import json
from typing import Any, Callable, ClassVar, Dict, Iterable, Optional, Tuple

from src.agents.streamer.planner_context import age_text
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider
from src.modules.utils.time_utils import now_ms

__all__ = [
    "WORKING_NOTE_MAX_CHARS",
    "WORKING_NOTE_TOOL_NAME",
    "WORKING_NOTE_TTL_MS",
    "WorkingNoteStore",
    "WorkingNoteToolProvider",
    "build_working_note_tool_spec",
]

#: 工作便签最大字数：单槽只放一句当前等待或下一步打算，保持尾部注入极低 token 占用
WORKING_NOTE_MAX_CHARS: int = 120
#: 工作便签自然过期时间（毫秒）：默认 5 分钟未更新即自动失效
WORKING_NOTE_TTL_MS: int = 5 * 60 * 1000

_TOOL_NAME: str = "note"
_PROVIDER: str = "streamer"
WORKING_NOTE_TOOL_NAME: str = f"{_PROVIDER}_{_TOOL_NAME}"

_DESCRIPTION: str = (
    "更新或清空跨轮工作便签（单槽覆盖，默认 5 分钟自动过期）：只用来记此刻正在等什么、或者下一步打算做什么；"
    "传空字符串即清空便签。不要把背包、炉子、坐标等世界事实写进便签，世界事实一律以【游戏那边此刻】最新读数为准。"
)

_PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {
            "type": "string",
            "description": f"此刻正在等什么或下一步打算（{WORKING_NOTE_MAX_CHARS} 字以内；传空字符串表示清空便签）",
        },
    },
    "required": ["note"],
}


def build_working_note_tool_spec() -> ToolSpec:
    """构造单槽工作便签工具定义（声明名 note；全名 streamer_note，仅对 streamer 可见）。"""
    return ToolSpec(
        name=_TOOL_NAME,
        description=_DESCRIPTION,
        parameters_schema=_PARAMETERS_SCHEMA,
        kind="sync",
        provider=_PROVIDER,
        visible_to=["streamer"],
    )


class WorkingNoteStore:
    """主播单槽跨轮工作便签（进程内内存态，带自然过期与场次清空）。"""

    def __init__(
        self,
        *,
        clock: Callable[[], int] = now_ms,
        ttl_ms: int = WORKING_NOTE_TTL_MS,
        max_chars: int = WORKING_NOTE_MAX_CHARS,
    ) -> None:
        self._clock: Callable[[], int] = clock
        self._ttl_ms: int = max(0, int(ttl_ms))
        self._max_chars: int = max(1, int(max_chars))
        # 单槽内容与写入时刻（毫秒）；空串表示当前无便签
        self._text: str = ""
        self._updated_at_ms: int = 0

    def write(self, raw_text: str) -> Dict[str, Any]:
        """覆盖写入工作便签（传空或纯空白则清空），返回给工具观察的结构化回执。"""
        # 折叠内部换行为单行空格，避免便签本身撑开参考段版式
        normalized = " ".join(str(raw_text or "").split())
        if not normalized:
            self.clear()
            return {"ok": True, "cleared": True, "note": ""}
        truncated = len(normalized) > self._max_chars
        stored = normalized[: self._max_chars] if truncated else normalized
        ts = int(self._clock())
        self._text = stored
        self._updated_at_ms = ts
        return {
            "ok": True,
            "cleared": False,
            "note": stored,
            "updated_at_ms": ts,
            "truncated": truncated,
        }

    def clear(self) -> None:
        """立即清空工作便签（开播/下播或模型主动传空清空时调用）。"""
        self._text = ""
        self._updated_at_ms = 0

    def read(self, *, current_ms: Optional[int] = None) -> Optional[Tuple[str, int]]:
        """读取未过期的工作便签 ``(文本, 写入时刻毫秒)``；为空或已过期时惰性清空并返回 None。"""
        if not self._text:
            return None
        now = int(self._clock()) if current_ms is None else int(current_ms)
        if self._ttl_ms > 0 and (now - self._updated_at_ms) >= self._ttl_ms:
            self.clear()
            return None
        return self._text, self._updated_at_ms

    def render(self, *, current_ms: Optional[int] = None) -> str:
        """渲染给 Planner 参考段尾部的【我的工作便签】块；无有效便签时返回空串。"""
        now = int(self._clock()) if current_ms is None else int(current_ms)
        entry = self.read(current_ms=now)
        if entry is None:
            return ""
        text, updated_at_ms = entry
        elapsed = max(0, now - updated_at_ms)
        return (
            f"【我的工作便签】（记于{age_text(elapsed)}；只记此刻正在等什么或下一步打算，"
            f"不要记世界事实；炉子、物品、位置等世界事实一律以【游戏那边此刻】最新读数为准）\n{text}"
        )


class WorkingNoteToolProvider(BaseToolProvider):
    """``streamer_note`` 的工具提供者（归属 streamer，仅对 streamer 可见）。"""

    category: ClassVar[str] = "framework"

    def __init__(self, store: WorkingNoteStore) -> None:
        self._store = store
        self._spec = build_working_note_tool_spec()

    @property
    def name(self) -> str:
        return _PROVIDER

    def list_tools(self) -> Iterable[ToolSpec]:
        return [self._spec]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """接收模型写入或清空单槽便签的调用并返回观察结果。"""
        args = dict(invocation.arguments or {})
        raw = args.get("note", "")
        if not isinstance(raw, str):
            payload: Dict[str, Any] = {"ok": False, "error": "note 参数必须是字符串（传空字符串表示清空）"}
            return ToolExecutionResult(
                tool_name=self._spec.full_name,
                success=False,
                content=json.dumps(payload, ensure_ascii=False),
                structured_content=payload,
                error_message=payload["error"],
                failure_kind="business",
            )
        payload = self._store.write(raw)
        return ToolExecutionResult(
            tool_name=self._spec.full_name,
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            structured_content=payload,
        )
