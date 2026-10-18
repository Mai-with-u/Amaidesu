"""
ExpressionSheetProvider - 表情差分图虚拟形象工具集

基于"表情差分图切换"的静态立绘虚拟形象：17 个情绪各对应一张差分图，
文件名 = 情绪枚举值（如 ``happy.png`` / ``speechless.png``），图片目录由
配置 ``[avatar.platform.expression_sheet] image_dir`` 指定。设置情绪 =
切换到对应差分图，由挂件页（``/widget/expression_sheet``）取
``image_url`` 展示；无连接语义、无外部依赖。

- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``expression_sheet_set_expression``        - 设置情绪（切换差分图）
  - ``expression_sheet_list_preset_actions``   - 列出可演预设（当前为空目录，动作面后补）
  - ``expression_sheet_trigger_preset_action`` - 触发预设动作（当前无动作可触发）

- 降级可见性：``image_dir`` 未配置 / 目录无图时属"已登记未就绪"，
  ``list_tools`` 返回空（装配期降级登记 0 工具，Agent 工具表不可见，
  WebUI 工具页显示"连接失败"标签而非"正常"），``last_error`` 携带出路
  提示。配置修改需重启生效（配置加载期读定，无运行期热更）。

- 情绪词汇唯一来源是 ``Emotion`` 枚举；情绪 → 图文件的解析只认扫描
  结果（png/jpg/jpeg/webp），对应图缺失时回退 ``neutral`` 差分图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import bind_speech_emotion
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

# 差分图支持的图片扩展名（扫描目录与校验文件名共用；小写比较）
SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# 兜底差分图的情绪名（对应情绪枚举值；缺图时回退到它）
FALLBACK_EMOTION = Emotion.NEUTRAL.value

# 挂件页取图的静态路由前缀（dashboard 后端提供，见 dashboard/api/avatar.py）
ASSET_URL_PREFIX = "/api/v1/avatar/expression_sheet/assets"

_EXPRESSION_SHEET_SET_EXPRESSION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "emotion": {
            "type": "string",
            "enum": [e.value for e in Emotion],
            "description": "情绪（17 枚举值之一，小写）",
        },
        "intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "description": "情绪强度（0.0–1.0）",
        },
    },
    "required": ["emotion"],
}

_EXPRESSION_SHEET_TRIGGER_PRESET_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": ("预设动作名（取自 expression_sheet_list_preset_actions 返回的目录）"),
        },
    },
    "required": ["action"],
}


class ExpressionSheetProvider(BaseToolProvider):
    """表情差分图虚拟形象 ToolProvider（静态立绘）"""

    PROVIDER_NAME = "expression_sheet"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    class ConfigSchema(BaseConfig):
        """表情差分图配置（差分图目录）

        TOML 段位：[avatar.platform.expression_sheet]（成员段直接铺键）
        """

        type: str = Field(
            title="段位标识",
            default="expression_sheet",
            json_schema_extra={"readonly": True},
        )
        image_dir: str = Field(
            title="差分图片目录",
            default="",
            description=(
                "差分图目录的绝对路径；目录内存放 17 个情绪的差分图，"
                "文件名 = 情绪枚举值（如 happy.png），支持 png/jpg/jpeg/webp；"
                "空串 = 未配置"
            ),
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
    ) -> None:
        # lipsync_analyzer：统一装配签名；静态立绘无口型渲染，不接口型
        self.config = config
        self.event_bus = event_bus
        self.lipsync_analyzer = lipsync_analyzer
        self.logger = get_logger(self.__class__.__name__)

        # Provider 侧最近一次未就绪原因摘要（工具页 last_error 数据源；就绪为空串）
        self.last_error = ""

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        # 已解析的差分图目录（None = 未配置；构造期读定，配置修改需重启生效）
        self.image_dir: Optional[Path] = self._resolve_image_dir()
        # 扫描结果：情绪枚举值 → 目录内实际文件名（大小写保留，用于拼 image_url）
        self._emotion_images: Dict[str, str] = self._scan_emotion_images()

        # 可变实例状态在 __init__ 初始化
        self._has_started = False
        self._speech_emotion_handler: Optional[Any] = None
        self.render_count = 0
        self.error_count = 0

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def is_ready(self) -> bool:
        """平台就绪态：目录已配置且至少扫描到一张差分图。"""
        return self.image_dir is not None and len(self._emotion_images) > 0

    def list_tools(self) -> list[ToolSpec]:
        """声明本 Provider 暴露的工具列表（随就绪态动态）。

        未就绪（目录未配置 / 无图）返回空（降级登记，Agent 工具表不含
        expression_sheet_*，WebUI 工具页按 0 工具降级展示，不误显"正常"）。
        """
        if not self.is_ready:
            return []
        return [
            ToolSpec(
                name="set_expression",
                description="表情差分形象切换主播当前情绪差分图（17 枚举值 + 强度，持续生效直至下次设置）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_EXPRESSION_SHEET_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="list_preset_actions",
                description="列出表情差分形象可演的预设动作目录（当前为空目录，动作面后补）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="trigger_preset_action",
                description="触发一个预设动作（动作名取自 expression_sheet_list_preset_actions 返回的目录）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_EXPRESSION_SHEET_TRIGGER_PRESET_ACTION_SCHEMA,
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "expression_sheet_set_expression":
                return await self.set_expression(str(args.get("emotion", "")), float(args.get("intensity", 0.5)))
            if n == "expression_sheet_list_preset_actions":
                return await self.list_preset_actions()
            if n == "expression_sheet_trigger_preset_action":
                return await self.trigger_preset_action(str(args.get("action", "")))
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.exception(f"表情差分工具 {invocation.tool_name} 调用异常: {exc}")
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """切换到情绪对应的差分图。

        校验顺序：情绪名合法性 → 目录就绪态 → 图文件存在（缺图回退
        ``neutral`` 差分图）。成功返回 ``{emotion, intensity, image_url}``，
        挂件页按 ``image_url`` 取图展示。
        """
        if emotion not in {e.value for e in Emotion}:
            return _fail("expression_sheet_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        if not self.is_ready:
            return _fail("expression_sheet_set_expression", self._not_ready_hint())
        factor = min(1.0, max(0.0, float(intensity)))
        filename = self._emotion_images.get(emotion)
        if filename is None:
            filename = self._emotion_images.get(FALLBACK_EMOTION)
            if filename is None:
                return _fail(
                    "expression_sheet_set_expression",
                    f"目录中缺少 {emotion} 的差分图，且无 {FALLBACK_EMOTION}.png 兜底"
                    f"（目录：{self.image_dir}，已扫描到：{sorted(self._emotion_images.values())}）",
                )
            self.logger.debug(f"情绪 '{emotion}' 缺差分图，回退 {filename}")
        self.render_count += 1
        return _ok(
            "expression_sheet_set_expression",
            True,
            {
                "emotion": emotion,
                "intensity": factor,
                "image_url": f"{ASSET_URL_PREFIX}/{filename}",
            },
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（当前为空目录：静态立绘无动作面，后补）。"""
        return _ok("expression_sheet_list_preset_actions", True, {"actions": []})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；当前无动作可触发，未知名随空目录一并返回（失败即发现）。"""
        return ToolExecutionResult(
            tool_name="expression_sheet_trigger_preset_action",
            success=False,
            error_message=f"未知预设动作 '{action}'（本平台暂无预设动作）",
            structured_content={"available_actions": []},
            content=str([]),
        )

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        if not self.is_ready:
            self.logger.warning(self._not_ready_hint())
        # 被动半事件订阅：情绪反射（streamer.speech）。未就绪时订阅保持——
        # 反射路径调用 set_expression 会拿到失败结果，配置就绪重启后无需改装配
        self._speech_emotion_handler = bind_speech_emotion(self.event_bus, self, self.logger)
        self._has_started = True
        self.logger.info(
            f"{self.__class__.__name__} 已启动"
            + (f"（差分图 {len(self._emotion_images)} 张，目录：{self.image_dir}）" if self.is_ready else "（未就绪）")
        )

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        if self.event_bus is not None and self._speech_emotion_handler is not None:
            try:
                self.event_bus.off(CoreEvents.STREAMER_SPEECH, self._speech_emotion_handler)
            except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                self.logger.debug(f"streamer.speech 退订失败（已忽略）: {exc}")
            self._speech_emotion_handler = None
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 内部辅助 =====

    def _resolve_image_dir(self) -> Optional[Path]:
        """解析差分图目录配置（空串 = 未配置，返回 None）。"""
        raw = str(self.typed_config.image_dir or "").strip()
        if not raw:
            self.last_error = (
                "差分图片目录未配置：请在 avatar.toml [avatar.platform.expression_sheet] "
                "填写 image_dir（差分图目录绝对路径），重启后生效"
            )
            return None
        return Path(raw)

    def _scan_emotion_images(self) -> Dict[str, str]:
        """扫描差分图目录，构建 情绪枚举值 → 文件名 映射。

        只认文件名 = 情绪枚举值（如 happy.png）的图片；目录未配置 / 不存在
        / 一张图都没有时记录实况日志并返回空映射（就绪态由 ``is_ready`` 判定）。
        """
        if self.image_dir is None:
            return {}
        if not self.image_dir.is_dir():
            self.last_error = (
                f"差分图片目录不存在：{self.image_dir}。"
                "请检查 avatar.toml [avatar.platform.expression_sheet] image_dir 路径，重启后生效"
            )
            return {}
        found: Dict[str, str] = {}
        for entry in self.image_dir.iterdir():
            if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                continue
            stem = entry.stem.lower()
            if stem in {e.value for e in Emotion}:
                found[stem] = entry.name
        if not found:
            self.last_error = (
                f"差分图片目录中没有可用的差分图：{self.image_dir}。"
                "请放入 17 个情绪的差分图（文件名 = 情绪枚举值，如 happy.png，"
                "支持 png/jpg/jpeg/webp），重启后生效"
            )
            self.logger.warning(
                f"差分图片目录扫描为空：{self.image_dir}（支持的扩展名："
                f"{sorted(SUPPORTED_IMAGE_EXTS)}，文件名需为情绪枚举值）"
            )
        else:
            missing = sorted({e.value for e in Emotion} - set(found))
            if missing:
                self.logger.info(f"差分图缺 {missing}（运行期将回退 {FALLBACK_EMOTION}）：目录 {self.image_dir}")
        return found

    def _not_ready_hint(self) -> str:
        """未就绪的失败结果文案（含出路提示）。"""
        return (
            f"表情差分形象未就绪：{self.last_error or '差分图片目录未就绪'}。"
            "请配置 avatar.toml [avatar.platform.expression_sheet] image_dir 后重启"
        )


def _ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def _fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


def create_expression_sheet_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> ExpressionSheetProvider:
    """构造 ExpressionSheetProvider 实例（不启动，由调用方 setup）"""
    return ExpressionSheetProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )


def register_expression_sheet_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> ExpressionSheetProvider:
    """构造 ExpressionSheetProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    目录未配置时降级登记 0 工具（工具页按降级展示），配置就绪需重启装配。
    """
    provider = create_expression_sheet_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )
    registry.register_provider(provider)
    return provider
