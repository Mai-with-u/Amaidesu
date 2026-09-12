"""ContentEngine——text_adv 包内私有的内容引擎接口

定位：text_adv Agent 的内部件（构造注入，Agent 与其工具直接调用；
不注册进工具注册表、不对其他 Agent 暴露）。

内容：
- ``ContentEngine`` Protocol——引擎接口（start/stop/send_input/status/get_state）
- ``ContentInput`` / ``ContentInputResult`` / ``ContentEngineStatus``——数据类
- ``StubContentEngine``——缺省 stub（无游戏进程时使用，记录所有 send_input 调用）
- ``FakeContentEngine``——测试用 fake（可注入预设响应）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Protocol


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
# Protocol（依赖注入点；引擎实现可替换）
# ---------------------------------------------------------------------------


class ContentEngine(Protocol):
    """内容引擎接口。

    text_adv 通过构造器注入具体实现；本协议只规定所有引擎都必须能
    回答的最小问题——start / stop / send_input / status / get_state。

    接口稳定（调用方只依赖这 5 个方法）；实现可换（stub / fake / 真实
    引擎都满足）；引擎特有的内容状态不在本协议定义（各实现内部自由）。
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

    用于：text_adv 默认接线（生产环境应注入真实引擎）。
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


__all__ = [
    "InputKind",
    "ContentInput",
    "ContentInputResult",
    "ContentEngineStatus",
    "ContentEngine",
    "StubContentEngine",
    "FakeContentEngine",
]
