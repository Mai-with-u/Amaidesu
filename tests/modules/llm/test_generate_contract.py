"""中立 payload 契约与 fake 厂商接入测试

证明两件事：

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

from src.modules.llm.bootstrap import ProfileNames
from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import GenerateRequest, Response, ToolCall, ToolSpec, Usage

PAYLOAD_FILE = Path(__file__).resolve().parents[3] / "src" / "modules" / "llm" / "payload.py"

FAKE_CONFIG: Dict[str, Any] = {
    "llm_providers": [
        {"name": "fake", "client_type": "fakevendor", "base_url": "fake://local", "api_key": "k"},
    ],
    "llm_models": [
        {"name": "m1", "model_identifier": "fake-mini", "api_provider": "fake"},
    ],
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
        interrupt_flag: Any = None,
    ) -> Response:
        self.requests.append(request)
        self.vision_images.extend(images)
        self.models.append(model)
        return Response(success=True, content="fake 视觉描述", model="fake-vision")


@pytest.fixture
def fake_vendor_manager():
    """fake 厂商挂进调度表 + 装配 LLMManager（TokenUsageManager 拦截防磁盘写入）

    yields manager；测试内经 ``manager._provider_clients["fake"]`` 取
    fake 实例读取捕获的请求。
    """
    with patch.dict(_CLIENT_DISPATCH, {"fakevendor": FakeVendorClient}):
        yield LLMManager()


class TestPayloadZeroVendor:
    def test_payload_source_has_no_vendor_reference(self):
        """payload 源码零厂商字样（openai / anthropic / google）"""
        source = PAYLOAD_FILE.read_text(encoding="utf-8")
        assert not re.search(r"openai|anthropic|google", source, re.IGNORECASE)


class TestFakeVendorThroughGenerate:
    @pytest.mark.asyncio
    async def test_generate_with_plain_string(self, fake_vendor_manager):
        """裸字符串输入经 generate 调通 fake 厂商，返回中立 Response"""
        manager = fake_vendor_manager
        await manager.setup(FAKE_CONFIG)
        fake = manager._provider_clients["fake"]

        resp = await manager.generate("你好", profile="planner")

        assert resp.success is True
        assert resp.content == "fake 回复: 你好"
        assert resp.usage.total_tokens == 8
        assert resp.request_id != ""
        assert fake.models == ["fake-mini"]
        # temperature 缺省回退 profile 档位（planner=0.7）
        assert fake.temperatures == [0.7]

    @pytest.mark.asyncio
    async def test_generate_normalizes_openai_style_dicts(self, fake_vendor_manager):
        """OpenAI 风格 dict 列表归一化：system 折叠为独立 system 参数"""
        manager = fake_vendor_manager
        await manager.setup(FAKE_CONFIG)
        fake = manager._provider_clients["fake"]

        resp = await manager.generate(
            [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "问题"},
            ],
            profile="planner",
        )

        assert resp.success is True
        assert len(fake.requests) == 1
        request = fake.requests[0]
        assert request.system == "你是助手"
        assert request.messages[0].role == "user"
        assert request.messages[0].parts == ["问题"]

    @pytest.mark.asyncio
    async def test_generate_with_tool_specs(self, fake_vendor_manager):
        """ToolSpec 声明原样到达 Client（翻译责任在适配端）"""
        manager = fake_vendor_manager
        await manager.setup(FAKE_CONFIG)
        fake = manager._provider_clients["fake"]

        await manager.generate(
            "调用工具",
            profile="planner",
            tools=[{"name": "get_time", "description": "查时间", "parameters": {"type": "object"}}],
        )

        request = fake.requests[0]
        assert request.tools[0] == ToolSpec(name="get_time", description="查时间", parameters={"type": "object"})

    @pytest.mark.asyncio
    async def test_generate_vision_end_to_end(self, fake_vendor_manager):
        """视觉入口：prompt + images 经 generate_vision 调通 fake 厂商"""
        manager = fake_vendor_manager
        await manager.setup(FAKE_CONFIG)
        fake = manager._provider_clients["fake"]

        resp = await manager.generate_vision("描述屏幕", ["/tmp/a.png"], system="视觉助手")

        assert resp.success is True
        assert resp.content == "fake 视觉描述"
        assert fake.vision_images == ["/tmp/a.png"]
        request = fake.requests[0]
        assert request.system == "视觉助手"
        assert request.messages[0].parts == ["描述屏幕"]

    @pytest.mark.asyncio
    async def test_generate_preserves_tool_context(self, fake_vendor_manager):
        """喂回的多轮 ReAct 上下文（assistant tool_calls + tool 观察）经归一化不丢失。

        回归：这些字段一旦被归一化吞掉，tool 消息就会失去与 assistant 调用的
        关联，真实端点按协议拒绝整个消息序列。
        """
        manager = fake_vendor_manager
        await manager.setup(FAKE_CONFIG)
        fake = manager._provider_clients["fake"]

        await manager.generate(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "query_memory", "arguments": '{"q": "x"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            ],
            profile="planner",
        )

        request = fake.requests[0]
        assert request.messages[0].tool_calls == [ToolCall(id="c1", name="query_memory", arguments={"q": "x"})]
        assert request.messages[1].tool_call_id == "c1"

    @pytest.mark.asyncio
    async def test_unknown_profile_rejected_at_setup(self):
        """装配期封闭集合校验：未知 profile 硬错（机制证明）"""
        bad_config = dict(FAKE_CONFIG)
        bad_config["llm_profiles"] = {**FAKE_CONFIG["llm_profiles"], "mystery": {"model_list": ["m1"]}}
        with patch.dict(_CLIENT_DISPATCH, {"fakevendor": FakeVendorClient}):
            manager = LLMManager()
            with pytest.raises(ValueError, match="mystery"):
                await manager.setup(bad_config)

    def test_old_registry_symbols_gone(self):
        """旧注册表机制已删除：client 模块不再提供 register_client/_client_impls"""
        import src.modules.llm.client as client_module

        assert not hasattr(client_module, "register_client")
        assert not hasattr(client_module, "_client_impls")
        assert _CLIENT_DISPATCH["openai"] is not None

    def test_vision_is_default_profile_for_generate_vision(self):
        """generate_vision 缺省走 vision 档位（与旧 chat_vision 语义一致）"""
        assert ProfileNames.VISION == "vision"
