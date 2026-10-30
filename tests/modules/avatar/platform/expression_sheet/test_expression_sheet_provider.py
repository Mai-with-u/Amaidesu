"""ExpressionSheetProvider：图映射/情绪校验/缺图回退/降级可见性/跟着面捕合成器换图"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import (
    ASSET_URL_PREFIX,
    ExpressionSheetProvider,
)
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.registry import ToolRegistry
from src.modules.types.emotion_vocab import Emotion

# PNG 文件头（只需让文件存在，路由/扫描不校验图片内容）
_PNG_BYTES = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def sheet_dir(tmp_path: Path) -> Path:
    """构造一个含 happy/speechless/neutral 三张差分图的目录。"""
    for name in ("happy", "speechless", "neutral"):
        (tmp_path / f"{name}.png").write_bytes(_PNG_BYTES)
    return tmp_path


class FakeTracking:
    """面捕合成器替身：只有差分图用得到的两件事——此刻的主表情、设一个表情。"""

    def __init__(self) -> None:
        self.emotion = Emotion.NEUTRAL.value
        self.expressed: list[tuple[str, float]] = []

    def current_emotion(self) -> str:
        return self.emotion

    def express(self, emotion: str, intensity: float) -> str:
        self.expressed.append((emotion, intensity))
        self.emotion = emotion
        return "shown"


def _provider(
    sheet_dir: Path, config: dict | None = None, tracking: FakeTracking | None = None
) -> ExpressionSheetProvider:
    return ExpressionSheetProvider(
        config=config if config is not None else {"image_dir": str(sheet_dir)}, tracking=tracking
    )


def test_connection_summary_mirrors_readiness(sheet_dir: Path) -> None:
    """观测面契约：就绪态即 connected；目录未配置时 False。"""
    assert _provider(sheet_dir).connection_summary() == {"is_connected": True, "stats": {}}
    unconfigured = _provider(sheet_dir, config={"image_dir": ""})
    assert unconfigured.connection_summary()["is_connected"] is False


def test_config_schema_is_default_constructible() -> None:
    schema = ExpressionSheetProvider.ConfigSchema()
    assert schema.image_dir == ""
    assert schema.type == "expression_sheet"


def test_unconfigured_dir_degrades_to_zero_tools_with_hint(sheet_dir: Path) -> None:
    provider = _provider(sheet_dir, config={})
    assert provider.image_dir is None
    assert not provider.is_ready
    assert provider.list_tools() == []
    assert "avatar.toml" in provider.last_error and "image_dir" in provider.last_error


def test_empty_dir_degrades_and_lists_scan_findings(tmp_path: Path) -> None:
    provider = _provider(tmp_path)  # 目录存在但一张图都没有
    assert not provider.is_ready
    assert provider.list_tools() == []
    assert "没有可用的差分图" in provider.last_error


def test_ready_provider_exposes_no_llm_tools(sheet_dir: Path) -> None:
    # 形象动作统一走 avatar_act，差分图跟着面捕合成器换
    assert _provider(sheet_dir).list_tools() == []


async def test_set_expression_rejects_unknown_emotion(sheet_dir: Path) -> None:
    provider = _provider(sheet_dir, tracking=FakeTracking())
    result = await provider.set_expression("ecstatic", 0.5)
    assert isinstance(result, ToolExecutionResult)
    assert result.success is False
    assert "未知情绪" in (result.error_message or "")


async def test_set_expression_goes_through_tracking_and_shows_on_the_widget(sheet_dir: Path) -> None:
    tracking = FakeTracking()
    provider = _provider(sheet_dir, tracking=tracking)
    result = await provider.set_expression(Emotion.HAPPY.value, 1.2)
    assert result.success is True
    assert tracking.expressed == [("happy", 1.0)]  # 越界强度被钳制到 1.0
    assert result.structured_content["image_url"] == f"{ASSET_URL_PREFIX}/happy.png"
    assert provider.current_image() == {"emotion": "happy", "image_url": f"{ASSET_URL_PREFIX}/happy.png"}


def test_current_image_follows_tracking_and_falls_back_to_neutral(sheet_dir: Path) -> None:
    tracking = FakeTracking()
    provider = _provider(sheet_dir, tracking=tracking)
    assert provider.current_image()["image_url"] == f"{ASSET_URL_PREFIX}/neutral.png"
    tracking.emotion = Emotion.ANGRY.value  # 目录里没有 angry.png
    assert provider.current_image() == {"emotion": "angry", "image_url": f"{ASSET_URL_PREFIX}/neutral.png"}
    tracking.emotion = Emotion.SPEECHLESS.value
    assert provider.current_image()["image_url"] == f"{ASSET_URL_PREFIX}/speechless.png"


def test_without_tracking_the_sheet_stays_neutral(sheet_dir: Path) -> None:
    assert _provider(sheet_dir).current_image()["emotion"] == "neutral"


async def test_set_expression_fails_when_both_emotion_and_neutral_missing(tmp_path: Path) -> None:
    (tmp_path / "happy.png").write_bytes(_PNG_BYTES)
    provider = _provider(tmp_path, tracking=FakeTracking())  # 有图但无 neutral 兜底
    result = await provider.set_expression(Emotion.SAD.value, 0.5)
    assert result.success is False
    assert "neutral" in (result.error_message or "")
    assert provider.current_image()["image_url"] is None


async def test_set_expression_fails_without_tracking(sheet_dir: Path) -> None:
    result = await _provider(sheet_dir).set_expression(Emotion.HAPPY.value, 0.5)
    assert result.success is False and "面捕合成器" in (result.error_message or "")


async def test_set_expression_fails_with_hint_when_not_ready(sheet_dir: Path) -> None:
    provider = _provider(sheet_dir, config={})
    result = await provider.set_expression(Emotion.HAPPY.value, 0.5)
    assert result.success is False
    assert "image_dir" in (result.error_message or "")


async def test_preset_action_surface_is_empty_directory(sheet_dir: Path) -> None:
    provider = _provider(sheet_dir)
    listed = await provider.list_preset_actions()
    assert listed.success is True
    assert listed.structured_content == {"actions": []}

    triggered = await provider.trigger_preset_action("wave")
    assert triggered.success is False
    assert triggered.structured_content == {"available_actions": []}


async def test_invoke_rejects_every_tool_name(sheet_dir: Path) -> None:
    from src.modules.tools.models import ToolInvocation

    provider = _provider(sheet_dir, tracking=FakeTracking())
    result = await provider.invoke(
        ToolInvocation(tool_name="expression_sheet_set_expression", arguments={"emotion": "happy"})
    )
    assert result.success is False


def test_register_tools_into_registry(sheet_dir: Path) -> None:
    from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import (
        register_expression_sheet_tools,
    )

    registry = ToolRegistry()
    provider = register_expression_sheet_tools(registry=registry, config={"image_dir": str(sheet_dir)})
    assert provider.name == "expression_sheet"
    assert len(registry) == 0 and provider in registry.providers()
