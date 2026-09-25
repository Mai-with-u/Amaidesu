"""look_at_screen 工具 —— 屏幕快照异步工具（被动多显示器/区域感知）

定位：
- 屏幕画面 = **快照型** → 工具内异步调用（gather 等齐结果）
- 任何 Agent 都可调用（公共工具，注册名 ``vision_look_at_screen``）
- 后端（屏幕采集 / 文本识别）通过 Protocol 注入
- 后端缺失 / 抓取失败 / VLM 失败 → **不抛**，统一返回 ``success=True`` +
  ``text=""`` + ``error``（降级语义，调用方据此继续）

数据流：
    Agent → ToolRegistry.invoke("vision_look_at_screen")
        → LookAtScreenProvider.invoke(invocation)        (async)
        → ScreenCapture.capture(monitor_index, region, max_width)   (PNG bytes or None)
        → TextReader.read(image_bytes, question=...)     (async; str 或空)
        → ToolExecutionResult (text content + image block + structured_content)

落地形态：
- 后端注入即可用：测试用 ``FakeScreenCapture`` + ``FakeTextReader`` 跑通感知-推进闭环
- 生产环境：注入 ``MssScreenCapture`` + ``LlmVisionTextReader``（组合根 ``main.py``）
- 零参 ``invoke(arguments={})`` 必须可用（``text_adv`` Agent 的调用形态）

设计要点：
- 工具描述里写**何时用**的引导；主播提示词不动
- 所有入参可选，缺省走 provider 配置默认
- 图片生命周期 = 工具调用内编码，调用结束后即丢弃引用（无缓存）
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.llm.bootstrap import ProfileNames
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider

logger = get_logger("look_at_screen")

# LlmVisionTextReader 的 VLM 调用模板键（由 Task 3 迁移到 vision/prompts/）。
SCREEN_VLM_PROMPT_KEY = "screen_vlm_prompt"
SCREEN_VLM_SYSTEM_KEY = "screen_vlm_system"


# ---------------------------------------------------------------------------
# 后端协议（依赖注入点；测试用 Fake 实现，生产用 MssScreenCapture）
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
        monitor_index: 实际使用的显示器索引（非法回退后会与入参不同）
    """

    image: Optional[bytes] = None
    width: int = 0
    height: int = 0
    mime_type: str = ""
    region: Optional[List[int]] = None
    captured_at_ms: int = 0
    monitor_index: int = 0


class ScreenCapture(Protocol):
    """屏幕采集后端协议（依赖注入点）。

    生产实现为 ``MssScreenCapture``（基于 mss 库，支持多显示器 / 区域相对换算
    / 越界 clamp / 真缩放）；测试用 ``FakeScreenCapture``。后端缺失或抓取
    失败时 ``image=None``，由 ``LookAtScreenProvider`` 走降级路径。
    """

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """截取指定显示器（+ 可选区域）的快照。

        Args:
            monitor_index: 显示器索引（1..N 物理显示器）；非法 → 后端自行
                决定降级（``MssScreenCapture`` 回退到首个物理显示器 + warning）。
            region: 可选区域 ``(x1, y1, x2, y2)``，相对所选显示器左上角；
                None = 全屏。
            max_width: 非 None 且图宽 > ``max_width`` 时做等比缩放；
                None 或 0 = 不缩放。

        Returns:
            :class:`ScreenCaptureResult`；``image=None`` 表示后端不可用或抓取失败。
        """
        ...


class TextReader(Protocol):
    """图像→文本 协议（OCR / VLM 均可实现，异步）。

    异步契约是 VLM/OCR 等 I/O 调用的硬要求，避免阻塞事件循环。失败 / 超时
    一律返回 ``""``（由 ``LookAtScreenProvider`` 组装 ``error`` 字段）。
    """

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        """从图像提取文本（OCR / VLM 描述）。

        Args:
            image_bytes: 图像字节（PNG/JPEG 等）
            mime_type: 图像 MIME
            question: 调用方对本次识别的提问（如"屏幕上显示什么"）；
                None = 实现自行决定使用默认提示

        Returns:
            提取的文本（空串表示无可读文本或识别失败/超时/异常）
        """
        ...


# ---------------------------------------------------------------------------
# 工具规格
# ---------------------------------------------------------------------------

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "vision"

# 工具声明名（不含 provider 前缀）。注册后全名 = ``vision_look_at_screen``，
# 与 ``text_adv`` Agent 的既有调用形态一致（名称稳定即兼容锚点）。
TOOL_NAME = "look_at_screen"

LOOK_AT_SCREEN_SPEC = ToolSpec(
    name=TOOL_NAME,
    description=(
        "当你需要知道屏幕上/游戏里正在发生什么时调用；可用 question 指定要看什么。"
        "可指定 monitor_index 切显示器、region 限定子区域、max_width 控制图像最大宽度"
        "（等比缩放，省 token）。所有参数都可选；不传则使用 provider 配置的默认值。"
        "失败时（采集失败 / VLM 超时 / 异常）返回空文本 + error 字段，不抛异常。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": '本次识别想问的问题（如"屏幕上显示什么"）；不传则走默认提示',
                "minLength": 1,
            },
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
                "description": "截图子区域 [x1, y1, x2, y2]，相对所选显示器左上角；不传则全屏",
            },
            "monitor_index": {
                "type": "integer",
                "description": "显示器索引（1..N 物理显示器；非法 → 自动回退并 warning）",
                "minimum": 0,
            },
            "max_width": {
                "type": "integer",
                "description": "图像缩放最大宽度（像素）；超出会等比缩放；不传则走 provider 默认",
                "minimum": 100,
                "maximum": 3840,
            },
        },
        "required": [],
    },
    kind="sync",
    provider=PROVIDER_NAME,
    output_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "VLM/OCR 提取的文本；失败时空串"},
            "image": {
                "type": "string",
                "description": "图像 base64（PNG）；采集失败时缺失",
            },
            "width": {"type": "integer", "description": "图像宽度（像素）"},
            "height": {"type": "integer", "description": "图像高度（像素）"},
            "monitor_index": {"type": "integer", "description": "实际使用的显示器索引"},
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "实际采集区域（相对显示器左上角）；None 表示全屏",
            },
            "error": {
                "type": "string",
                "description": "降级原因（capture_failed / vlm_timeout / vlm_failed 等）；无错误时缺失",
            },
            "latency_ms": {
                "type": "integer",
                "description": "工具调用总耗时（毫秒）",
            },
        },
    },
)


def build_look_at_screen_spec() -> ToolSpec:
    """构造 look_at_screen ToolSpec（工厂方法，便于将来参数化）。"""
    return LOOK_AT_SCREEN_SPEC


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class LookAtScreenProvider(BaseToolProvider):
    """``look_at_screen`` 工具的 ToolProvider（Provider 协议）。

    通过构造器注入屏幕采集 / 文本读取后端；测试可传 ``None`` 表示优雅降级。

    Example:
        >>> provider = LookAtScreenProvider(
        ...     config={},
        ...     screen_capture=MssScreenCapture(),
        ...     text_reader=LlmVisionTextReader(llm_manager=llm_mgr),
        ... )
        >>> registry.register_provider(provider)
    """

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "vision"

    class ConfigSchema(BaseConfig):
        """look_at_screen 配置（默认显示器 / 区域 / VLM 超时 / 最大图像宽度）

        TOML 段位：[tools.vision].config
        """

        type: str = "vision"
        # 默认显示器索引（1..N 物理显示器）；对应 ToolSpec 的 monitor_index 入参缺省
        monitor_index: int = Field(
            default=1,
            ge=0,
            description="默认显示器索引（1..N 物理显示器）；非法 → 后端回退并 warning",
        )
        # 默认区域 [x1, y1, x2, y2]（相对显示器左上角）；None = 全屏
        default_region: Optional[List[int]] = Field(
            default=None,
            description="默认区域 [x1, y1, x2, y2]（相对显示器左上角）；None = 全屏",
        )

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        screen_capture: Optional[ScreenCapture] = None,
        text_reader: Optional[TextReader] = None,
    ) -> None:
        # 配置转 typed（config: dict 必填；空 dict = 全部默认；失败 log+raise）
        self._config_raw = dict(config) if config is not None else {}
        self.typed_config = self.ConfigSchema.from_dict(self._config_raw)
        self._default_monitor_index = int(self.typed_config.monitor_index)
        self._default_region: Optional[Tuple[int, int, int, int]] = None
        if self.typed_config.default_region is not None:
            try:
                r = self.typed_config.default_region
                if len(r) == 4:
                    self._default_region = (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
            except (TypeError, ValueError):
                self._default_region = None

        self._capture = screen_capture
        self._reader = text_reader

        self._call_count = 0

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [LOOK_AT_SCREEN_SPEC]

    @property
    def call_count(self) -> int:
        """测试用：累计调用次数。"""
        return self._call_count

    # ----- 入参解析辅助 -----

    @staticmethod
    def _parse_region(raw: Any) -> Optional[Tuple[int, int, int, int]]:
        """把入参 region 规范化为 ``(x1, y1, x2, y2)`` 四元组。非法 → None。"""
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_positive_int(raw: Any) -> Optional[int]:
        """int 解析；None / 非法 → None。"""
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    # ----- invoke 主流程 -----

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行 look_at_screen：截屏 + 可选 VLM + 返回 ResultBlocks + structured_content。"""
        self._call_count += 1
        started_ms = int(time.time() * 1000)
        # 注册名（vision_look_at_screen）即调用方使用的名；结果回显它保持溯源一致
        tool_name = invocation.tool_name

        args = invocation.arguments or {}

        # 入参解析（缺省走 provider 配置）
        question_raw = args.get("question")
        if isinstance(question_raw, str):
            question = question_raw.strip() or None
        elif question_raw is None:
            question = None
        else:
            question = None

        region_arg = self._parse_region(args.get("region"))
        region = region_arg if region_arg is not None else self._default_region

        monitor_arg = self._parse_positive_int(args.get("monitor_index"))
        monitor_index = monitor_arg if monitor_arg is not None else self._default_monitor_index

        max_width_arg = self._parse_positive_int(args.get("max_width"))
        if max_width_arg is not None and max_width_arg > 0:
            max_width = max_width_arg
        else:
            # 默认读取原始画面，只有调用方明确请求缩放时才改变像素尺寸。
            max_width = None

        # 采集后端不可用 → 优雅降级（不抛，返回成功 + 空文本 + error）
        if self._capture is None:
            latency_ms = int(time.time() * 1000) - started_ms
            return self._build_no_backend_result(
                tool_name=tool_name,
                started_ms=started_ms,
                latency_ms=latency_ms,
            )

        # 调用采集后端（捕获异常 → 失败 result，不抛）
        try:
            result = self._capture.capture(
                monitor_index=monitor_index,
                region=region,
                max_width=max_width,
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底
            logger.warning(f"look_at_screen 采集失败: {exc}", exc=True)
            return self._build_capture_failed_result(
                tool_name=tool_name,
                started_ms=started_ms,
                error=f"capture_failed: {type(exc).__name__}: {exc}",
            )

        # 采集后端返回 None（场景：无显示 / 无权限 / 越界退化）→ 优雅降级
        if result is None or result.image is None:
            logger.warning("look_at_screen 采集后端返回空图像（可能无显示 / 无权限 / mss 不可用）")
            return self._build_capture_failed_result(
                tool_name=tool_name,
                started_ms=started_ms,
                error="capture_failed: empty image (no display / permission denied / mss unavailable)",
            )

        # 可选 VLM 文本提取（异步：避免阻塞事件循环；reader 内部负责超时/降级）
        text = ""
        vlm_error: Optional[str] = None
        if self._reader is not None:
            try:
                text = await self._reader.read(
                    result.image,
                    mime_type=result.mime_type or "image/png",
                    question=question,
                )
            except Exception as exc:  # noqa: BLE001 - 边界处兜底（不抛）
                logger.warning(f"look_at_screen TextReader 失败: {exc}", exc=True)
                text = ""
                vlm_error = f"vlm_failed: {type(exc).__name__}: {exc}"

        latency_ms = int(time.time() * 1000) - started_ms

        # 组装 blocks（text + image）
        blocks: List[ResultBlock] = []
        if text:
            blocks.append(ResultBlock(kind="text", text=text))
        encoded = ""
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
            # 既无文本也无图像（极端情况：reader 返回空且 image 为 None）→ 空文本兜底
            blocks.append(ResultBlock(kind="text", text=""))

        # structured_content：含 image 字段（base64），供调用方按需取图
        structured: Dict[str, Any] = {
            "text": text,
            "width": int(result.width),
            "height": int(result.height),
            "monitor_index": int(result.monitor_index or monitor_index),
            "region": list(result.region) if result.region else None,
            "latency_ms": int(latency_ms),
        }
        if encoded:
            structured["image"] = encoded
        if vlm_error:
            structured["error"] = vlm_error

        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=text or "(no text extracted)",
            blocks=blocks,
            structured_content=structured,
            timestamp_ms=int(time.time() * 1000),
            duration_ms=int(latency_ms),
        )

    # ----- 降级结果工厂（统一 success=True + text="" + error 模式）-----

    @staticmethod
    def _build_no_backend_result(
        *,
        tool_name: str,
        started_ms: int,
        latency_ms: int,
    ) -> ToolExecutionResult:
        """ScreenCapture 未注入时的降级结果。"""
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content="(no screen capture backend installed; returning empty snapshot)",
            blocks=[
                ResultBlock(
                    kind="text",
                    text=(
                        "[look_at_screen] 后端 ScreenCapture 未注入；"
                        "返回空快照。请在生产 wiring 处注入 MssScreenCapture；"
                        "测试场景下注入 FakeScreenCapture 即可。"
                    ),
                ),
            ],
            structured_content={
                "text": "",
                "error": "capture_failed: no screen capture backend installed",
                "latency_ms": int(latency_ms),
            },
            timestamp_ms=finished_ms,
            duration_ms=int(latency_ms),
        )

    @staticmethod
    def _build_capture_failed_result(
        *,
        tool_name: str,
        started_ms: int,
        error: str,
    ) -> ToolExecutionResult:
        """capture 失败 / 空图时的降级结果（success=True + text="" + error）。"""
        finished_ms = int(time.time() * 1000)
        latency_ms = finished_ms - started_ms
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content="(screen capture failed; returning empty snapshot)",
            blocks=[
                ResultBlock(
                    kind="text",
                    text=f"[look_at_screen] 屏幕采集失败: {error}",
                ),
            ],
            structured_content={
                "text": "",
                "error": error,
                "latency_ms": int(latency_ms),
            },
            timestamp_ms=finished_ms,
            duration_ms=int(latency_ms),
        )


# ---------------------------------------------------------------------------
# Fake 后端（测试 / 默认无依赖时使用）
# ---------------------------------------------------------------------------


class FakeScreenCapture:
    """测试用 ScreenCapture，可注入预置的截图结果序列。

    新协议形态：``capture(monitor_index, region=None, max_width=None)``，与
    生产 ``MssScreenCapture`` 同形（Task 4 收口后协议已统一）。

    Example:
        >>> cap = FakeScreenCapture()
        >>> cap.queue_png(b"\\x89PNG...fake bytes...", width=1920, height=1080)
        >>> provider = LookAtScreenProvider(screen_capture=cap)
    """

    def __init__(self) -> None:
        self._queue: List[ScreenCaptureResult] = []
        self.calls: List[dict[str, Any]] = []
        self._raise: Optional[BaseException] = None

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
                monitor_index=1,
            )
        )

    def queue_empty(self) -> None:
        """便捷方法：入队一个空 result（image=None），代表无显示/无权限。"""
        self.queue(ScreenCaptureResult(captured_at_ms=int(time.time() * 1000)))

    def set_raise(self, exc: Optional[BaseException]) -> None:
        """下一次 capture 调用抛该异常（用于测试 capture 异常降级）。"""
        self._raise = exc

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        self.calls.append(
            {
                "monitor_index": int(monitor_index),
                "region": region,
                "max_width": max_width,
            }
        )
        if self._raise is not None:
            exc = self._raise
            self._raise = None
            raise exc
        if self._queue:
            return self._queue.pop(0)
        # 缺省：返回空 result（代表无显示/无图像）
        return ScreenCaptureResult(captured_at_ms=int(time.time() * 1000), monitor_index=int(monitor_index))


class FakeTextReader:
    """测试用 TextReader，可注入预置的文本结果序列（异步契约）。"""

    def __init__(self) -> None:
        self._queue: List[str] = []
        self.calls: List[dict[str, Any]] = []
        self._raise: Optional[BaseException] = None
        self._hang_until: Optional[float] = None  # 运行中事件循环的 monotonic 截止时刻

    def queue_text(self, text: str) -> None:
        self._queue.append(text)

    def set_raise(self, exc: Optional[BaseException]) -> None:
        """下一次 read 调用抛该异常（用于测试 reader 异常降级）。"""
        self._raise = exc

    def set_hang_until_ms(self, deadline_ms_from_now: int) -> None:
        """挂起到指定时间点后返回空串（用于测试 reader 超时降级）。"""
        loop_now = asyncio.get_running_loop().time()
        self._hang_until = loop_now + deadline_ms_from_now / 1000.0

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        self.calls.append(
            {
                "image_bytes_len": len(image_bytes),
                "mime_type": mime_type,
                "question": question,
            }
        )
        if self._raise is not None:
            exc = self._raise
            self._raise = None
            raise exc
        if self._hang_until is not None:
            now = asyncio.get_running_loop().time()
            remaining = self._hang_until - now
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._hang_until = None
            return ""
        if self._queue:
            return self._queue.pop(0)
        return ""


# ---------------------------------------------------------------------------
# VLM 实现（生产路径）：构造注入 llm_manager + prompt_manager
# ---------------------------------------------------------------------------


class LlmVisionTextReader:
    """完整等待视觉模型识别画面，生成失败时记录原因并返回空文本。"""

    def __init__(
        self,
        *,
        llm_manager: Any,
        prompt_manager: Optional[PromptManager] = None,
    ) -> None:
        self._llm_manager = llm_manager
        self._prompt_manager = prompt_manager

    def _render_user_prompt(self, question: Optional[str]) -> str:
        """user prompt：优先用调用方传入的 question，否则渲染默认模板。"""
        if question:
            return question
        if self._prompt_manager is None:
            return "描述屏幕上正在显示的内容。"
        try:
            rendered = self._prompt_manager.render(SCREEN_VLM_PROMPT_KEY)
        except Exception as exc:  # noqa: BLE001 - 模板缺失/变量不匹配时降级到内置默认
            logger.warning(f"LlmVisionTextReader 渲染 {SCREEN_VLM_PROMPT_KEY} 失败: {exc}; 改用内置默认")
            return "描述屏幕上正在显示的内容。"
        return rendered or "描述屏幕上正在显示的内容。"

    def _render_system_prompt(self) -> Optional[str]:
        """system 模板（如注入 prompt_manager）：渲染 screen_vlm_system。"""
        if self._prompt_manager is None:
            return None
        try:
            return self._prompt_manager.render(SCREEN_VLM_SYSTEM_KEY)
        except Exception as exc:  # noqa: BLE001 - 模板缺失时降级为 None（系统无 system 也可调）
            logger.warning(f"LlmVisionTextReader 渲染 {SCREEN_VLM_SYSTEM_KEY} 失败: {exc}; system 留空")
            return None

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        """等待完整识别结果；生成失败时返回空文本。"""
        prompt = self._render_user_prompt(question)
        system = self._render_system_prompt()
        try:
            # 读屏可能包含较长文字；等待模型完整返回，并继承父任务的主动取消。
            response = await self._llm_manager.generate_vision(
                prompt,
                [image_bytes],
                profile=ProfileNames.VISION,
                system=system,
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底（不抛）
            logger.warning(f"LlmVisionTextReader VLM 调用异常: {exc}", exc=True)
            return ""

        if not getattr(response, "success", False):
            err = getattr(response, "error", None)
            logger.warning(f"LlmVisionTextReader VLM 返回失败 (error={err!r}); 降级为空文本")
            return ""

        content = getattr(response, "content", None) or ""
        return content.strip()


__all__ = [
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
    "LookAtScreenProvider",
    "LOOK_AT_SCREEN_SPEC",
    "build_look_at_screen_spec",
    "PROVIDER_NAME",
    "TOOL_NAME",
    "FakeScreenCapture",
    "FakeTextReader",
    "LlmVisionTextReader",
    "SCREEN_VLM_PROMPT_KEY",
    "SCREEN_VLM_SYSTEM_KEY",
]
