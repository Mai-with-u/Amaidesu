"""中立 payload 契约与 fake 厂商接入。

- ``payload.py`` 零厂商依赖（守护测试直接断言源码无厂商字样）
- 新增厂商只动 ``clients/`` 调度表一行 + 一个 ``clients/<vendor>/`` 实现，
  消费方经 ``generate`` / ``generate_vision`` 调通，调用处零改动
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.modules.llm import client as client_module
from src.modules.llm.bootstrap import ProfileNames
from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager, _content_to_parts
from src.modules.llm.payload import GenerateRequest, Response, ToolCall, ToolSpec, Usage

PAYLOAD_FILE = Path(__file__).resolve().parents[3] / "src" / "modules" / "llm" / "payload.py"

FAKE_CONFIG: Dict[str, Any] = {
    "llm_providers": [{"name": "fake", "client_type": "fakevendor", "base_url": "fake://local", "api_key": "k"}],
    "llm_models": [{"name": "m1", "model_identifier": "fake-mini", "api_provider": "fake"}],
    "llm_profiles": {
        "planner": {"model_list": ["m1"], "temperature": 0.7, "max_tokens": 4096},
        "vision": {"model_list": ["m1"]},
    },
}


class FakeVendorClient(BaseLLMClient):
    """fake 厂商适配端：实现中立 payload 接口，记录收到的请求供断言"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.requests: List[GenerateRequest] = []
        self.vision_images: List[Any] = []
        self.models: List[str] = []
        self.temperatures: List[Optional[float]] = []

    async def generate(
        self,
        request: GenerateRequest,
        *,
        model: str,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        on_delta: Any = None,
        interrupt_flag: Any = None,
    ) -> Response:
        self.requests.append(request)
        self.models.append(model)
        self.temperatures.append(temperature)
        return Response(
            success=True,
            content=f"fake 回复: {request.messages[-1].parts[0]}",
            usage=Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8),
            model="fake-mini-actual",
        )

    async def generate_vision(
        self,
        request: GenerateRequest,
        images: List[Any],
        *,
        model: str,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        interrupt_flag: Any = None,
    ) -> Response:
        self.requests.append(request)
        self.vision_images.extend(images)
        self.models.append(model)
        return Response(success=True, content="fake 视觉描述", model="fake-vision")


@pytest.fixture
async def fake_vendor() -> tuple[LLMManager, FakeVendorClient]:
    """fake 厂商挂进调度表并装配 LLMManager，返回 (manager, 厂商实例) 供读取捕获的请求。"""
    with patch.dict(_CLIENT_DISPATCH, {"fakevendor": FakeVendorClient}):
        manager = LLMManager()
        await manager.setup(FAKE_CONFIG)
        yield manager, manager._provider_clients["fake"]


def test_payload_source_has_no_vendor_reference_and_the_old_registry_is_gone() -> None:
    assert not re.search(r"openai|anthropic|google", PAYLOAD_FILE.read_text(encoding="utf-8"), re.IGNORECASE)
    assert not hasattr(client_module, "register_client") and not hasattr(client_module, "_client_impls")
    assert _CLIENT_DISPATCH["openai"] is not None
    assert ProfileNames.VISION == "vision"  # generate_vision 缺省走 vision 档位


async def test_plain_string_reaches_the_vendor_and_returns_a_neutral_response(fake_vendor) -> None:
    manager, fake = fake_vendor

    response = await manager.generate("你好", profile="planner")

    assert (response.success, response.content, response.usage.total_tokens) == (True, "fake 回复: 你好", 8)
    assert response.request_id != ""
    assert fake.models == ["fake-mini"]
    assert fake.temperatures == [0.7]  # temperature 缺省回退 profile 档位


async def test_openai_style_dicts_are_normalized_with_system_folded_into_its_own_field(fake_vendor) -> None:
    manager, fake = fake_vendor

    await manager.generate(
        [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "问题"}], profile="planner"
    )

    (request,) = fake.requests
    assert request.system == "你是助手"
    assert (request.messages[0].role, request.messages[0].parts) == ("user", ["问题"])


async def test_tool_specs_reach_the_client_untranslated(fake_vendor) -> None:
    manager, fake = fake_vendor

    await manager.generate(
        "调用工具",
        profile="planner",
        tools=[{"name": "get_time", "description": "查时间", "parameters": {"type": "object"}}],
    )

    assert fake.requests[0].tools[0] == ToolSpec(name="get_time", description="查时间", parameters={"type": "object"})


async def test_vision_entry_passes_prompt_and_images_to_the_vendor(fake_vendor) -> None:
    manager, fake = fake_vendor

    response = await manager.generate_vision("描述屏幕", ["/tmp/a.png"], system="视觉助手")

    assert (response.success, response.content) == (True, "fake 视觉描述")
    assert fake.vision_images == ["/tmp/a.png"]
    assert (fake.requests[0].system, fake.requests[0].messages[0].parts) == ("视觉助手", ["描述屏幕"])


async def test_multi_round_react_context_survives_normalization(fake_vendor) -> None:
    """assistant tool_calls 与 tool 观察一旦被归一化吞掉，tool 消息会失去与调用的关联，真实端点会拒绝整个序列。"""
    manager, fake = fake_vendor
    call = {"id": "c1", "type": "function", "function": {"name": "query_memory", "arguments": '{"q": "x"}'}}

    await manager.generate(
        [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
        profile="planner",
    )

    request = fake.requests[0]
    assert request.messages[0].tool_calls == [ToolCall(id="c1", name="query_memory", arguments={"q": "x"})]
    assert request.messages[1].tool_call_id == "c1"


async def test_unknown_profile_is_rejected_at_setup() -> None:
    bad_config = {**FAKE_CONFIG, "llm_profiles": {**FAKE_CONFIG["llm_profiles"], "mystery": {"model_list": ["m1"]}}}
    with patch.dict(_CLIENT_DISPATCH, {"fakevendor": FakeVendorClient}):
        with pytest.raises(ValueError, match="mystery"):  # 装配期封闭集合校验
            await LLMManager().setup(bad_config)


def test_message_content_normalizes_to_parts_and_rejects_inline_images() -> None:
    """content 为 None（assistant 发起 tool_calls 时的协议合法形态）折成空 parts，不能抛「不支持的类型」。"""
    assert _content_to_parts(None) == []
    assert _content_to_parts("你好") == ["你好"]
    mixed = _content_to_parts([{"type": "text", "text": "a"}, {"type": "image_ref", "ref": "img_ab12"}])
    assert [type(part).__name__ for part in mixed] == ["TextPart", "ImageRefPart"]
    # 历史里图的唯一合法形态是引用：任何整体序列化消费者（压缩器/预算）都不该见到 base64
    with pytest.raises(TypeError, match="不允许内联图像"):
        _content_to_parts([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
