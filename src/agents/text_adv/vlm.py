"""text_adv 识别契约 —— question 模板渲染、VLM 回复结构化解析、读屏 reader。

职责：
- ``build_question``：从 PromptManager 模板渲染给 ``vision_look_at_screen`` 的
  question，要求 VLM 按固定可解析格式回复（正文 + 带中心像素坐标的选项列表，
  坐标位于发送给 VLM 的那张图的像素空间）
- ``parse_reply``：把 VLM 回复解析为结构化 :class:`ScreenReading`；对格式容错
  （全角/半角、多余空格、缺少末尾项），整体无法解析时返回失败标志为真的结果
  而非抛异常——调用方据此拒绝 choose，绝不拿到半个结果或猜测的坐标
- ``VisionReader`` 协议 + ``RegistryVisionReader``（经 ToolRegistry 调用
  ``vision_look_at_screen``）+ ``FakeVisionReader``（测试假件，可注入固定回复）

被消费工具 ``vision_look_at_screen`` 的契约只消费不修改：``question`` 限
1..500 字符；``structured_content["text"]`` 是识别文本硬约束键；``width`` 是
按 max_width 等比缩放后真实发送图像的宽度（坐标有效性校验以此为界）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager, get_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.vision.look_at_screen import PROVIDER_NAME, TOOL_NAME

logger = get_logger("text_adv_vlm")

# PromptManager 模板键（对应 src/agents/text_adv/prompts/text_adv_vlm_question.md 的 frontmatter name）
TEXT_ADV_VLM_QUESTION_KEY = "text_adv_vlm_question"

# 注册到 ToolRegistry 的全名（vision_look_at_screen）
VISION_TOOL_NAME = f"{PROVIDER_NAME}_{TOOL_NAME}"

# want_options=True 时注入模板的选项段引导（要求标签 + 中心像素坐标）
_OPTIONS_DIRECTIVE = (
    "选项：\n"
    "<若有选项，每行一项，格式：序号. 标签 (中心x, 中心y)。"
    '坐标为选项在这张图里的中心像素位置；看不清坐标的选项只写"序号. 标签">'
)

# want_options=False 时注入模板的说明
_NO_OPTIONS_DIRECTIVE = "无需列出选项。"

# 模板渲染失败时的内置兜底文案（与模板正文同构，保证契约不因模板缺失而断裂）
_FALLBACK_TEMPLATE = (
    "请识别这张视觉小说游戏画面，并严格按以下固定格式回复，不要添加任何其他内容：\n"
    "正文：\n"
    "<画面正文的全部文字，按原始换行>\n"
    "{options_directive}"
)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class Option(BaseModel):
    """单个可交互选项。

    Attributes:
        label: 选项文字标签
        vlm_xy: 选项中心在发送图像像素空间里的坐标；None = VLM 未给出可解析
            坐标（或坐标越出图像范围）——绝不猜测
    """

    label: str = ""
    vlm_xy: Optional[Tuple[int, int]] = None

    @property
    def clickable(self) -> bool:
        """是否可点击（坐标可用才可点；缺坐标一律不可点）。"""
        return self.vlm_xy is not None


class ScreenReading(BaseModel):
    """一次读屏的结构化结果。

    Attributes:
        text: 画面正文文字（解析成功时为契约「正文」段；失败时可能为空）
        options: 解析出的选项列表（可能为空）
        parse_ok: 解析是否成功；False 时调用方必须把本次读屏视为不可用
        parse_error: 解析失败原因（parse_ok=True 时为 None）
        raw_text: VLM 原始回复文本（未加工，供排查与审计）
        sent_width: 实际发送给 VLM 的图像宽度（像素，按 max_width 缩放后）；
            0 = 未知。vlm_xy 的像素空间即这张图，坐标反算与有效性校验都以此为界
    """

    text: str = ""
    options: List[Option] = Field(default_factory=list)
    parse_ok: bool = True
    parse_error: Optional[str] = None
    raw_text: str = ""
    sent_width: int = 0


# ---------------------------------------------------------------------------
# question 构造
# ---------------------------------------------------------------------------


def _render_question_template(options_directive: str) -> str:
    """渲染 question 模板；模板缺失/渲染失败时降级到内置兜底文案。"""
    try:
        rendered = get_prompt_manager().render(TEXT_ADV_VLM_QUESTION_KEY, options_directive=options_directive)
    except Exception as exc:  # noqa: BLE001 - 模板缺失/变量不匹配时降级
        logger.warning(f"渲染模板 {TEXT_ADV_VLM_QUESTION_KEY} 失败: {exc}; 改用内置兜底文案")
        return _FALLBACK_TEMPLATE.format(options_directive=options_directive)
    if not rendered or not rendered.strip():
        logger.warning(f"模板 {TEXT_ADV_VLM_QUESTION_KEY} 渲染为空; 改用内置兜底文案")
        return _FALLBACK_TEMPLATE.format(options_directive=options_directive)
    return rendered


def build_question(*, want_options: bool) -> str:
    """构造给 ``vision_look_at_screen`` 的 question。

    要求 VLM 按固定可解析格式回复：正文 + （可选）选项列表，每个选项带标签与
    中心像素坐标，坐标位于发送给 VLM 的那张图的像素空间里。

    Args:
        want_options: 是否要求 VLM 列出选项（纯叙事屏传 False 可省 token）

    Returns:
        完整的 question 文本，保留识别要求与选项坐标说明
    """
    directive = _OPTIONS_DIRECTIVE if want_options else _NO_OPTIONS_DIRECTIVE
    question = _render_question_template(directive).strip()
    return question


# ---------------------------------------------------------------------------
# 回复解析
# ---------------------------------------------------------------------------

# 全角字符 → 半角的翻译表（数字 / 括号 / 冒号 / 逗号 / 顿号）
_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９（）｛｝：，、",
    "0123456789(){}:,、",
)

# 选项坐标：半角括号包裹的两个非负整数（归一化后匹配）
_COORD_RE = re.compile(r"\((\d+)\s*,\s*(\d+)\)")

# 选项行首的序号前缀（"1." / "1、" / "1)" / "1)" 等）
_NUM_PREFIX_RE = re.compile(r"^\s*\d+\s*[.\u3001)\]]?\s*")

# 标记行："正文" / "选项"（可带冒号，允许首尾空白）
_BODY_MARKER_RE = re.compile(r"^\s*正文\s*[:：]?\s*$")
_OPTIONS_MARKER_RE = re.compile(r"^\s*选项\s*[:：]?\s*$")

# 选项标签首尾的分隔残留（序号点、括号、顿号、空白等）
_LABEL_TRIM_RE = re.compile(r"^[\s\-.·、)\]]+|[\s\-.·、)\]]+$")


def _find_marker(lines: List[str], pattern: "re.Pattern[str]") -> Optional[int]:
    """返回首个匹配标记行的下标；无则 None。"""
    for idx, line in enumerate(lines):
        if pattern.match(line):
            return idx
    return None


def _coord_valid(x: int, y: int, sent_width: int) -> bool:
    """坐标是否落在发送图像的像素空间内（越界坐标视为无效，不猜测修正）。"""
    if x < 0 or y < 0:
        return False
    if sent_width > 0 and x >= sent_width:
        return False
    return True


def parse_reply(text: str, *, sent_width: int) -> ScreenReading:
    """把 VLM 回复解析为结构化 :class:`ScreenReading`。

    容错面：全角/半角标点与数字、多余空格、缺少末尾项。无法解析出坐标的选项
    标为不可点（``vlm_xy=None``）；整体无法按契约解析（空回复 / 缺标记 /
    选项段为空）时返回 ``parse_ok=False`` 的结果，不抛异常。

    Args:
        text: VLM 原始回复文本
        sent_width: 实际发送图像宽度（像素）；x 越出该宽度的坐标视为无效。
            0 表示宽度未知，此时只做非负校验

    Returns:
        结构化读屏结果；失败形态一定是 ``parse_ok=False`` 的完整对象
    """
    raw = text or ""
    if not raw.strip():
        return ScreenReading(parse_ok=False, parse_error="空回复（VLM 可能失败或超时）", raw_text=raw)

    normalized = raw.translate(_FULLWIDTH_TRANS)
    lines = raw.splitlines()
    norm_lines = normalized.splitlines()
    body_idx = _find_marker(norm_lines, _BODY_MARKER_RE)
    options_idx = _find_marker(norm_lines, _OPTIONS_MARKER_RE)

    if body_idx is None:
        return ScreenReading(
            parse_ok=False,
            parse_error="缺少「正文」标记，无法按契约解析",
            raw_text=raw,
            sent_width=sent_width,
        )
    if options_idx is not None and options_idx <= body_idx:
        return ScreenReading(
            parse_ok=False,
            parse_error="标记顺序异常（「选项」出现在「正文」之前）",
            raw_text=raw,
            sent_width=sent_width,
        )

    body_end = options_idx if options_idx is not None else len(lines)
    body_text = "\n".join(line.rstrip() for line in lines[body_idx + 1 : body_end]).strip()

    options: List[Option] = []
    parse_error: Optional[str] = None
    if options_idx is not None:
        for line in norm_lines[options_idx + 1 :]:
            stripped = line.strip()
            if not stripped:
                continue
            coord_match = _COORD_RE.search(stripped)
            label = _COORD_RE.sub("", stripped)
            label = _NUM_PREFIX_RE.sub("", label, count=1)
            label = _LABEL_TRIM_RE.sub("", label).strip()
            vlm_xy: Optional[Tuple[int, int]] = None
            if coord_match is not None:
                x, y = int(coord_match.group(1)), int(coord_match.group(2))
                if _coord_valid(x, y, sent_width):
                    vlm_xy = (x, y)
            if not label and vlm_xy is None:
                # 既无标签也无有效坐标的行不构成选项
                continue
            options.append(Option(label=label, vlm_xy=vlm_xy))
        if not options:
            parse_error = "存在「选项」段但未解析出任何选项"

    return ScreenReading(
        text=body_text,
        options=options,
        parse_ok=parse_error is None,
        parse_error=parse_error,
        raw_text=raw,
        sent_width=sent_width,
    )


# ---------------------------------------------------------------------------
# reader 协议与实现
# ---------------------------------------------------------------------------


class VisionReader(Protocol):
    """读屏协议（异步）：读一屏并返回结构化结果。"""

    async def read_screen(self, *, want_options: bool) -> ScreenReading:
        """读当前屏幕。

        Args:
            want_options: 是否要求 VLM 列出选项

        Returns:
            结构化读屏结果；失败形态为 ``parse_ok=False``，不抛异常
        """
        ...


class VisionToolRegistry(Protocol):
    """读屏所需的注册表最小面：按调用单调用工具并返回执行结果。"""

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行一次工具调用（失败兜底为失败结果，不抛异常）。"""
        ...


class RegistryVisionReader:
    """经 ToolRegistry 调用 ``vision_look_at_screen`` 的 VisionReader 实现。

    与仓内 text_adv Agent 的感知调用先例一致：构造注入 ToolRegistry，调用时
    携带完整的 question 并读取原始分辨率画面；从 structured_content 取识别文本
    （硬约束键）与实际发送宽度（缩放后图像宽，供坐标校验与反算）。
    """

    def __init__(
        self,
        tool_registry: VisionToolRegistry,
        *,
        source: str = "text_adv",
        prompt_manager: Optional[PromptManager] = None,
    ) -> None:
        """构造注入注册表与调用参数。

        Args:
            tool_registry: ToolRegistry 实例（经它调用 vision_look_at_screen）
            source: ToolInvocation 的 source 标识（调用方 Agent 名）
            prompt_manager: 可选 PromptManager（不传则用全局单例渲染模板）
        """
        self._tool_registry = tool_registry
        self._source = source
        self._prompt_manager = prompt_manager

    def _build_question(self, *, want_options: bool) -> str:
        """渲染 question（注入了 prompt_manager 则走它，否则走全局单例）。"""
        directive = _OPTIONS_DIRECTIVE if want_options else _NO_OPTIONS_DIRECTIVE
        if self._prompt_manager is not None:
            try:
                question = self._prompt_manager.render(TEXT_ADV_VLM_QUESTION_KEY, options_directive=directive).strip()
            except Exception as exc:  # noqa: BLE001 - 与全局单例路径同构降级
                logger.warning(f"渲染模板 {TEXT_ADV_VLM_QUESTION_KEY} 失败: {exc}; 改用内置兜底文案")
                question = _FALLBACK_TEMPLATE.format(options_directive=directive).strip()
        else:
            question = build_question(want_options=want_options)
        return question

    async def read_screen(self, *, want_options: bool) -> ScreenReading:
        """调一次 vision_look_at_screen 并解析回复；失败返回可判定结果。"""
        question = self._build_question(want_options=want_options)
        invocation = ToolInvocation(
            tool_name=VISION_TOOL_NAME,
            arguments={"question": question},
            source=self._source,
        )
        result = await self._tool_registry.invoke(invocation)
        if not result.success:
            logger.warning(f"vision_look_at_screen 调用失败: {result.error_message}")
            return ScreenReading(
                parse_ok=False,
                parse_error=f"工具调用失败: {result.error_message}",
                sent_width=0,
            )
        structured: Dict[str, Any] = result.structured_content or {}
        raw = str(structured.get("text") or "")
        sent_width = int(structured.get("width") or 0)
        reading = parse_reply(raw, sent_width=sent_width)
        error = structured.get("error")
        if error:
            # 感知降级（capture 失败 / VLM 超时）：text 为空必然解析失败，补上原因
            reading.parse_ok = False
            reading.parse_error = f"感知降级: {error}"
        return reading


class FakeVisionReader:
    """测试用 VisionReader 假件：可注入固定 reply 文本，按真契约解析返回。"""

    def __init__(self, reply: str, *, sent_width: int = 1280) -> None:
        """注入固定回复与发送宽度（宽度参与坐标有效性校验）。

        Args:
            reply: 每次 read_screen 返回并解析的 VLM 原始回复文本
            sent_width: 模拟的实际发送图像宽度（像素）
        """
        self._reply = reply
        self._queue: List[str] = []
        self.sent_width = int(sent_width)
        self.calls: List[Dict[str, Any]] = []

    def queue_reply(self, reply: str) -> None:
        """入队一次性回复（优先于固定 reply 被消费，用于多屏序列）。"""
        self._queue.append(reply)

    async def read_screen(self, *, want_options: bool) -> ScreenReading:
        """返回按契约解析后的注入回复，并记录调用（含 question）。"""
        question = build_question(want_options=want_options)
        self.calls.append({"question": question, "want_options": want_options})
        reply = self._queue.pop(0) if self._queue else self._reply
        return parse_reply(reply, sent_width=self.sent_width)


__all__ = [
    "Option",
    "ScreenReading",
    "VisionReader",
    "RegistryVisionReader",
    "FakeVisionReader",
    "build_question",
    "parse_reply",
    "TEXT_ADV_VLM_QUESTION_KEY",
    "VISION_TOOL_NAME",
]
