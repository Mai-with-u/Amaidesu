"""主播记忆工具：``memory_recall_session`` / ``memory_recall_viewer``。

两个真工具（注册进 ToolRegistry、名单只对主播可见），都是按明确的键直接取、
不做自由文本搜索：

- ``recall_session(n_back)``——往前第 n 场的回顾（1 = 上一场）。主播要回答
  "上次玩到哪了""上周答应的事"时用它，不用猜关键词
- ``recall_viewer(nickname | user_id)``——观众画像里对某位观众的印象；昵称经
  ``viewers`` 反查身份键（印象文本不存昵称，昵称随改名漂移）

简单工具正典路径：``ToolSpec`` + 普通 async 函数（``as_tool_impl`` 包装）+
``make_provider_from_specs`` 组装，主播 Agent 启动时一行 ``register_tool_provider``。
提供者短名 ``memory``（主播的 ``streamer`` 短名已被 reply 工具占用，同一提供者一个短名）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

from .recap import format_recap

__all__ = ["MEMORY_PROVIDER_NAME", "RECALL_SESSION_SPEC", "RECALL_VIEWER_SPEC", "build_memory_tool_provider"]

# 提供者短名（注册全名 = memory_recall_session / memory_recall_viewer）
MEMORY_PROVIDER_NAME = "memory"

# 工具目录分类（工具页按它归组；随主播 Agent 启用，不可单独开关）
MEMORY_TOOL_CATEGORY = "memory"

# 查印象时的默认平台（本项目生产平台；工具参数可显式覆盖）
_DEFAULT_PLATFORM = "bilibili"

RECALL_SESSION_SPEC = ToolSpec(
    name="recall_session",
    description=(
        "回忆之前某一场直播的回顾：那场做了什么、答应过观众什么、聊过哪些梗、谁来得多。"
        "n_back=1 是上一场，2 是上上场，以此类推。观众问“上次玩到哪了”“上周说好的事”时用。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "n_back": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 1,
                "description": "往前数第几场（1 = 上一场）",
            },
        },
        "required": [],
    },
    kind="sync",
    result_event="",
    provider=MEMORY_PROVIDER_NAME,
)

RECALL_VIEWER_SPEC = ToolSpec(
    name="recall_viewer",
    description=(
        "查观众画像里对某位观众的印象（偏好、性格、与主播的关系、待回应的历史）。"
        "提供 nickname（昵称）或 user_id 之一；只有互动够多的常客才有印象，没有就是还不熟。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "观众昵称（经统计表反查用户 ID）"},
            "user_id": {"type": "string", "description": "平台用户 ID"},
            "platform": {"type": "string", "description": "平台标识，默认 bilibili"},
        },
        "required": [],
    },
    kind="sync",
    result_event="",
    provider=MEMORY_PROVIDER_NAME,
)


def build_memory_tool_provider(
    *,
    recap_repo: Any,
    profile_repo: Any,
    viewer_repo: Any = None,
    current_session_provider: Optional[Callable[[], Optional[int]]] = None,
) -> ToolProvider:
    """组装记忆工具 Provider（provider="memory"，两个工具）。

    Args:
        recap_repo: ``SessionRecapRepo``——场次回顾查询
        profile_repo: ``ViewerProfileRepo``——观众印象查询
        viewer_repo: ``ViewerRepo``——昵称反查（可选；缺失时只能按 user_id 查）
        current_session_provider: 返回进行中场次主键的回调（``None`` = 未开播）；
            本场还没收播，往前数时要把它排除掉
    """

    async def _recall_session(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        try:
            n_back = int(args.get("n_back", 1))
        except (TypeError, ValueError):
            n_back = 1
        n_back = max(1, min(n_back, 20))
        current = current_session_provider() if current_session_provider is not None else None
        rows = await recap_repo.list_recent_recaps(limit=n_back, exclude_session_id=current)
        if len(rows) < n_back:
            return (
                "（没有那么早的场次回顾）" if rows else "（还没有任何场次回顾——这是第一场，或者之前的场次没留下记录）"
            )
        return format_recap(rows[n_back - 1])

    async def _recall_viewer(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        nickname = str(args.get("nickname", "") or "").strip()
        user_id = str(args.get("user_id", "") or "").strip()
        platform = str(args.get("platform", "") or "").strip() or _DEFAULT_PLATFORM
        if not nickname and not user_id:
            return "（请提供 nickname 或 user_id）"
        if not user_id:
            if viewer_repo is None:
                return "（无法按昵称反查观众，请提供 user_id）"
            resolved = await viewer_repo.get_viewer_id_by_name(platform=platform, user_name=nickname)
            if not resolved:
                return f"（没找到叫「{nickname}」的观众，可能还没互动过或改过名）"
            user_id = str(resolved)
        profile_text = await profile_repo.get_profile_text(platform=platform, user_id=user_id)
        label = nickname or f"{platform}/{user_id}"
        if not profile_text:
            return f"（对 {label} 还没有形成印象——互动不多或还没收播整理过）"
        return f"对 {label} 的印象：\n{profile_text}"

    return make_provider_from_specs(
        MEMORY_PROVIDER_NAME,
        [
            (RECALL_SESSION_SPEC, as_tool_impl(RECALL_SESSION_SPEC.full_name, _recall_session)),
            (RECALL_VIEWER_SPEC, as_tool_impl(RECALL_VIEWER_SPEC.full_name, _recall_viewer)),
        ],
        category=MEMORY_TOOL_CATEGORY,
    )
