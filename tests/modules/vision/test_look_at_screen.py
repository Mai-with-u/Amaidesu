"""视觉工具完整传递识别问题与结果，默认读取原图，显式区域及缩放参数按请求执行。"""

from __future__ import annotations

import asyncio


from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.vision.look_at_screen import (
    LOOK_AT_SCREEN_SPEC,
    LlmVisionTextReader,
    LookAtScreenProvider,
    PROVIDER_NAME,
    SCREEN_VLM_PROMPT_KEY,
    SCREEN_VLM_SYSTEM_KEY,
    TOOL_NAME,
    FakeScreenCapture,
    FakeTextReader,
)
from src.modules.vision.mss_capture import MssScreenCapture


# ===========================================================================
# 工具规格断言
# ===========================================================================


class TestLookAtScreenSpec:
    def test_full_name_is_stable_for_compatibility(self) -> None:
        """兼容锚点：ToolSpec.full_name = "vision_look_at_screen"。"""
        assert TOOL_NAME == "look_at_screen"
        assert PROVIDER_NAME == "vision"
        assert LOOK_AT_SCREEN_SPEC.full_name == "vision_look_at_screen"

    def test_description_prioritizes_when_to_use(self) -> None:
        """description 含"何时用"表述 + 失败降级语义（不抛）。"""
        desc = LOOK_AT_SCREEN_SPEC.description
        assert "需要知道" in desc or "调用" in desc
        assert "屏幕" in desc
        # 失败语义：不抛
        assert "失败" in desc or "error" in desc.lower()
        assert "不抛" in desc or "降级" in desc

    def test_required_is_empty_all_args_optional(self) -> None:
        """所有入参可选：required == []。"""
        params = LOOK_AT_SCREEN_SPEC.parameters_schema or {}
        assert params.get("required") == []

    def test_parameters_have_four_optional_fields(self) -> None:
        """四个可选参数：question / region / monitor_index / max_width。"""
        props = (LOOK_AT_SCREEN_SPEC.parameters_schema or {}).get("properties", {})
        assert set(props.keys()) == {"question", "region", "monitor_index", "max_width"}

    def test_question_constraints(self) -> None:
        q = (LOOK_AT_SCREEN_SPEC.parameters_schema or {})["properties"]["question"]
        assert q["type"] == "string"
        assert q.get("minLength") == 1
        assert "maxLength" not in q

    def test_region_constraints(self) -> None:
        r = (LOOK_AT_SCREEN_SPEC.parameters_schema or {})["properties"]["region"]
        assert r["type"] == "array"
        assert r["items"] == {"type": "integer"}
        assert r.get("minItems") == 4
        assert r.get("maxItems") == 4

    def test_monitor_index_constraints(self) -> None:
        m = (LOOK_AT_SCREEN_SPEC.parameters_schema or {})["properties"]["monitor_index"]
        assert m["type"] == "integer"
        assert m.get("minimum") == 0

    def test_max_width_constraints(self) -> None:
        mw = (LOOK_AT_SCREEN_SPEC.parameters_schema or {})["properties"]["max_width"]
        assert mw["type"] == "integer"
        assert mw.get("minimum") == 100
        assert mw.get("maximum") == 3840

    def test_output_schema_has_all_required_fields(self) -> None:
        """output_schema 含 text/image?/width/height/monitor_index/region/error?/latency_ms。"""
        props = (LOOK_AT_SCREEN_SPEC.output_schema or {}).get("properties", {})
        for field in ("text", "width", "height", "monitor_index", "region", "latency_ms"):
            assert field in props, f"output_schema 缺字段: {field}"
        # image 与 error 可选（采集失败时缺失）
        assert "image" in props
        assert "error" in props

    def test_kind_and_provider(self) -> None:
        """kind=sync；provider=vision。"""
        assert LOOK_AT_SCREEN_SPEC.kind == "sync"
        assert LOOK_AT_SCREEN_SPEC.provider == PROVIDER_NAME


# ===========================================================================
# ConfigSchema：新字段
# ===========================================================================


class TestConfigSchema:
    def test_default_fields(self) -> None:
        """默认捕获完整显示器，生成没有宿主时限或自动缩放。"""
        cfg = LookAtScreenProvider.ConfigSchema.from_dict({})
        assert cfg.monitor_index == 1 and cfg.default_region is None
        assert "vlm_timeout_ms" not in cfg.model_dump()
        assert "default_max_width" not in cfg.model_dump()

    def test_overrides_apply(self) -> None:
        """显示器和显式区域继续按用户选择生效。"""
        cfg = LookAtScreenProvider.ConfigSchema.from_dict({"monitor_index": 2, "default_region": [10, 20, 110, 120]})
        assert cfg.monitor_index == 2
        assert cfg.default_region == [10, 20, 110, 120]

    def test_extra_fields_stripped_silently(self) -> None:
        """未知字段被 from_dict 静默剥离（不抛）；BaseConfig.from_dict 行为约定。"""
        cfg = LookAtScreenProvider.ConfigSchema.from_dict({"monitor_index": 1, "unknown_field": "x"})
        # 未知字段不影响构造
        assert cfg.monitor_index == 1
        # 实例上不存在该字段
        assert not hasattr(cfg, "unknown_field")

    async def test_legacy_config_does_not_resize_capture(self) -> None:
        """即使旧配置残留缩放或时限字段，画面仍按原始尺寸交给识别。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"image", width=2560, height=1440)
        reader = FakeTextReader()
        reader.queue_text("完整画面")
        provider = LookAtScreenProvider(
            config={"vlm_timeout_ms": 1, "default_max_width": 800},
            screen_capture=capture,
            text_reader=reader,
        )
        result = await provider.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}))
        assert result.structured_content["width"] == 2560
        assert capture.calls[-1]["max_width"] is None

    def test_provider_does_not_inject_reader_deadline(self) -> None:
        """装配视觉工具时不再给 reader 写入时钟截止配置。"""
        reader = LlmVisionTextReader(llm_manager=object())
        LookAtScreenProvider(config={}, text_reader=reader)
        assert not hasattr(reader, "_timeout_s")


# ===========================================================================
# 零参调用兼容锚点（text_adv）
# ===========================================================================


class TestZeroArgumentCompatibility:
    async def test_zero_arg_invoke_succeeds_with_text_string(self) -> None:
        """``invoke(arguments={})`` → success=True 且 ``structured_content["text"]`` 为 str。"""
        registry = ToolRegistry()
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG_FAKE", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("hello screen")
        provider = LookAtScreenProvider(
            config={"monitor_index": 1, "default_max_width": 320},
            screen_capture=capture,
            text_reader=reader,
        )
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert isinstance(res.structured_content, dict)
        assert isinstance(res.structured_content.get("text"), str)
        assert res.structured_content["text"] == "hello screen"

    async def test_zero_arg_uses_provider_defaults(self) -> None:
        """零参时 capture 收到 provider 配置默认的 monitor_index 与 max_width。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG_FAKE", width=800, height=600)
        reader = FakeTextReader()
        reader.queue_text("text")
        provider = LookAtScreenProvider(
            config={"monitor_index": 2, "default_max_width": 640, "default_region": [10, 10, 100, 100]},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert capture.calls[0]["monitor_index"] == 2
        assert capture.calls[0]["region"] == (10, 10, 100, 100)
        assert capture.calls[0]["max_width"] is None


# ===========================================================================
# 入参透传
# ===========================================================================


class TestArgumentPassthrough:
    async def test_question_passed_to_reader(self) -> None:
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("answer")
        provider = LookAtScreenProvider(
            config={},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"question": "屏幕上有几个选项？"},
                source="test",
            )
        )

        assert reader.calls[0]["question"] == "屏幕上有几个选项？"
        assert reader.calls[0]["image_bytes_len"] > 0

    async def test_monitor_index_passed_to_capture(self) -> None:
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"monitor_index": 3},
                source="test",
            )
        )

        assert capture.calls[0]["monitor_index"] == 3

    async def test_region_passed_to_capture(self) -> None:
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"region": [100, 200, 500, 600]},
                source="test",
            )
        )

        assert capture.calls[0]["region"] == (100, 200, 500, 600)

    async def test_max_width_passed_to_capture(self) -> None:
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"max_width": 200},
                source="test",
            )
        )

        assert capture.calls[0]["max_width"] == 200

    async def test_question_whitespace_stripped(self) -> None:
        """question 入参全空白 → 视为 None（用默认提示）。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"question": "   "},
                source="test",
            )
        )

        # reader 收到的 question 应是 None（被 strip 后判空）
        assert reader.calls[0]["question"] is None

    async def test_invalid_region_falls_back_to_default(self) -> None:
        """region 入参非法（长度不对 / 非数字）→ 用 provider 配置默认。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={"default_region": [5, 5, 50, 50]},
            screen_capture=capture,
            text_reader=reader,
        )

        await provider.invoke(
            ToolInvocation(
                tool_name="vision_look_at_screen",
                arguments={"region": [1, 2, 3]},  # 长度不是 4
                source="test",
            )
        )

        # 非法 → 走 default_region
        assert capture.calls[0]["region"] == (5, 5, 50, 50)


# ===========================================================================
# 失败降级
# ===========================================================================


class TestGracefulDegradation:
    async def test_no_backend_returns_success_with_error(self) -> None:
        """未注入 ScreenCapture → success=True + text="" + error 含 'capture_failed'。"""
        registry = ToolRegistry()
        provider = LookAtScreenProvider(config={}, screen_capture=None, text_reader=None)
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert res.structured_content["text"] == ""
        assert "capture_failed" in res.structured_content["error"]

    async def test_capture_exception_returns_success_with_error(self) -> None:
        """capture 抛异常 → success=True + text="" + error 含 'capture_failed'。"""
        capture = FakeScreenCapture()
        capture.set_raise(RuntimeError("screen unavailable"))
        provider = LookAtScreenProvider(config={}, screen_capture=capture, text_reader=FakeTextReader())
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert res.structured_content["text"] == ""
        assert "capture_failed" in res.structured_content["error"]
        assert "screen unavailable" in res.structured_content["error"]

    async def test_capture_empty_image_returns_success_with_error(self) -> None:
        """capture 返回空图像 → success=True + text="" + error 含 'capture_failed'。"""
        capture = FakeScreenCapture()
        capture.queue_empty()
        provider = LookAtScreenProvider(config={}, screen_capture=capture, text_reader=FakeTextReader())
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert res.structured_content["text"] == ""
        assert "capture_failed" in res.structured_content["error"]
        assert "empty" in res.structured_content["error"].lower()

    async def test_reader_timeout_returns_success_with_error(self) -> None:
        """reader 抛 asyncio.TimeoutError → success=True + text="" + error 含 'vlm_failed'。

        超时由 reader 内部管理（``LlmVisionTextReader`` 用 ``asyncio.wait_for`` 包裹）；
        FakeTextReader 通过 set_raise(asyncio.TimeoutError()) 模拟"reader 已按自身超时降级"。
        Provider 兜底任何 reader 抛出的异常 → success=True + error 字段。
        """
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.set_raise(asyncio.TimeoutError("simulated vlm timeout"))
        provider = LookAtScreenProvider(
            config={"vlm_timeout_ms": 100},
            screen_capture=capture,
            text_reader=reader,
        )
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert res.structured_content["text"] == ""
        # vlm 失败路径 → structured_content 里有 error 字段（vlm_failed）
        assert "vlm_failed" in (res.structured_content.get("error") or "")
        # image 仍可用（采集成功，reader 失败）
        assert "image" in res.structured_content

    async def test_reader_exception_returns_success_with_error(self) -> None:
        """reader 抛异常 → success=True + text="" + error 含 'vlm_failed'。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG", width=320, height=240)
        reader = FakeTextReader()
        reader.set_raise(RuntimeError("vlm transport broken"))
        provider = LookAtScreenProvider(
            config={"vlm_timeout_ms": 5000},
            screen_capture=capture,
            text_reader=reader,
        )
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert res.success is True
        assert res.structured_content["text"] == ""
        assert "vlm_failed" in (res.structured_content.get("error") or "")
        # image 仍可用（采集成功但 reader 失败）
        assert "image" in res.structured_content


# ===========================================================================
# 块组装与图片生命周期
# ===========================================================================


class TestBlocksAndImageLifecycle:
    async def test_blocks_have_text_and_image(self) -> None:
        """成功路径 → blocks 含 text + image。"""
        capture = FakeScreenCapture()
        capture.queue_png(b"\x89PNG_FAKE", width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("屏幕上有一个选项")
        provider = LookAtScreenProvider(config={}, screen_capture=capture, text_reader=reader)
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        kinds = {b.kind for b in res.blocks}
        assert "text" in kinds
        assert "image" in kinds
        # structured_content 同时含 image 键（base64）
        assert "image" in res.structured_content
        assert isinstance(res.structured_content["image"], str)
        assert len(res.structured_content["image"]) > 0

    async def test_structured_content_includes_metadata(self) -> None:
        """structured_content 含 width/height/monitor_index/region/latency_ms。"""
        capture = FakeScreenCapture()
        capture.queue_png(
            b"\x89PNG",
            width=640,
            height=480,
        )  # width/height 提示为 PNG bytes
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(
            config={"monitor_index": 1},
            screen_capture=capture,
            text_reader=reader,
        )
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        sc = res.structured_content
        assert isinstance(sc.get("width"), int)
        assert isinstance(sc.get("height"), int)
        assert sc.get("monitor_index") == 1
        assert "latency_ms" in sc
        assert isinstance(sc["latency_ms"], int)

    async def test_no_image_field_when_capture_fails(self) -> None:
        """capture 失败 → structured_content 不含 image 字段。"""
        capture = FakeScreenCapture()
        capture.set_raise(RuntimeError("boom"))
        provider = LookAtScreenProvider(config={}, screen_capture=capture, text_reader=None)
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        assert "image" not in res.structured_content

    async def test_image_base64_in_blocks_is_decodable(self) -> None:
        """blocks 中 image.data 可被 base64 解码回原 bytes。"""
        import base64

        png_bytes = b"\x89PNG_FAKE_TEST_BYTES"
        capture = FakeScreenCapture()
        capture.queue_png(png_bytes, width=320, height=240)
        reader = FakeTextReader()
        reader.queue_text("x")
        provider = LookAtScreenProvider(config={}, screen_capture=capture, text_reader=reader)
        registry = ToolRegistry()
        registry.register_provider(provider)

        res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))

        image_block = next((b for b in res.blocks if b.kind == "image"), None)
        assert image_block is not None
        decoded = base64.b64decode(image_block.data)
        assert decoded == png_bytes


# ===========================================================================
# 工具名稳定（compat 锚点）
# ===========================================================================


class TestProviderRegistrationContract:
    def test_provider_registers_with_full_name(self) -> None:
        """注册后工具全名 = vision_look_at_screen（兼容 text_adv 的硬编码调用）。"""
        registry = ToolRegistry()
        provider = LookAtScreenProvider(
            config={},
            screen_capture=FakeScreenCapture(),
            text_reader=FakeTextReader(),
        )
        registry.register_provider(provider)
        assert registry.has("vision_look_at_screen")
        assert LOOK_AT_SCREEN_SPEC.full_name == "vision_look_at_screen"

    def test_provider_visible_to_all_by_default(self) -> None:
        """visible_to 默认 = ['*']（所有 Planner 可见，工具可被发现）。"""
        # visible_to 在 ToolRegistry 一侧由 Provider 自己声明（BaseToolProvider 默认 ["*"]）
        provider = LookAtScreenProvider(
            config={},
            screen_capture=FakeScreenCapture(),
            text_reader=FakeTextReader(),
        )
        # Provider 不显式覆写 visible_to → 走 ToolRegistry 默认 ("*" / 全可见)
        assert getattr(provider, "visible_to", None) in (None, ["*"])


# ===========================================================================
# Module-level 常量与导出
# ===========================================================================


class TestModuleExports:
    def test_vlm_controls_are_not_exposed(self) -> None:
        """配置界面从 Schema 生成，已删除的生成限制不能再次显示。"""
        fields = LookAtScreenProvider.ConfigSchema.model_fields
        assert "vlm_timeout_ms" not in fields and "default_max_width" not in fields

    def test_template_keys_unchanged(self) -> None:
        """模板键名保留（Task 3 迁移后）。"""
        assert SCREEN_VLM_PROMPT_KEY == "screen_vlm_prompt"
        assert SCREEN_VLM_SYSTEM_KEY == "screen_vlm_system"

    def test_mss_capture_production_protocol_compatible(self) -> None:
        """生产 MssScreenCapture 满足 ScreenCapture 协议形态（capture 形参一致）。"""
        # 仅做静态形参核对（运行时 mss 不一定可用）
        import inspect

        cap_sig = inspect.signature(MssScreenCapture.capture)
        # 形参顺序：self, monitor_index, region, max_width
        params = list(cap_sig.parameters.keys())
        assert params[0] == "self"
        assert params[1] == "monitor_index"
        assert params[2] == "region"
        assert params[3] == "max_width"
