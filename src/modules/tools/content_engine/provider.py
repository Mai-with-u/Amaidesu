"""ContentEngine Protocol + Provider（v2.0.0 / Wave 7）

按架构 §1.5.1 定案：
- ContentEngine = 通用游戏控制器控制面（start/stop/send_input/status/get_state）
- 是"接口契约"，不是游戏实现
- 游戏 Agent 通过它驱动具体游戏进程（MC / 文字冒险 / ...）
- 本模块是 **control plane** —— 落地点是 ToolProvider + Protocol，
  具体游戏引擎（MinecraftEngine / TextAdvEngine / ...）由各游戏 Agent 包自己实现

判别（§1.2 判别哲学）：
- ✅ 提供"通用能力契约"（任何游戏都能套用）
- ✅ 后端可换可 mock（Protocol + 构造注入）
- ❌ 不实现任何具体游戏的逻辑
- ❌ 不依赖 Agent 类型
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Protocol

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

logger = get_logger("content_engine")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


InputKind = Literal["click", "key", "command", "raw"]


@dataclass(slots=True)
class ContentInput:
    """游戏输入（统一抽象）。

    Attributes:
        kind: 输入类型 — "click"（坐标点击）/ "key"（按键）/ "command"（命令）/"raw"（透传）
        x: click 模式 X 坐标
        y: click 模式 Y 坐标
        button: click 模式按键（"left"/"right"/"middle"，默认 left）
        key: key 模式按键名（"enter"/"space"/"escape" 等）
        command: command 模式字符串
        raw: raw 模式透传字符串
        payload: 扩展字段（按需；测试用）
    """

    kind: InputKind = "click"
    x: int = 0
    y: int = 0
    button: str = "left"
    key: str = ""
    command: str = ""
    raw: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContentInputResult:
    """单次输入响应。

    Attributes:
        accepted: 是否被引擎接受（≠ 执行成功——某些引擎异步受理）
        echoed: 引擎回显字符串（可空）
        error_message: 错误信息（接受失败 / 异常）
    """

    accepted: bool = True
    echoed: str = ""
    error_message: str = ""


@dataclass(slots=True)
class ContentEngineStatus:
    """引擎运行状态。"""

    running: bool = False
    engine_kind: str = "stub"
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol（依赖注入点；游戏 Agent 自己实现）
# ---------------------------------------------------------------------------


class ContentEngine(Protocol):
    """通用游戏控制器接口（§1.5.1 控制面）。

        游戏 Agent 通过构造器注入具体实现（TextAdvEngine / MinecraftEngine / ...）；
        本协议只规定**所有引擎都必须能回答的最小问题**——
        start / stop / send_input / status / get_state。

        实现要点（来自 §1.2 判别）：
    - ✅ 接口稳定（游戏 Agent 只依赖这 5 个方法）
    - ✅ 实现可换（mock / stub / 真实引擎 都满足）
    - ❌ 不在本协议里塞游戏特定字段（如 MC 的世界坐标 / 文字冒险的剧情节点）
            ——那些归各 Agent 包内部状态，§1.31 内容状态内部自由
    """

    async def start(self) -> None:
        """启动引擎进程/会话。"""
        ...

    async def stop(self) -> None:
        """停止引擎进程/会话。"""
        ...

    async def send_input(self, content_input: ContentInput) -> ContentInputResult:
        """向引擎投递一次输入（点击/按键/命令等）。"""
        ...

    async def status(self) -> ContentEngineStatus:
        """查询运行状态。"""
        ...

    async def get_state(self) -> Dict[str, Any]:
        """读取引擎侧持久状态（自由 dict；游戏 Agent 自己定义 schema）。"""
        ...


# ---------------------------------------------------------------------------
# Stub 默认实现（无游戏进程时使用；永远 success，仅记录）
# ---------------------------------------------------------------------------


class StubContentEngine:
    """无操作 ContentEngine（默认 / 无依赖时）。

    行为：
    - start/stop → no-op
    - send_input → 记录到 ``self.sent_inputs``（测试可断言）
    - status → running=True（已"启动"）
    - get_state → 返回空 dict

    用于：示例游戏 Agent 默认 wiring（生产环境应注入真实引擎）。
    """

    def __init__(self, *, engine_kind: str = "stub") -> None:
        self._engine_kind = engine_kind
        self._running = False
        self.sent_inputs: List[ContentInput] = []
        self.state: Dict[str, Any] = {}

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send_input(self, content_input: ContentInput) -> ContentInputResult:
        if not self._running:
            return ContentInputResult(accepted=False, error_message="引擎未启动")
        self.sent_inputs.append(content_input)
        return ContentInputResult(accepted=True, echoed=f"stub:{content_input.kind}")

    async def status(self) -> ContentEngineStatus:
        return ContentEngineStatus(running=self._running, engine_kind=self._engine_kind)

    async def get_state(self) -> Dict[str, Any]:
        return dict(self.state)


# ---------------------------------------------------------------------------
# FakeContentEngine（测试用，可预设响应）
# ---------------------------------------------------------------------------


class FakeContentEngine:
    """测试用 ContentEngine，支持预设 send_input 响应序列。

    Example:
        >>> engine = FakeContentEngine(engine_kind="test")
        >>> engine.queue_response(ContentInputResult(accepted=True, echoed="ok"))
        >>> result = await engine.send_input(ContentInput(kind="key", key="enter"))
        >>> assert result.accepted is True
    """

    def __init__(self, *, engine_kind: str = "fake") -> None:
        self._engine_kind = engine_kind
        self._running = False
        self._responses: List[ContentInputResult] = []
        self.sent_inputs: List[ContentInput] = []
        self.start_count = 0
        self.stop_count = 0
        self.state: Dict[str, Any] = {}

    def queue_response(self, result: ContentInputResult) -> None:
        self._responses.append(result)

    async def start(self) -> None:
        self._running = True
        self.start_count += 1

    async def stop(self) -> None:
        self._running = False
        self.stop_count += 1

    async def send_input(self, content_input: ContentInput) -> ContentInputResult:
        self.sent_inputs.append(content_input)
        if not self._running:
            return ContentInputResult(accepted=False, error_message="引擎未启动")
        if self._responses:
            return self._responses.pop(0)
        return ContentInputResult(accepted=True, echoed="default-ok")

    async def status(self) -> ContentEngineStatus:
        return ContentEngineStatus(running=self._running, engine_kind=self._engine_kind)

    async def get_state(self) -> Dict[str, Any]:
        return dict(self.state)


# ---------------------------------------------------------------------------
# ToolProvider：把 ContentEngine 封装成 5 个工具
# ---------------------------------------------------------------------------


_CONTENT_ENGINE_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="content_engine_start",
        description="启动游戏内容引擎（同步调用；等待引擎就绪或返回失败）。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="builtin",
    ),
    ToolSpec(
        name="content_engine_stop",
        description="停止游戏内容引擎（释放资源）。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="builtin",
    ),
    ToolSpec(
        name="content_engine_send_input",
        description=(
            "向游戏引擎发送一次输入（点击/按键/命令）。"
            "返回引擎受理结果；某些引擎异步执行，accepted=True 仅代表受理成功。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["click", "key", "command", "raw"],
                    "description": "输入类型",
                },
                "x": {"type": "integer", "description": "click 模式 X 坐标"},
                "y": {"type": "integer", "description": "click 模式 Y 坐标"},
                "button": {"type": "string", "description": "click 模式按键 (left/right/middle)"},
                "key": {"type": "string", "description": "key 模式按键名"},
                "command": {"type": "string", "description": "command 模式字符串"},
                "raw": {"type": "string", "description": "raw 模式透传内容"},
                "payload": {"type": "object", "description": "扩展字段（按引擎需求）"},
            },
            "required": ["kind"],
        },
        kind="sync",
        provider="builtin",
    ),
    ToolSpec(
        name="content_engine_status",
        description="查询内容引擎运行状态（running / engine_kind / 扩展字段）。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="builtin",
    ),
    ToolSpec(
        name="content_engine_get_state",
        description="读取引擎侧持久状态（自由 dict；具体 schema 由各游戏 Agent 定义）。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="builtin",
    ),
]


def build_content_engine_specs() -> List[ToolSpec]:
    """构造 ContentEngine 工具 spec 列表（工厂方法）。"""
    return list(_CONTENT_ENGINE_SPECS)


class ContentEngineProvider(ToolProvider):
    """把 ContentEngine 封装成 5 个工具注册到 ToolRegistry。

    工具来源 provider="builtin"（框架基础设施，按 §1.5 provider 溯源）。
    注意：本 Provider **不实现具体游戏逻辑**——它只是 ContentEngine 的薄包装。
    """

    def __init__(self, engine: ContentEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "ContentEngineProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return list(_CONTENT_ENGINE_SPECS)

    @property
    def engine(self) -> ContentEngine:
        """暴露引擎实例（供 Agent 直接 send_input 走内部通路）。"""
        return self._engine

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args = invocation.arguments or {}
        try:
            if name == "content_engine_start":
                await self._engine.start()
                return _ok(name, "started")
            if name == "content_engine_stop":
                await self._engine.stop()
                return _ok(name, "stopped")
            if name == "content_engine_send_input":
                kind = str(args.get("kind", "click"))
                content_input = ContentInput(
                    kind=kind,  # type: ignore[arg-type]
                    x=int(args.get("x", 0) or 0),
                    y=int(args.get("y", 0) or 0),
                    button=str(args.get("button", "left") or "left"),
                    key=str(args.get("key", "") or ""),
                    command=str(args.get("command", "") or ""),
                    raw=str(args.get("raw", "") or ""),
                    payload=dict(args.get("payload") or {}),
                )
                result = await self._engine.send_input(content_input)
                if not result.accepted:
                    return ToolExecutionResult(
                        tool_name=name,
                        success=False,
                        error_message=result.error_message or "引擎拒绝输入",
                    )
                return _ok(name, result.echoed or "accepted")
            if name == "content_engine_status":
                st = await self._engine.status()
                return _ok(name, repr(st))
            if name == "content_engine_get_state":
                state = await self._engine.get_state()
                return _ok(name, str(state))
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"未知 ContentEngine 工具 '{name}'",
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底
            logger.warning(f"ContentEngine 工具 '{name}' 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=int(time.time() * 1000),
            )


def _ok(tool_name: str, content: str) -> ToolExecutionResult:
    """生成成功 ToolExecutionResult 的小工具（带时间戳）。"""
    return ToolExecutionResult(
        tool_name=tool_name,
        success=True,
        content=content,
        timestamp_ms=int(time.time() * 1000),
    )


__all__ = [
    "InputKind",
    "ContentInput",
    "ContentInputResult",
    "ContentEngineStatus",
    "ContentEngine",
    "StubContentEngine",
    "FakeContentEngine",
    "ContentEngineProvider",
    "build_content_engine_specs",
]
