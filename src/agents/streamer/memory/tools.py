"""主播记忆工具：``memory_recall_session`` / ``memory_recall_viewer``。

两个真工具（注册进 ToolRegistry、名单只对主播可见），都是按明确的键直接取、
不做自由文本搜索：

- ``recall_session(n_back)``——往前第 n 场的回顾（1 = 上一场）。主播要回答
  "上次玩到哪了""上周答应的事"时用它，不用猜关键词
- ``recall_viewer(nickname | viewer_id)``——观众画像里对某位观众的印象；昵称经
  ``viewers`` 反查观众（印象文本不存昵称，昵称随改名漂移），当前昵称找不到时
  也认曾用名，并告诉主播对方现在叫什么

简单工具正典路径：``ToolSpec`` + 普通 async 函数（``as_tool_impl`` 包装）+
``make_provider_from_specs`` 组装，主播 Agent 启动时一行 ``register_tool_provider``。
提供者短名 ``memory``（主播的 ``streamer`` 短名已被 reply 工具占用，同一提供者一个短名）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.notebook import StreamerNotebookChangedPayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

from .notebook import StreamerNotebook
from .recap import format_recap

logger = get_logger("StreamerNotebookTools")

__all__ = [
    "MEMORY_PROVIDER_NAME",
    "NOTEBOOK_SPEC",
    "NOTEBOOK_TOOL_FULL_NAME",
    "RECALL_SESSION_SPEC",
    "RECALL_VIEWER_SPEC",
    "build_memory_tool_provider",
    "build_notebook_tool_provider",
    "emit_notebook_changed",
]

# 提供者短名（注册全名 = memory_recall_session / memory_recall_viewer）
MEMORY_PROVIDER_NAME = "memory"

# 备忘录工具的注册形态：provider="streamer"、声明名 notebook → 全名 streamer_notebook
# （与 streamer_reply 同短名；测试锁定全名防拼写类事故）
NOTEBOOK_PROVIDER_NAME = "streamer"
NOTEBOOK_TOOL_FULL_NAME = "streamer_notebook"

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
        "提供 nickname（昵称，改过名的观众用以前的名字也能找到）或 viewer_id 之一；"
        "只有互动够多的常客才有印象，没有就是还不熟。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "观众昵称（当前昵称或曾用名）"},
            "viewer_id": {"type": "integer", "description": "观众编号（之前查询结果里给出的 #编号）"},
            "platform": {"type": "string", "description": "按昵称查时的平台标识，默认 bilibili"},
        },
        "required": [],
    },
    kind="sync",
    result_event="",
    provider=MEMORY_PROVIDER_NAME,
)


NOTEBOOK_SPEC = ToolSpec(
    name="notebook",
    description=(
        "本场备忘录（工作笔记，全量读写）：你的跨轮次记忆——滚动上下文每轮被裁剪，"
        "备忘录不会，且每轮自动注入决策与表达上下文。沉淀结论，不沉淀流水——"
        "坐标、观察编号、证据清单和操作流水不写进来；过几轮还会需要、而滚动窗口留不住的"
        "信息才写。无参调用=读回全文；带 content=用新全文覆盖旧全文。收播清空，跨场记忆走场次回顾。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "新的备忘录全文（覆盖写）；缺省 = 只读"},
        },
        "required": [],
    },
    kind="sync",
    result_event="",
    provider=NOTEBOOK_PROVIDER_NAME,
)


async def emit_notebook_changed(event_bus: Any, notebook_text: str) -> None:
    """发布 ``streamer.notebook.changed`` 观测事件（写入/清空后调用）。

    终点广播：只供观察面渲染与复盘落盘（EventHistoryRecorder 按 ``#`` 通配
    接住落 `logs/events/`），消费者不得触发新决策；发布失败只告警不反噬工具
    执行与生命周期收口。``live_session_id`` 发布方不填，由事件总线的场次盖章
    拦截器注入（与 ``streamer.speech`` 同路）。
    """
    if event_bus is None:
        return
    try:
        await event_bus.emit(
            CoreEvents.STREAMER_NOTEBOOK_CHANGED,
            StreamerNotebookChangedPayload(notebook=notebook_text),
            source="streamer_notebook",
        )
    except Exception as exc:  # noqa: BLE001 - 观测事件不阻断写入路径
        logger.warning(f"streamer.notebook.changed 发布失败（已忽略）: {exc}")


def build_notebook_tool_provider(notebook: StreamerNotebook, event_bus: Any = None) -> ToolProvider:
    """组装备忘录工具 Provider（provider="streamer"，全名 ``streamer_notebook``）。

    与 ``streamer_reply`` 共用提供者短名 "streamer"——注册表按派生全名索引，
    全名唯一即可；备忘录是主播自己的工作笔记，随主播 Agent 一并注册。
    ``event_bus`` 用于写入成功后发 ``streamer.notebook.changed`` 观测事件
    （读回不发——读不改变备忘录状态）；未注入（极简场景）时只写不发。
    """

    async def _notebook(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        if "content" not in args:
            text = notebook.read()
            return text if text else "（备忘录还是空的）"
        saved = notebook.set(str(args.get("content", "")))
        await emit_notebook_changed(event_bus, saved)
        return saved

    return make_provider_from_specs(
        NOTEBOOK_PROVIDER_NAME,
        [(NOTEBOOK_SPEC, as_tool_impl(NOTEBOOK_SPEC.full_name, _notebook))],
        category=MEMORY_TOOL_CATEGORY,
    )


def build_memory_tool_provider(
    *,
    recap_repo: Any,
    profile_repo: Any,
    viewer_repo: Any = None,
    current_session_provider: Optional[Callable[[], Optional[int]]] = None,
    current_kind_provider: Optional[Callable[[], SessionKind]] = None,
) -> ToolProvider:
    """组装记忆工具 Provider（provider="memory"，两个工具）。

    Args:
        recap_repo: ``SessionRecapRepo``——场次回顾查询
        profile_repo: ``ViewerProfileRepo``——观众印象查询
        viewer_repo: ``ViewerRepo``——昵称反查与展示名（可选；缺失时只能按 viewer_id 查）
        current_session_provider: 返回进行中场次主键的回调（``None`` = 未开播）；
            本场还没收播，往前数时要把它排除掉
        current_kind_provider: 返回本场类型的回调（未开播时按正式场算）；往前数只数
            同类型场次——彩排里问"上一场"不会翻到正式直播，反之亦然。``None`` 按正式场
    """

    async def _recall_session(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        try:
            n_back = int(args.get("n_back", 1))
        except (TypeError, ValueError):
            n_back = 1
        n_back = max(1, min(n_back, 20))
        current = current_session_provider() if current_session_provider is not None else None
        kind = current_kind_provider() if current_kind_provider is not None else SESSION_KIND_LIVE
        rows = await recap_repo.list_recent_recaps(limit=n_back, exclude_session_id=current, kind=kind)
        if len(rows) < n_back:
            return (
                "（没有那么早的场次回顾）" if rows else "（还没有任何场次回顾——这是第一场，或者之前的场次没留下记录）"
            )
        return format_recap(rows[n_back - 1])

    async def _recall_viewer(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        nickname = str(args.get("nickname", "") or "").strip()
        platform = str(args.get("platform", "") or "").strip() or _DEFAULT_PLATFORM
        try:
            viewer_id = int(args["viewer_id"]) if args.get("viewer_id") not in (None, "") else None
        except (TypeError, ValueError):
            return "（viewer_id 必须是观众编号数字）"
        if not nickname and viewer_id is None:
            return "（请提供 nickname 或 viewer_id）"
        note = ""
        if viewer_id is None:
            if viewer_repo is None:
                return "（无法按昵称反查观众，请提供 viewer_id）"
            match = await viewer_repo.find_by_name(platform=platform, user_name=nickname)
            if match is None:
                return f"（没找到叫「{nickname}」的观众，可能还没互动过）"
            viewer_id = match.viewer_id
            # 命中的是曾用名：告诉主播对方现在叫什么，免得拿旧名字称呼
            if match.by_former_name:
                note = f"（「{nickname}」是以前的名字，现在叫「{match.current_name}」）"
        label = f"{nickname or '这位观众'}（观众 #{viewer_id}）"
        profile_text = await profile_repo.get_profile_text(viewer_id=viewer_id)
        if not profile_text:
            return f"{note}（对 {label} 还没有形成印象——互动不多或还没收播整理过）"
        return f"{note}对 {label} 的印象：\n{profile_text}"

    return make_provider_from_specs(
        MEMORY_PROVIDER_NAME,
        [
            (RECALL_SESSION_SPEC, as_tool_impl(RECALL_SESSION_SPEC.full_name, _recall_session)),
            (RECALL_VIEWER_SPEC, as_tool_impl(RECALL_VIEWER_SPEC.full_name, _recall_viewer)),
        ],
        category=MEMORY_TOOL_CATEGORY,
    )
