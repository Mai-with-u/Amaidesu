"""vision_look_at_screen：工具规格、配置、入参透传、失败降级（不抛）、块组装与图片生命周期、按调用方视觉能力决定是否跑 VLM"""

from __future__ import annotations

import asyncio
import base64
import inspect
import threading
from unittest.mock import MagicMock

import pytest

from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.vision.look_at_screen import (
    LOOK_AT_SCREEN_SPEC,
    PROVIDER_NAME,
    TOOL_NAME,
    LookAtScreenProvider,
)
from src.modules.vision.mss_capture import MssScreenCapture
from src.modules.vision.vlm_reader import (
    SCREEN_VLM_PROMPT_KEY,
    SCREEN_VLM_SYSTEM_KEY,
    LlmVisionTextReader,
)
from tests.modules.vision.fakes import FakeScreenCapture, FakeTextReader

FULL_NAME = "vision_look_at_screen"


def _setup(config=None, *, text="x", width=320, height=240, png=b"\x89PNG_FAKE", **kwargs):
    capture = FakeScreenCapture()
    capture.queue_png(png, width=width, height=height)
    reader = FakeTextReader()
    reader.queue_text(text)
    provider = LookAtScreenProvider(config=config or {}, screen_capture=capture, text_reader=reader, **kwargs)
    return provider, capture, reader


async def _look(provider: LookAtScreenProvider, caller_profile: str | None = None, **arguments):
    extra = {} if caller_profile is None else {"caller_profile": caller_profile}
    return await provider.invoke(ToolInvocation(tool_name=FULL_NAME, arguments=arguments, source="test", **extra))


# 规格与配置


def test_spec_is_stable_and_every_argument_is_optional() -> None:
    assert (TOOL_NAME, PROVIDER_NAME, LOOK_AT_SCREEN_SPEC.full_name) == (
        "look_at_screen",
        "vision",
        FULL_NAME,
    )  # 兼容锚点
    assert LOOK_AT_SCREEN_SPEC.kind == "sync" and LOOK_AT_SCREEN_SPEC.provider == PROVIDER_NAME
    desc = LOOK_AT_SCREEN_SPEC.description
    assert ("需要知道" in desc or "调用" in desc) and "屏幕" in desc and ("不抛" in desc or "降级" in desc)

    schema = LOOK_AT_SCREEN_SPEC.parameters_schema or {}
    props = schema["properties"]
    assert schema.get("required") == [] and set(props) == {"question", "region", "monitor_index", "max_width"}
    assert (
        props["question"]["type"] == "string"
        and props["question"]["minLength"] == 1
        and "maxLength" not in props["question"]
    )
    assert props["region"] == {
        **props["region"],
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 4,
        "maxItems": 4,
    }
    assert (props["monitor_index"]["type"], props["monitor_index"]["minimum"]) == ("integer", 0)
    assert (props["max_width"]["minimum"], props["max_width"]["maximum"]) == (100, 3840)
    output = (LOOK_AT_SCREEN_SPEC.output_schema or {})["properties"]
    assert {"text", "image", "error", "width", "height", "monitor_index", "region", "latency_ms"} <= set(output)


def test_registration_name_template_keys_and_mss_signature() -> None:
    registry = ToolRegistry()
    registry.register_provider(
        LookAtScreenProvider(config={}, screen_capture=FakeScreenCapture(), text_reader=FakeTextReader())
    )
    assert registry.has(FULL_NAME)  # 兼容 text_adv 的硬编码调用
    assert (SCREEN_VLM_PROMPT_KEY, SCREEN_VLM_SYSTEM_KEY) == ("screen_vlm_prompt", "screen_vlm_system")
    params = list(inspect.signature(MssScreenCapture.capture).parameters)  # 生产实现与 ScreenCapture 协议形参一致
    assert params[:4] == ["self", "monitor_index", "region", "max_width"]


async def test_config_defaults_overrides_and_default_max_width_applies() -> None:
    cfg = LookAtScreenProvider.ConfigSchema.from_dict({})
    assert (cfg.monitor_index, cfg.default_region, cfg.default_max_width) == (
        1,
        None,
        1280,
    )  # 图进对话，默认降采样控传输成本
    fields = LookAtScreenProvider.ConfigSchema.model_fields
    assert "vlm_timeout_ms" not in fields and "vlm_timeout_ms" not in cfg.model_dump()  # 已删除的 VLM 时限不得复活
    custom = LookAtScreenProvider.ConfigSchema.from_dict(
        {"monitor_index": 2, "default_region": [10, 20, 110, 120], "unknown_field": "x"}
    )
    assert (custom.monitor_index, custom.default_region) == (2, [10, 20, 110, 120]) and not hasattr(
        custom, "unknown_field"
    )

    provider, capture, _ = _setup({"default_max_width": 800}, width=2560, height=1440)
    await _look(provider)
    assert capture.calls[-1]["max_width"] == 800  # 调用方未传 max_width 时用配置默认

    reader = LlmVisionTextReader(llm_manager=object())
    LookAtScreenProvider(config={}, text_reader=reader)
    assert not hasattr(reader, "_timeout_s")  # 装配时不再给 reader 写入时钟截止配置


async def test_capture_runs_off_the_event_loop_thread() -> None:
    provider, capture, _ = _setup(width=64, height=48)
    threads: list[int] = []
    original = capture.capture

    def probe(**kwargs: object) -> object:
        threads.append(threading.get_ident())
        return original(**kwargs)

    capture.capture = probe  # type: ignore[method-assign]
    await _look(provider)
    assert (
        len(threads) == 1 and threads[0] != threading.get_ident()
    )  # 抓屏同步调用移入 worker 线程，截图期间事件循环不冻结


# 入参


async def test_zero_arg_call_succeeds_with_text_and_uses_provider_defaults() -> None:
    config = {"monitor_index": 2, "default_max_width": 640, "default_region": [10, 10, 100, 100]}
    provider, capture, reader = _setup(config, text="hello screen", width=800, height=600)
    result = await _look(provider)
    assert result.success is True and result.structured_content["text"] == "hello screen"  # text_adv 零参兼容锚点
    assert capture.calls[0] == {**capture.calls[0], "monitor_index": 2, "region": (10, 10, 100, 100), "max_width": 640}


@pytest.mark.parametrize(
    ("arguments", "target", "key", "expected"),
    [
        ({"question": "屏幕上有几个选项？"}, "reader", "question", "屏幕上有几个选项？"),
        ({"question": "   "}, "reader", "question", None),  # 全空白视为未提问，用默认提示
        ({"monitor_index": 3}, "capture", "monitor_index", 3),
        ({"region": [100, 200, 500, 600]}, "capture", "region", (100, 200, 500, 600)),
        ({"region": [1, 2, 3]}, "capture", "region", (5, 5, 50, 50)),  # 长度不对 → 回落配置的 default_region
        ({"max_width": 200}, "capture", "max_width", 200),
    ],
)
async def test_arguments_are_passed_through_or_normalized(arguments, target, key, expected) -> None:
    provider, capture, reader = _setup({"default_region": [5, 5, 50, 50]})
    await _look(provider, **arguments)
    assert (reader if target == "reader" else capture).calls[0][key] == expected
    if target == "reader" and expected is not None:
        assert reader.calls[0]["image_bytes_len"] > 0


# 失败降级：永不抛，success=True 带 error 字段


@pytest.mark.parametrize(
    ("scenario", "error_keyword", "extra_keyword"),
    [
        ("no-backend", "capture_failed", None),
        ("capture-exception", "capture_failed", "screen unavailable"),
        ("capture-empty", "capture_failed", "empty"),
        ("reader-timeout", "vlm_failed", None),
        ("reader-exception", "vlm_failed", None),
    ],
)
async def test_failures_degrade_to_success_with_error_field(scenario, error_keyword, extra_keyword) -> None:
    capture, reader = FakeScreenCapture(), FakeTextReader()
    if scenario == "no-backend":
        capture = reader = None
    elif scenario == "capture-exception":
        capture.set_raise(RuntimeError("screen unavailable"))
    elif scenario == "capture-empty":
        capture.queue_empty()
    else:
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader.set_raise(
            asyncio.TimeoutError("simulated vlm timeout")
            if scenario == "reader-timeout"
            else RuntimeError("vlm broken")
        )
    registry = ToolRegistry()
    registry.register_provider(LookAtScreenProvider(config={}, screen_capture=capture, text_reader=reader))

    result = await registry.invoke(ToolInvocation(tool_name=FULL_NAME, arguments={}, source="test"))

    structured = result.structured_content
    assert result.success is True and structured["text"] == "" and error_keyword in structured["error"]
    if extra_keyword:
        assert extra_keyword in structured["error"].lower()
    assert ("image" in structured) is error_keyword.startswith("vlm")  # 仅 VLM 失败时采集已成功，图仍可用


# 块组装与图片生命周期


async def test_success_result_carries_text_and_decodable_image_with_metadata() -> None:
    png = b"\x89PNG_FAKE_TEST_BYTES"
    provider, _, _ = _setup({"monitor_index": 1}, text="屏幕上有一个选项", width=640, height=480, png=png)
    result = await _look(provider)

    assert {b.kind for b in result.blocks} == {"text", "image"}
    image_block = next(b for b in result.blocks if b.kind == "image")
    assert base64.b64decode(image_block.data) == png
    structured = result.structured_content
    assert isinstance(structured["image"], str) and structured["image"]
    assert isinstance(structured["width"], int) and isinstance(structured["height"], int)
    assert structured["monitor_index"] == 1 and isinstance(structured["latency_ms"], int)


# 按调用方视觉能力决定是否跑 VLM


def _manager(visual: bool, *, raises: bool = False) -> MagicMock:
    manager = MagicMock()
    if raises:
        manager.is_profile_visual = MagicMock(side_effect=RuntimeError("引擎未就绪"))
    else:
        manager.is_profile_visual = MagicMock(return_value=visual)
    return manager


@pytest.mark.parametrize(
    ("caller_profile", "manager", "vlm_runs", "text"),
    [
        ("planner", _manager(True), False, ""),  # 视觉调用方（主模型能看图）：跳过 VLM，text 空但图照常返回
        ("minecraft", _manager(False), True, "画面描述文本"),  # 非视觉调用方：照旧跑 VLM
        (
            "",
            _manager(True),
            True,
            "画面描述文本",
        ),  # 管线式调用（text_adv 读屏）：结果不进 LLM 对话，要解析 VLM 的坐标文本
        ("planner", _manager(True, raises=True), True, "画面描述文本"),  # 能力查询异常：按非视觉降级，不抛
        ("planner", None, True, "画面描述文本"),  # 未注入 llm_manager（旧组装形态）：向后兼容
    ],
    ids=["visual-skips", "non-visual-runs", "pipeline-runs", "query-failure-runs", "no-manager-runs"],
)
async def test_vlm_runs_only_when_caller_cannot_see_images(caller_profile, manager, vlm_runs, text) -> None:
    kwargs = {} if manager is None else {"llm_manager": manager}
    provider, _, reader = _setup(text="画面描述文本", **kwargs)
    result = await _look(provider, caller_profile=caller_profile)

    assert result.success is True and len(reader.calls) == (1 if vlm_runs else 0)
    assert result.structured_content["text"] == text
    assert any(block.kind == "image" for block in result.blocks)
    if not vlm_runs:
        assert "error" not in result.structured_content
