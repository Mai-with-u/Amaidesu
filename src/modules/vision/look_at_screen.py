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
- 后端注入即可用：测试替身（见 tests/modules/vision/fakes.py）跑通感知-推进闭环
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.llm.capabilities import ProfileVisualCapabilityLookup, validate_llm_capability
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider
from src.modules.vision.capture_types import ScreenCapture, ScreenCaptureResult, TextReader

# 协议与类型已下沉到 capture_types，VLM 生产实现已拆到 vlm_reader；
# 此处 re-export 维持既有 import 路径可用
from src.modules.vision.vlm_reader import (  # noqa: F401
    LlmVisionTextReader,
    SCREEN_VLM_PROMPT_KEY,
    SCREEN_VLM_SYSTEM_KEY,
)

logger = get_logger("look_at_screen")


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

        type: str = Field(default="vision", title="段位标识", json_schema_extra={"x-ui-advanced": True})
        # 默认显示器索引（1..N 物理显示器）；对应 ToolSpec 的 monitor_index 入参缺省
        monitor_index: int = Field(
            title="显示器索引",
            default=1,
            ge=0,
            description="默认显示器索引（1..N 物理显示器）；非法 → 后端回退并 warning",
        )
        # 默认区域 [x1, y1, x2, y2]（相对显示器左上角）；None = 全屏
        default_region: Optional[List[int]] = Field(
            title="默认区域",
            default=None,
            description="默认区域 [x1, y1, x2, y2]（相对显示器左上角）；None = 全屏",
        )
        # 默认缩放上限（像素宽）：调用方未传 max_width 时生效。全尺寸 1080p 截图
        # base64 约 1.8MB；1280 宽等比缩放省一半以上体积——进对话的图按引用注水，
        # 单价虽省，窗口内每轮重发与视觉编码成本仍随像素走
        default_max_width: int = Field(
            title="默认最大宽度",
            default=1280,
            ge=100,
            description="调用方未指定 max_width 时的等比缩放上限（像素）",
        )

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        screen_capture: Optional[ScreenCapture] = None,
        text_reader: Optional[TextReader] = None,
        llm_manager: Optional[ProfileVisualCapabilityLookup] = None,
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
        # 能力协商查询用：判定调用方主模型能否看图，能看则跳过 VLM。
        # 注入了缺方法的依赖时启动即报错，而不是运行期静默按非视觉处理
        validate_llm_capability(llm_manager, ProfileVisualCapabilityLookup, parameter="llm_manager")
        self._llm_manager = llm_manager

        self._call_count = 0

    def _caller_is_visual(self, invocation: ToolInvocation) -> bool:
        """调用方主模型能否看图（能力协商）。

        管线式调用（``caller_profile`` 空串，如 text_adv 读屏）按非视觉——
        它们消费的是 VLM 的结构化文本输出（选项中心像素坐标），跳过 VLM 会让
        解析管线断裂。未注入或查询异常同样按非视觉：判错方向是多一次
        VLM 转述，而不是管线拿到一份没有文字答案的图。注入了但缺方法的依赖
        在装配期已被校验拦下（见 __init__）。
        """
        if not invocation.caller_profile or self._llm_manager is None:
            return False
        try:
            return self._llm_manager.is_profile_visual(invocation.caller_profile) is True
        except Exception as exc:  # noqa: BLE001 - 能力查询失败退化为 VLM 照旧
            logger.warning(f"视觉能力查询失败（{invocation.caller_profile}），VLM 照旧: {exc}", exc=True)
            return False

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
            # 缺省走配置的缩放上限：进对话的图按引用注水，但窗口内每轮重发
            # 与视觉编码成本仍随像素走，默认 1280 而不是原始全尺寸
            max_width = self.typed_config.default_max_width

        # 采集后端不可用 → 优雅降级（不抛，返回成功 + 空文本 + error）
        if self._capture is None:
            latency_ms = int(time.time() * 1000) - started_ms
            return self._build_no_backend_result(
                tool_name=tool_name,
                started_ms=started_ms,
                latency_ms=latency_ms,
            )

        # 调用采集后端（捕获异常 → 失败 result，不抛）。
        # mss 抓屏是同步阻塞调用（实测 80–220ms/次），移入 worker 线程避免
        # 冻结事件循环；MssScreenCapture 每次调用自建 mss 实例，跨线程安全
        try:
            result = await asyncio.to_thread(
                self._capture.capture,
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

        # 可选 VLM 文本提取（异步：避免阻塞事件循环；reader 内部负责超时/降级）。
        # 视觉调用方跳过 VLM：主模型自己看图，省一次视觉调用与转述延迟
        text = ""
        vlm_error: Optional[str] = None
        if self._reader is not None and not self._caller_is_visual(invocation):
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
                        "测试场景下注入 Fake 替身（见 tests/modules/vision/fakes.py）即可。"
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


__all__ = [
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
    "LookAtScreenProvider",
    "LOOK_AT_SCREEN_SPEC",
    "build_look_at_screen_spec",
    "PROVIDER_NAME",
    "TOOL_NAME",
    "LlmVisionTextReader",
    "SCREEN_VLM_PROMPT_KEY",
    "SCREEN_VLM_SYSTEM_KEY",
]
