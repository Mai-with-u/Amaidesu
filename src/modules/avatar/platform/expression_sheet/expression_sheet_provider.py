"""
ExpressionSheetProvider - 表情差分图形象

静态立绘形象：17 个情绪各对应一张差分图，文件名 = 情绪枚举值（如 ``happy.png`` /
``speechless.png``），目录由 ``[avatar.platform.expression_sheet] image_dir`` 指定，
小部件页（``/widget/expression_sheet``）负责显示。无连接语义、无外部依赖。

- 显示哪张图跟着面捕合成器走：合成器此刻的主表情就是要显示的情绪——它已经处理好
  "语音开始播放才上脸、播完停一会儿再回落、同类只延长、强情绪才打断"，主播用
  ``avatar_act`` 的"保持表情"也直接生效。小部件页按 :meth:`ExpressionSheetProvider.current_image`
  取图。差分图只有一张张整图，表情的强弱、眨眼、口型都画不出来，所以不挂逐帧驱动。
- 不向 LLM 暴露工具：形象动作统一走 ``avatar_act``；``set_expression`` 等契约方法
  只给调试台用（设表情交给面捕合成器）。
- 降级可见性：``image_dir`` 未配置 / 目录无图时属"已登记未就绪"，
  ``connection_summary`` 报未连接（工具页显示"连接失败"而非"正常"），``last_error``
  携带出路提示。配置修改需重启生效（配置加载期读定，无运行期热更）。
- 情绪词汇唯一来源是 ``Emotion`` 枚举；情绪 → 图文件的解析只认扫描结果
  （png/jpg/jpeg/webp），对应图缺失时回退 ``neutral`` 差分图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.tool_common import dispatch_tool
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
    tool_result_fail,
    tool_result_ok,
)
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

# 差分图支持的图片扩展名（扫描目录与校验文件名共用；小写比较）
SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# 兜底差分图的情绪名（对应情绪枚举值；缺图时回退到它）
FALLBACK_EMOTION = Emotion.NEUTRAL.value

# 小部件页取图的静态路由前缀（dashboard 后端提供，见 dashboard/api/avatar.py）
ASSET_URL_PREFIX = "/api/v1/avatar/expression_sheet/assets"


class ExpressionSheetProvider(BaseToolProvider):
    """表情差分图形象 ToolProvider（静态立绘，表情跟着面捕合成器）"""

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
        tracking: Optional[Any] = None,
    ) -> None:
        """``tracking``：面捕合成器运行时（组合根共享一个）；None = 一直显示 neutral 差分图。

        event_bus / lipsync_analyzer 是统一装配签名：情绪由面捕合成器订阅，静态立绘也没有口型。
        """
        self.config = config
        self.event_bus = event_bus
        self.lipsync_analyzer = lipsync_analyzer
        self._tracking = tracking
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

    def connection_summary(self) -> Dict[str, Any]:
        """连接态观测摘要：无外部连接，就绪态（目录已配置且有图）即 connected。"""
        return {"is_connected": self.is_ready, "stats": {}}

    def list_tools(self) -> list[ToolSpec]:
        """不向 LLM 暴露工具：形象动作统一走 ``avatar_act``，表情由面捕合成器决定。"""
        return []

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """本适配器没有 LLM 工具：任何调用都按"工具不属于本 Provider"失败返回。"""
        return await dispatch_tool(invocation, self.PROVIDER_NAME, {}, self.logger)

    # ===== 小部件页取图 =====

    def current_image(self) -> Dict[str, Any]:
        """此刻该显示的差分图：``{emotion, image_url}``。

        情绪取面捕合成器此刻的主表情（没装配合成器时一直是 neutral）；缺这张图回退 neutral 差分图，
        neutral 也没有时 ``image_url`` 为 None（小部件页按未配置处理）。
        """
        emotion = self._tracking.current_emotion() if self._tracking is not None else FALLBACK_EMOTION
        filename = self._image_for(emotion)
        return {"emotion": emotion, "image_url": f"{ASSET_URL_PREFIX}/{filename}" if filename else None}

    def _image_for(self, emotion: str) -> Optional[str]:
        """情绪对应的差分图文件名；缺图回退 neutral，都没有为 None。"""
        return self._emotion_images.get(emotion) or self._emotion_images.get(FALLBACK_EMOTION)

    # ===== 契约方法（调试台直接调用）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：交给面捕合成器（浮现后保持配置的时长再回落），小部件页随之换图。

        校验顺序：情绪名合法性 → 目录就绪态 → 有图可显示（缺图回退 ``neutral``）→ 合成器已装配。
        成功返回 ``{emotion, intensity, image_url, outcome}``。
        """
        if emotion not in {e.value for e in Emotion}:
            return tool_result_fail("expression_sheet_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        if not self.is_ready:
            return tool_result_fail("expression_sheet_set_expression", self._not_ready_hint())
        filename = self._image_for(emotion)
        if filename is None:
            return tool_result_fail(
                "expression_sheet_set_expression",
                f"目录中缺少 {emotion} 的差分图，且无 {FALLBACK_EMOTION}.png 兜底"
                f"（目录：{self.image_dir}，已扫描到：{sorted(self._emotion_images.values())}）",
            )
        if self._tracking is None:
            return tool_result_fail(
                "expression_sheet_set_expression", "面捕合成器未启用（avatar.toml [tracking].enabled）"
            )
        factor = min(1.0, max(0.0, float(intensity)))
        outcome = self._tracking.express(emotion, factor)
        self.render_count += 1
        return tool_result_ok(
            "expression_sheet_set_expression",
            True,
            {
                "emotion": emotion,
                "intensity": factor,
                "image_url": f"{ASSET_URL_PREFIX}/{filename}",
                "outcome": outcome,
            },
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（静态立绘没有做好的动作，恒为空目录）。"""
        return tool_result_ok("expression_sheet_list_preset_actions", True, {"actions": []})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；本平台没有动作可触发，未知名随空目录一并返回（失败即发现）。"""
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
        if self._tracking is None:
            self.logger.warning("面捕合成器未装配：差分图会一直停在 neutral")
        self._has_started = True
        self.logger.info(
            f"{self.__class__.__name__} 已启动"
            + (f"（差分图 {len(self._emotion_images)} 张，目录：{self.image_dir}）" if self.is_ready else "（未就绪）")
        )

    async def cleanup(self) -> None:
        if not self._has_started:
            return
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


def create_expression_sheet_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    tracking: Optional[Any] = None,
) -> ExpressionSheetProvider:
    """构造 ExpressionSheetProvider 实例（不启动，由调用方 setup）"""
    return ExpressionSheetProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
        tracking=tracking,
    )


def register_expression_sheet_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
    tracking: Optional[Any] = None,
) -> ExpressionSheetProvider:
    """构造 ExpressionSheetProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    不登记 LLM 工具；目录未配置时工具页按未连接展示，配置就绪需重启装配。
    """
    provider = create_expression_sheet_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
        tracking=tracking,
    )
    registry.register_provider(provider)
    return provider
