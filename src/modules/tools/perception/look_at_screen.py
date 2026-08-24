"""look_at_screen 工具 —— 屏幕快照同步工具（v2.0.0 / Wave 7）

按架构 §1.5.1 定案：
- 屏幕画面 = **快照型** → 同步工具（gather 等齐结果）
- 任何 Agent 都可调用（公共工具，放 ``tools/perception/``）
- 后端（屏幕采集 / 文本识别）通过 Protocol 注入
- 后端缺失时**优雅降级**：返回成功 + 空文本 + 警告 block（不抛）

数据流：
    Agent → ToolRegistry.invoke("look_at_screen")
        → LookAtScreenProvider.invoke(invocation)
        → ScreenCapture.capture(region)  (PIL Image or None)
        → TextReader.read(image)         (str or None, 可选)
        → ToolExecutionResult (text content + image block)

落地形态（Wave 7）：
- 后端注入即可用：测试用 ``FakeScreenCapture`` + ``FakeTextReader`` 跑通感知-推进闭环
- 生产环境：注入基于 pyautogui / mss / dxcam 的真实采集后端（与屏幕采集器同源，
  但本工具只暴露**调用即看**的同步接口，不做变化检测轮询）

设计要点（§1.2 "判别口诀"）：
- ✅ 只暴露"能力契约"（ToolSpec + ToolProvider）
- ✅ 后端可换可 mock（Protocol 注入）
- ❌ 不内置采集器逻辑（流型归 collectors/screen/，本工具只快照）
- ❌ 不依赖具体游戏（任何需要"看屏幕"的 Agent 都可用）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Tuple

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

logger = get_logger("look_at_screen")


# ---------------------------------------------------------------------------
# 后端协议（依赖注入点；测试用 Fake 实现，生产用 PIL/mss/pyautogui）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScreenCaptureResult:
    """一次屏幕采集的返回值（图像 + 元数据）。

    Attributes:
        image: 图像数据（bytes 形态 PNG / JPEG；None = 后端不可用）
        width: 像素宽（image=None 时可为 0）
        height: 像素高
        mime_type: 图像 MIME（如 ``"image/png"``；image=None 时为空）
        region: 实际采集区域 ``[x1, y1, x2, y2]``；None 表示全屏
        captured_at_ms: 采集时刻（Unix 毫秒）
    """

    image: Optional[bytes] = None
    width: int = 0
    height: int = 0
    mime_type: str = ""
    region: Optional[List[int]] = None
    captured_at_ms: int = 0


class ScreenCapture(Protocol):
    """屏幕采集后端协议（依赖注入点）。

    生产实现可基于 pyautogui / mss / dxcam；测试用 ``FakeScreenCapture``。
    不存在该协议的方法视为"后端不可用" → 工具返回空快照（不抛）。
    """

    def capture(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> ScreenCaptureResult:
        """截取屏幕快照。

        Args:
            region: 可选区域 ``(x1, y1, x2, y2)``；None = 全屏

        Returns:
            :class:`ScreenCaptureResult`；image=None 表示后端不可用
        """
        ...


class TextReader(Protocol):
    """图像→文本 协议（OCR / VLM 均可实现）。

    可选注入：不注入时 ``look_at_screen`` 只返回图像块，不含文本。
    """

    def read(self, image_bytes: bytes, *, mime_type: str = "image/png") -> str:
        """从图像提取文本（OCR / VLM 描述）。

        Args:
            image_bytes: 图像字节（PNG/JPEG 等）
            mime_type: 图像 MIME

        Returns:
            提取的文本（空串表示无可读文本）
        """
        ...


# ---------------------------------------------------------------------------
# 工具规格
# ---------------------------------------------------------------------------


LOOK_AT_SCREEN_SPEC = ToolSpec(
    name="look_at_screen",
    description=(
        "截取屏幕快照（同步工具，调用即看）。返回当前屏幕的文本内容"
        "（来自注入的 OCR/VLM reader）+ 图像块（base64）。"
        "无屏幕采集后端时返回成功 + 空内容（不抛异常，Agent 可继续）。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "可选截图区域 [x1, y1, x2, y2]；缺省=全屏",
            },
            "max_width": {
                "type": "integer",
                "description": "图像缩放最大宽度（像素，0=不缩放；省 token 用）",
                "minimum": 0,
            },
        },
        "required": [],
    },
    kind="sync",
    provider="builtin",
    output_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "OCR/VLM 提取的文本"},
            "image": {"type": "string", "description": "图像 base64（PNG）"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "backend_available": {"type": "boolean"},
        },
    },
)


def build_look_at_screen_spec() -> ToolSpec:
    """构造 look_at_screen ToolSpec（工厂方法，便于将来参数化）。"""
    return LOOK_AT_SCREEN_SPEC


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class LookAtScreenProvider(ToolProvider):
    """``look_at_screen`` 工具的 ToolProvider（§1.5 Provider 协议）。

    通过构造器注入屏幕采集 / 文本读取后端；测试可传 ``None`` 表示优雅降级。

    Example:
        >>> provider = LookAtScreenProvider(screen_capture=PyautoguiCapture())
        >>> registry.register_provider(provider)
    """

    def __init__(
        self,
        *,
        screen_capture: Optional[ScreenCapture] = None,
        text_reader: Optional[TextReader] = None,
        default_max_width: int = 0,
    ) -> None:
        self._capture = screen_capture
        self._reader = text_reader
        self._default_max_width = int(default_max_width)
        self._call_count = 0

    @property
    def name(self) -> str:
        return "LookAtScreenProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [LOOK_AT_SCREEN_SPEC]

    @property
    def call_count(self) -> int:
        """测试用：累计调用次数。"""
        return self._call_count

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行 look_at_screen：截屏 + 可选 OCR + 返回 ResultBlocks。"""
        self._call_count += 1
        started_ms = int(time.time() * 1000)

        args = invocation.arguments or {}
        region_raw = args.get("region")
        region: Optional[Tuple[int, int, int, int]] = None
        if isinstance(region_raw, (list, tuple)) and len(region_raw) == 4:
            try:
                region = (int(region_raw[0]), int(region_raw[1]), int(region_raw[2]), int(region_raw[3]))
            except (TypeError, ValueError):
                region = None

        max_width_raw = args.get("max_width")
        try:
            max_width = int(max_width_raw) if max_width_raw is not None else 0
        except (TypeError, ValueError):
            max_width = 0
        if max_width <= 0:
            max_width = self._default_max_width

        # 1) 采集后端不可用 → 优雅降级（不抛，返回成功 + 空文本 + 警告块）
        if self._capture is None:
            return ToolExecutionResult(
                tool_name="look_at_screen",
                success=True,
                content="(no screen capture backend installed; returning empty snapshot)",
                blocks=[
                    ResultBlock(
                        kind="text",
                        text=(
                            "[look_at_screen] 后端 ScreenCapture 未注入；"
                            "返回空快照。请在生产 wiring 处注入 pyautogui/mss/dxcam 后端；"
                            "测试场景下注入 FakeScreenCapture 即可。"
                        ),
                    ),
                ],
                structured_content={"text": "", "backend_available": False},
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 2) 调用采集后端（捕获异常 → 失败 result，不抛）
        try:
            result = self._capture.capture(region=region)
        except Exception as exc:  # noqa: BLE001 - 边界处兜底
            logger.warning(f"look_at_screen 采集失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name="look_at_screen",
                success=False,
                error_message=f"ScreenCapture.capture 失败: {type(exc).__name__}: {exc}",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 3) 采集后端返回 None（场景：无显示/无权限）→ 同样优雅
        if result.image is None:
            return ToolExecutionResult(
                tool_name="look_at_screen",
                success=True,
                content="(screen capture returned empty)",
                blocks=[
                    ResultBlock(
                        kind="text",
                        text="[look_at_screen] 屏幕采集后端返回空图像（可能无显示/无权限）。",
                    ),
                ],
                structured_content={"text": "", "backend_available": True, "image_empty": True},
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 4) 可选 OCR/VLM 文本提取
        text = ""
        if self._reader is not None:
            try:
                text = self._reader.read(result.image, mime_type=result.mime_type or "image/png")
            except Exception as exc:  # noqa: BLE001 - 边界处兜底
                logger.warning(f"look_at_screen TextReader 失败: {exc}", exc_info=True)
                text = ""

        # 5) 缩放（占位：当前不真做缩放，只在文本里声明 max_width；后续实现可加）
        #    简化原则：宁可不缩放也别误删信息。
        # 6) 组装 result
        import base64

        blocks: List[ResultBlock] = []
        if text:
            blocks.append(ResultBlock(kind="text", text=text))
        if result.image:
            encoded = base64.b64encode(result.image).decode("ascii")
            blocks.append(
                ResultBlock(
                    kind="image",
                    data=encoded,
                    mime_type=result.mime_type or "image/png",
                )
            )
        if not blocks:
            # 既无文本也无图像（极端情况）→ 放一个空文本兜底
            blocks.append(ResultBlock(kind="text", text=""))

        return ToolExecutionResult(
            tool_name="look_at_screen",
            success=True,
            content=text or "(no text extracted)",
            blocks=blocks,
            structured_content={
                "text": text,
                "width": int(result.width),
                "height": int(result.height),
                "region": list(result.region) if result.region else None,
                "max_width": int(max_width),
                "backend_available": True,
                "captured_at_ms": int(result.captured_at_ms),
            },
            timestamp_ms=int(time.time() * 1000),
            duration_ms=int(time.time() * 1000) - started_ms,
        )


# ---------------------------------------------------------------------------
# Fake 后端（测试 / 默认无依赖时使用）
# ---------------------------------------------------------------------------


class FakeScreenCapture:
    """测试用 ScreenCapture，可注入预置的截图结果序列。

    Example:
        >>> cap = FakeScreenCapture()
        >>> cap.queue_png(b"\\x89PNG...fake bytes...", width=1920, height=1080)
        >>> provider = LookAtScreenProvider(screen_capture=cap)
    """

    def __init__(self) -> None:
        self._queue: List[ScreenCaptureResult] = []
        self.calls: List[Optional[Tuple[int, int, int, int]]] = []

    def queue(self, result: ScreenCaptureResult) -> None:
        """入队一个采集结果（下次 capture 调用返回）。"""
        self._queue.append(result)

    def queue_png(self, image_bytes: bytes, *, width: int = 1920, height: int = 1080) -> None:
        """便捷方法：入队一个 PNG 图像。"""
        self.queue(
            ScreenCaptureResult(
                image=image_bytes,
                width=width,
                height=height,
                mime_type="image/png",
                captured_at_ms=int(time.time() * 1000),
            )
        )

    def capture(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> ScreenCaptureResult:
        self.calls.append(region)
        if self._queue:
            return self._queue.pop(0)
        # 缺省：返回空 result（代表无显示/无图像）
        return ScreenCaptureResult()


class FakeTextReader:
    """测试用 TextReader，可注入预置的文本结果序列。"""

    def __init__(self) -> None:
        self._queue: List[str] = []
        self.calls = 0

    def queue_text(self, text: str) -> None:
        self._queue.append(text)

    def read(self, image_bytes: bytes, *, mime_type: str = "image/png") -> str:
        self.calls += 1
        if self._queue:
            return self._queue.pop(0)
        return ""


__all__ = [
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
    "LookAtScreenProvider",
    "LOOK_AT_SCREEN_SPEC",
    "build_look_at_screen_spec",
    "FakeScreenCapture",
    "FakeTextReader",
]
