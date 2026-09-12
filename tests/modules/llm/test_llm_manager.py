"""LLMManager 单元测试（三层配置：providers / models / profiles）

测试 LLMManager 的所有核心功能：
- 三层结构初始化（llm_providers + llm_models + llm_profiles）
- 聊天接口（chat / chat_messages / chat_vision / stream_chat / call_tools）
- 简化接口（simple_chat / simple_vision）
- 模型选择策略（sequential / balance / random）
- 故障切换（hard_timeout_ms → 切下一个 model；slow_threshold_ms → 仅告警）
- 重试机制（仅在单模型上重试，不跨模型）
- 统计信息（token usage / provider info）
- 清理（cleanup）

运行: uv run pytest tests/modules/llm/test_llm_manager.py -v

=== 新配置格式 ===

    {
        "llm_providers": [
            {"name": "test", "client_type": "openai", "base_url": "...", "api_key": "..."}
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "gpt-4o-mini", "api_provider": "test"},
        ],
        "llm_profiles": {
            "planner": {"model_list": ["m1"], "hard_timeout_ms": 90000},
        },
    }

=== Mock 策略 ===

client 实现通过 src.modules.llm.clients.base.register_client() 在模块导入
时注册到 _client_impls 字典，注册时存的是原始类对象的引用，
不是通过模块属性查找。

因此 `patch("src.modules.llm.clients.openai_client.OpenAIClient")` 不能拦截
manager 内部 `get_client_impl("openai")` 的查询——它会拿到未 patch 的原类。

正确的 mock 方式是 patch 注册表：
    with patch.dict(_client_impls, {"openai": mock_class}):
        ...
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients.base import _client_impls
from src.modules.llm.manager import (
    ClientType,  # 向后兼容别名
    LLMManager,
    LLMResponse,
    ProfileNames,
    RetryConfig,
    normalize_tool_calls_for_protocol,
)


# =============================================================================
# normalize_tool_calls_for_protocol - 喂回协议规整
# =============================================================================


class TestNormalizeToolCallsForProtocol:
    def test_dict_arguments_serialized_to_json_string(self) -> None:
        raw = [
            {
                "id": "call_01",
                "type": "function",
                "function": {"name": "minecraft_assign", "arguments": {"content": "合成工作台"}},
            }
        ]
        normalized = normalize_tool_calls_for_protocol(raw)
        assert normalized[0]["function"]["arguments"] == '{"content": "合成工作台"}'
        assert isinstance(normalized[0]["function"]["arguments"], str)

    def test_string_arguments_passthrough(self) -> None:
        raw = [{"id": "c1", "type": "function", "function": {"name": "reply", "arguments": '{"a": 1}'}}]
        normalized = normalize_tool_calls_for_protocol(raw)
        assert normalized[0]["function"]["arguments"] == '{"a": 1}'

    def test_missing_fields_defaulted(self) -> None:
        normalized = normalize_tool_calls_for_protocol([{"function": {"name": "x"}}])
        assert normalized[0]["id"] == ""
        assert normalized[0]["type"] == "function"
        assert normalized[0]["function"]["arguments"] == "{}"

    def test_none_and_empty(self) -> None:
        assert normalize_tool_calls_for_protocol(None) == []
        assert normalize_tool_calls_for_protocol([]) == []


# =============================================================================
# 标准配置：三层结构（1 个 provider / 3 个 model / 3 个 profile）
# =============================================================================


PROVIDER_CONFIG = {
    "name": "test",
    "client_type": "openai",
    "base_url": "https://api.test.com/v1",
    "api_key": "test-api-key",
}

STANDARD_MOCK_CONFIG: Dict[str, Any] = {
    "llm_providers": [PROVIDER_CONFIG],
    "llm_models": [
        {"name": "gpt-4o-mini", "model_identifier": "gpt-4o-mini", "api_provider": "test"},
        {"name": "gpt-3.5-turbo", "model_identifier": "gpt-3.5-turbo", "api_provider": "test"},
        {"name": "gpt-4-vision", "model_identifier": "gpt-4-vision-preview", "api_provider": "test"},
    ],
    "llm_profiles": {
        "planner": {"model_list": ["gpt-4o-mini"], "temperature": 0.7, "max_tokens": 2048},
        "replyer": {"model_list": ["gpt-3.5-turbo"], "temperature": 0.2, "max_tokens": 1024},
        "vision": {"model_list": ["gpt-4-vision"], "temperature": 0.3, "max_tokens": 1024},
    },
}


# =============================================================================
# Helper: 标准 mock backend
# =============================================================================


def _make_mock_backend() -> MagicMock:
    mock_backend = MagicMock()
    mock_backend.chat = AsyncMock(
        return_value=LLMResponse(
            success=True,
            content="Test response",
            model="gpt-4o-mini",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    )
    mock_backend.stream_chat = AsyncMock()
    mock_backend.vision = AsyncMock(
        return_value=LLMResponse(
            success=True,
            content="Image description",
            model="gpt-4-vision-preview",
            usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        )
    )
    mock_backend.cleanup = AsyncMock()
    mock_backend.get_info.return_value = {
        "name": "OpenAIClient",
        "model": "gpt-4o-mini",
        "base_url": "https://api.test.com/v1",
    }
    return mock_backend


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_config() -> Dict[str, Any]:
    return STANDARD_MOCK_CONFIG


@pytest.fixture
def llm_manager() -> LLMManager:
    return LLMManager()


@pytest.fixture
async def setup_llm_manager(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """完整初始化 LLMManager（mock OpenAIClient + TokenUsageManager）"""
    mock_backend = _make_mock_backend()
    mock_backend_class = MagicMock(return_value=mock_backend)

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager") as mock_token_manager:
            await llm_manager.setup(mock_config)
            yield llm_manager, mock_backend, mock_token_manager


# =============================================================================
# 初始化和配置测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_initializes_providers_and_profiles(
    llm_manager: LLMManager, mock_config: Dict[str, Any]
):
    """测试 setup 初始化 providers / models / profiles"""
    mock_backend_class = MagicMock(return_value=_make_mock_backend())

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # 1 个 provider → 1 个客户端实例（profile 维度不再各自构造 client）
            assert mock_backend_class.call_count == 1
            assert len(llm_manager.list_providers()) == 1
            assert "test" in llm_manager.list_providers()
            assert len(llm_manager.list_models()) == 3
            assert len(llm_manager.list_clients()) == 3  # planner / replyer / vision
            for p in ("planner", "replyer", "vision"):
                assert llm_manager.has_profile(p)


@pytest.mark.asyncio
async def test_setup_initializes_token_manager(
    llm_manager: LLMManager, mock_config: Dict[str, Any]
):
    """测试 setup 初始化 TokenUsageManager"""
    mock_backend_class = MagicMock(return_value=_make_mock_backend())

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager") as mock_token_manager:
            await llm_manager.setup(mock_config)

            assert llm_manager._token_manager is not None
            mock_token_manager.assert_called_once_with(use_global=True)


@pytest.mark.asyncio
async def test_setup_with_custom_config(llm_manager: LLMManager):
    """测试自定义 provider 配置初始化"""
    custom_provider = {
        "name": "custom",
        "client_type": "openai",
        "api_key": "custom-key",
        "base_url": "https://custom.api.com/v1",
    }
    config = {
        "llm_providers": [custom_provider],
        "llm_models": [
            {"name": "m1", "model_identifier": "custom-model-id", "api_provider": "custom"},
        ],
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
        },
    }

    mock_backend_class = MagicMock(return_value=MagicMock())
    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # client 仅构造 provider 配置（不绑定 model）
            call_args = mock_backend_class.call_args[0][0]
            assert call_args["api_key"] == "custom-key"
            assert call_args["base_url"] == "https://custom.api.com/v1"
            assert "model" not in call_args  # model 由调用方每次传入


@pytest.mark.asyncio
async def test_setup_unknown_client_type_raises_error(llm_manager: LLMManager):
    """测试未注册的 client_type 立即 fail-fast"""
    config = {
        "llm_providers": [
            {"name": "test", "client_type": "unknown_backend"},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "test"},
        ],
        "llm_profiles": {"planner": {"model_list": ["m1"]}},
    }

    with pytest.raises(ValueError, match="未注册的客户端类型"):
        await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_unknown_provider_in_model_raises_error(llm_manager: LLMManager):
    """测试 model 引用了不存在的 provider 时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [{"name": "real", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "nonexistent"},
        ],
        "llm_profiles": {"planner": {"model_list": ["m1"]}},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="llm_models"):
            await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_duplicate_provider_name_raises_error(llm_manager: LLMManager):
    """测试 llm_providers 中存在重复 name 时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [
            {"name": "test", "client_type": "openai"},
            {"name": "test", "client_type": "openai"},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "test"},
        ],
        "llm_profiles": {"planner": {"model_list": ["m1"]}},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="重复的 provider name"):
            await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_empty_providers_raises_error(llm_manager: LLMManager):
    """测试 llm_providers 为空时 fail-fast"""
    config: Dict[str, Any] = {
        "llm_providers": [],
        "llm_models": [],
        "llm_profiles": {},
    }
    with pytest.raises(ValueError, match="llm_providers"):
        await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_profile_references_unknown_model_raises_error(llm_manager: LLMManager):
    """测试 profile.model_list 引用未知 model 时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [{"name": "test", "client_type": "openai"}],
        "llm_models": [{"name": "m1", "model_identifier": "id1", "api_provider": "test"}],
        "llm_profiles": {"planner": {"model_list": ["nonexistent"]}},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="引用未知 model"):
            await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_profile_empty_model_list_raises_error(llm_manager: LLMManager):
    """测试 profile.model_list 为空时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [{"name": "test", "client_type": "openai"}],
        "llm_models": [{"name": "m1", "model_identifier": "id1", "api_provider": "test"}],
        "llm_profiles": {"planner": {"model_list": []}},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="model_list 为空"):
            await llm_manager.setup(config)


# =============================================================================
# 聊天接口测试
# =============================================================================


@pytest.mark.asyncio
async def test_chat_basic(setup_llm_manager):
    """测试基本聊天功能（planner profile）"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello, world!")

    assert response.success is True
    assert response.content == "Test response"
    assert response.model == "gpt-4o-mini"
    assert response.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_chat_passes_model_per_call(setup_llm_manager):
    """测试 chat 调用时把 model_identifier 作为 model 参数传给 client"""
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat("Hello", client_type="planner")

    call_args = mock_backend.chat.call_args
    # 每次调用都应携带 model 参数（profile='planner' → model='gpt-4o-mini'）
    assert call_args[1]["model"] == "gpt-4o-mini"
    # model 不再绑定到 self.model（构造时无 model 字段）
    assert "model" not in (llm_manager._providers["test"][0])


@pytest.mark.asyncio
async def test_chat_with_system_message(setup_llm_manager):
    """测试带系统消息的聊天"""
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat("Hello", system_message="You are a helpful assistant")

    call_args = mock_backend.chat.call_args
    messages = call_args[1]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful assistant"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_chat_with_temperature(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat("Hello", temperature=0.9)

    call_args = mock_backend.chat.call_args
    assert call_args[1]["temperature"] == 0.9


@pytest.mark.asyncio
async def test_chat_with_max_tokens(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat("Hello", max_tokens=100)

    call_args = mock_backend.chat.call_args
    assert call_args[1]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_chat_with_custom_profile(setup_llm_manager):
    """测试选择 replyer profile"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", client_type="replyer")

    assert response.success is True
    call_args = mock_backend.chat.call_args
    assert call_args[1]["model"] == "gpt-3.5-turbo"


@pytest.mark.asyncio
async def test_chat_records_token_usage(setup_llm_manager):
    llm_manager, mock_backend, mock_token_manager = setup_llm_manager

    await llm_manager.chat("Hello")

    mock_token_manager.return_value.record_usage.assert_called_once_with(
        model_name="gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )


@pytest.mark.asyncio
async def test_chat_with_unknown_profile_raises(setup_llm_manager):
    """测试调用未注册的 profile 立即 fail-fast"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="未配置"):
        await llm_manager.chat("Test", client_type="unknown_backend")


# =============================================================================
# 流式聊天测试
# =============================================================================


@pytest.mark.asyncio
async def test_stream_chat_basic(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    async def mock_stream(**kwargs):
        for chunk in ["Hello", " world", "!"]:
            yield chunk

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            chunks = []
            async for chunk in llm_manager.stream_chat("Tell me a story"):
                chunks.append(chunk)

            assert chunks == ["Hello", " world", "!"]


@pytest.mark.asyncio
async def test_stream_chat_with_stop_event(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    async def mock_stream(**kwargs):
        for chunk in ["Chunk1", "Chunk2", "Chunk3"]:
            stop_event = kwargs.get("stop_event")
            if stop_event and stop_event.is_set():
                break
            yield chunk

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            stop_event = asyncio.Event()
            chunks = []

            async for chunk in llm_manager.stream_chat("Test", stop_event=stop_event):
                chunks.append(chunk)
                if len(chunks) == 2:
                    stop_event.set()

            assert len(chunks) == 2


# =============================================================================
# 工具调用测试
# =============================================================================


@pytest.mark.asyncio
async def test_call_tools_basic(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    mock_backend.chat.return_value = LLMResponse(
        success=True,
        content="I'll call the tool",
        model="gpt-4o-mini",
        tool_calls=[
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "Tokyo"}'},
            }
        ],
        usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information",
                "parameters": {"type": "object", "properties": {"location": {"type": "string"}}},
            },
        }
    ]

    response = await llm_manager.call_tools("What's the weather in Tokyo?", tools)

    assert response.success is True
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["function"]["name"] == "get_weather"


@pytest.mark.asyncio
async def test_call_tools_passes_tools_parameter(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    tools = [{"type": "function", "function": {"name": "calculate", "description": "Perform calculation"}}]

    await llm_manager.call_tools("Calculate 2+2", tools)

    call_args = mock_backend.chat.call_args
    assert "tools" in call_args[1]
    assert call_args[1]["tools"] == tools


# =============================================================================
# 视觉理解测试
# =============================================================================


@pytest.mark.asyncio
async def test_vision_basic(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    images = ["https://example.com/image.jpg"]

    response = await llm_manager.chat_vision("Describe this image", images)

    assert response.success is True
    assert response.content == "Image description"
    assert response.model == "gpt-4-vision-preview"


@pytest.mark.asyncio
async def test_vision_with_multiple_images(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    images = [
        "https://example.com/image1.jpg",
        "https://example.com/image2.jpg",
    ]

    response = await llm_manager.chat_vision("Compare these images", images)

    assert response.success is True
    call_args = mock_backend.vision.call_args
    assert call_args[1]["images"] == images
    assert call_args[1]["model"] == "gpt-4-vision-preview"


@pytest.mark.asyncio
async def test_vision_uses_vision_backend_by_default(setup_llm_manager):
    """测试 vision 默认走 vision profile"""
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat_vision("Test", ["image.jpg"])

    mock_backend.vision.assert_called_once()
    call_args = mock_backend.vision.call_args
    assert call_args[1]["model"] == "gpt-4-vision-preview"


# =============================================================================
# 简化接口测试
# =============================================================================


@pytest.mark.asyncio
async def test_simple_chat_returns_text(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_chat("Hello")

    assert result == "Test response"


@pytest.mark.asyncio
async def test_simple_chat_with_error_returns_error_message(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    mock_backend.chat.return_value = LLMResponse(success=False, content=None, error="API Error")

    result = await llm_manager.simple_chat("Hello")

    # 新格式：失败时 error 含 "全部模型失败 [...]" + 底层错误，便于排查
    assert result.startswith("错误:")
    assert "API Error" in result
    assert "gpt-4o-mini" in result  # 列出尝试过的 model


@pytest.mark.asyncio
async def test_simple_chat_with_profile_parameter(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_chat("Test", client_type="replyer")

    assert result == "Test response"


@pytest.mark.asyncio
async def test_simple_vision_returns_text(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_vision("Describe this", ["image.jpg"])

    assert result == "Image description"


@pytest.mark.asyncio
async def test_simple_vision_with_error_returns_error_message(setup_llm_manager):
    llm_manager, mock_backend, _ = setup_llm_manager

    mock_backend.vision.return_value = LLMResponse(success=False, content=None, error="Vision API Error")

    result = await llm_manager.simple_vision("Test", ["image.jpg"])

    # 新格式：失败时 error 含 "全部模型失败 [...]" + 底层错误
    assert result.startswith("错误:")
    assert "Vision API Error" in result
    assert "gpt-4-vision" in result


# =============================================================================
# 重试机制测试（单模型内的重试，不跨模型）
# =============================================================================


@pytest.mark.asyncio
async def test_retry_on_failure(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试失败时在单模型上自动重试"""
    call_count = 0

    async def failing_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("API Error")
        return LLMResponse(
            success=True,
            content="Success after retries",
            model="gpt-4o-mini",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    mock_backend = MagicMock()
    mock_backend.chat = failing_chat
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            response = await llm_manager.chat("Test")

            assert response.success is True
            assert call_count == 3  # 失败 2 次，第 3 次成功


@pytest.mark.asyncio
async def test_retry_exhaustion(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试单模型重试耗尽（max_retries 默认 3）"""

    async def always_failing_chat(**kwargs):
        raise Exception("Persistent API Error")

    mock_backend = MagicMock()
    mock_backend.chat = always_failing_chat
    mock_backend.get_info.return_value = {"name": "OpenAIBackend"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            response = await llm_manager.chat("Test")

            assert response.success is False
            assert "Persistent API Error" in response.error


@pytest.mark.asyncio
async def test_retry_with_custom_config(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试自定义重试配置"""
    llm_manager._retry_config = RetryConfig(max_retries=2, base_delay=0.1)

    call_count = 0

    async def failing_chat(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception("Error")

    mock_backend = MagicMock()
    mock_backend.chat = failing_chat
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            import time

            start = time.time()
            response = await llm_manager.chat("Test")
            elapsed = time.time() - start

            assert response.success is False
            assert call_count == 2  # max_retries=2 → 1 次首次 + 1 次重试
            assert elapsed >= 0.1


# =============================================================================
# 模型选择策略测试
# =============================================================================


@pytest.mark.asyncio
async def test_sequential_strategy_uses_first_model(llm_manager: LLMManager):
    """sequential 策略：始终取 model_list[0]"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "sequential"},
            },
        },
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # 连续调用 3 次：每次都应该用 m1
            for _ in range(3):
                await llm_manager.chat("Test", client_type="planner")

            used_models = [c[1]["model"] for c in mock_backend.chat.call_args_list]
            assert used_models == ["id-m1", "id-m1", "id-m1"]


@pytest.mark.asyncio
async def test_balance_strategy_picks_least_used(llm_manager: LLMManager):
    """balance 策略：调用次数最少的优先"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "balance"},
            },
        },
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            await llm_manager.chat("Test", client_type="planner")
            first = mock_backend.chat.call_args_list[0][1]["model"]

            await llm_manager.chat("Test", client_type="planner")
            second = mock_backend.chat.call_args_list[1][1]["model"]

            # 第一次取计数少的（并列时取 model_list 中靠前的 m1）；
            # 第二次取计数更少的 m2（balance 选最闲）
            assert first == "id-m1"  # m1 在并列时优先（同计数取 index 小的）
            assert second == "id-m2"  # m2 调用次数少，被选
            assert first != second


@pytest.mark.asyncio
async def test_random_strategy_with_seed(llm_manager: LLMManager):
    """random 策略：seed 固定时 RNG 可重现"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
            {"name": "m3", "model_identifier": "id-m3", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2", "m3"],
                "selection_strategy": {"name": "random", "seed": 42},
            },
        },
    }
    mock_backend1 = _make_mock_backend()
    mock_backend2 = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend1)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)
            await llm_manager.chat("Test", client_type="planner")
            chosen_1 = mock_backend1.chat.call_args[1]["model"]

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend2)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)
            await llm_manager.chat("Test", client_type="planner")
            chosen_2 = mock_backend2.chat.call_args[1]["model"]

    # 相同 seed → 同样选择
    assert chosen_1 == chosen_2


@pytest.mark.asyncio
async def test_invalid_selection_strategy_raises(llm_manager: LLMManager):
    """未知策略在 setup 阶段 fail-fast"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1"],
                "selection_strategy": {"name": "unknown_strategy"},
            },
        },
    }
    with pytest.raises(ValueError, match="selection_strategy"):
        await llm_manager.setup(config)


# =============================================================================
# 故障切换测试
# =============================================================================


@pytest.mark.asyncio
async def test_failover_advances_to_second_model_on_timeout(llm_manager: LLMManager):
    """硬超时 / 异常切到下一个"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai", "max_retries": 0}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "sequential"},
                "hard_timeout_ms": 1000,
                "slow_threshold_ms": 500,
            },
        },
    }
    call_count = {"m1": 0, "m2": 0}

    async def flaky_chat(**kwargs):
        model = kwargs.get("model")
        call_count[model] = call_count.get(model, 0) + 1
        if model == "id-m1":
            raise Exception("upstream timeout")
        return LLMResponse(
            success=True,
            content="success from m2",
            model="id-m2",
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    mock_backend = MagicMock()
    mock_backend.chat = flaky_chat
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            response = await llm_manager.chat("Test", client_type="planner")

            assert response.success is True
            assert response.content == "success from m2"
            assert response.model == "id-m2"
            assert call_count["id-m1"] == 1
            assert call_count["id-m2"] == 1


@pytest.mark.asyncio
async def test_failover_all_models_fail_returns_error_listing_all(llm_manager: LLMManager):
    """全列表失败：返回 success=False 且 error 含所有模型名"""
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai", "max_retries": 0}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "sequential"},
            },
        },
    }

    async def always_fail(**kwargs):
        raise Exception(f"fail-{kwargs.get('model')}")

    mock_backend = MagicMock()
    mock_backend.chat = always_fail
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            response = await llm_manager.chat("Test", client_type="planner")

            assert response.success is False
            assert "全部模型失败" in response.error
            assert "m1" in response.error
            assert "m2" in response.error


@pytest.mark.asyncio
async def test_slow_threshold_warns_but_does_not_switch(llm_manager: LLMManager):
    """慢调用仅告警，不切换模型

    验证点：单次慢调用后直接返回成功结果（不再尝试 m2）。
    告警日志通过 loguru 发出（不走 stdlib logging），本测试不强行捕获，
    由日志收集链路负责——重点守住"不切换"行为契约。
    """
    config = {
        "llm_providers": [{"name": "p1", "client_type": "openai"}],
        "llm_models": [
            {"name": "m1", "model_identifier": "id-m1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id-m2", "api_provider": "p1"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "sequential"},
                "slow_threshold_ms": 0,  # 任何调用都触发告警
                "hard_timeout_ms": 10000,
            },
        },
    }

    async def slow_but_succeed(**kwargs):
        await asyncio.sleep(0.01)
        return LLMResponse(
            success=True,
            content="ok",
            model="id-m1",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    mock_backend = MagicMock()
    mock_backend.chat = AsyncMock(side_effect=slow_but_succeed)
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)
            response = await llm_manager.chat("Test", client_type="planner")

            # 慢调用仍返回成功（不切换）
            assert response.success is True
            assert response.content == "ok"
            # 不切换：只调了 1 次
            assert len(mock_backend.chat.call_args_list) == 1


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_token_usage_summary(setup_llm_manager):
    llm_manager, _, mock_token_manager = setup_llm_manager

    mock_token_manager.return_value.format_total_cost_summary.return_value = (
        "=== 所有模型费用汇总 ===\n总调用次数: 100\n总Token: 50000\n总费用: 1.234567"
    )

    summary = llm_manager.get_token_usage_summary()

    assert "100" in summary
    assert "50000" in summary
    assert "1.234567" in summary


@pytest.mark.asyncio
async def test_get_token_usage_summary_when_not_initialized(llm_manager: LLMManager):
    summary = llm_manager.get_token_usage_summary()

    assert summary == "Token 管理器未初始化"


@pytest.mark.asyncio
async def test_get_client_info(setup_llm_manager):
    """测试 get_client_info 返回 provider 视角（含 profile 关联）"""
    llm_manager, _, _ = setup_llm_manager

    info = llm_manager.get_client_info()

    assert isinstance(info, dict)
    assert "test" in info
    assert "client" in info["test"]
    assert "profiles" in info["test"]
    # 1 个 provider 被 3 个 profile 共享
    assert set(info["test"]["profiles"]) == {"planner", "replyer", "vision"}


@pytest.mark.asyncio
async def test_get_client_config_returns_profile_runtime(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 get_client_config 返回 profile 运行时视图"""
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            cfg = llm_manager.get_client_config("planner")
            assert cfg is not None
            assert cfg["profile_name"] == "planner"
            assert cfg["hard_timeout_ms"] >= 1000
            assert cfg["slow_threshold_ms"] >= 100
            assert cfg["selection_strategy"] == "sequential"
            assert len(cfg["models"]) == 1
            assert cfg["models"][0]["model_identifier"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_get_client_config_returns_none_for_unconfigured(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    assert llm_manager.get_client_config("nonexistent") is None


# =============================================================================
# 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_provider_client_unknown_raises(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="provider 'unknown_backend' 不存在"):
        llm_manager.get_provider_client("unknown_backend")


@pytest.mark.asyncio
async def test_chat_with_unknown_profile(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="未配置"):
        await llm_manager.chat("Test", client_type="unknown_backend")


@pytest.mark.asyncio
async def test_vision_with_unknown_profile(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="未配置"):
        await llm_manager.chat_vision("Test", ["image.jpg"], client_type="unknown_backend")


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_all_providers(llm_manager: LLMManager):
    """3 个独立 provider 时各清理 1 次"""
    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai"},
            {"name": "p2", "client_type": "openai"},
            {"name": "p3", "client_type": "openai"},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id2", "api_provider": "p2"},
            {"name": "m3", "model_identifier": "id3", "api_provider": "p3"},
        ],
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
            "replyer": {"model_list": ["m2"]},
            "vision": {"model_list": ["m3"]},
        },
    }
    mock_backends = []
    for _ in range(3):
        mb = MagicMock()
        mb.cleanup = AsyncMock()
        mb.get_info.return_value = {"name": "OpenAIClient"}
        mock_backends.append(mb)

    with patch.dict(_client_impls, {"openai": MagicMock(side_effect=mock_backends)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)
            assert len(llm_manager._provider_clients) == 3

            await llm_manager.cleanup()

            for mb in mock_backends:
                assert mb.cleanup.call_count == 1
            assert len(llm_manager._provider_clients) == 0
            assert len(llm_manager._providers) == 0


@pytest.mark.asyncio
async def test_cleanup_handles_provider_errors(llm_manager: LLMManager):
    """清理时单个 provider 错误不阻断其他清理"""

    async def failing_cleanup():
        raise Exception("Cleanup error")

    mock_backend1 = MagicMock()
    mock_backend1.cleanup = failing_cleanup
    mock_backend1.get_info.return_value = {"name": "Backend1"}

    mock_backend2 = MagicMock()
    mock_backend2.cleanup = AsyncMock()
    mock_backend2.get_info.return_value = {"name": "Backend2"}

    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai"},
            {"name": "p2", "client_type": "openai"},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id2", "api_provider": "p2"},
        ],
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
            "replyer": {"model_list": ["m2"]},
        },
    }

    with patch.dict(_client_impls, {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            await llm_manager.cleanup()

            mock_backend2.cleanup.assert_awaited_once()
            assert len(llm_manager._provider_clients) == 0


@pytest.mark.asyncio
async def test_cleanup_dedups_shared_provider(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """同一 provider 被多个 profile 共享时只 cleanup 一次"""
    mock_backend = MagicMock()
    mock_backend.cleanup = AsyncMock()
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)
            # 3 个 profile 共享同一 provider 客户端
            assert llm_manager._provider_clients["test"] is mock_backend

            await llm_manager.cleanup()

            assert mock_backend.cleanup.call_count == 1


# =============================================================================
# 消息构建测试
# =============================================================================


@pytest.mark.asyncio
async def test_build_messages_without_system(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    messages = llm_manager._build_messages("Hello", None)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_build_messages_with_system(setup_llm_manager):
    llm_manager, _, _ = setup_llm_manager

    messages = llm_manager._build_messages("Hello", "You are helpful")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


# =============================================================================
# Provider 共享 / 引用测试
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_profiles_share_same_provider_client(
    llm_manager: LLMManager, mock_config: Dict[str, Any]
):
    """3 个 profile 共享同一 provider 客户端实例"""
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # 3 个 profile 都指向 provider "test"
            assert llm_manager._provider_clients["test"] is mock_backend
            assert len(llm_manager.list_providers()) == 1


@pytest.mark.asyncio
async def test_multiple_independent_providers_create_separate_clients(llm_manager: LLMManager):
    """不同 provider 各自独立 client"""
    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai"},
            {"name": "p2", "client_type": "openai"},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "id1", "api_provider": "p1"},
            {"name": "m2", "model_identifier": "id2", "api_provider": "p2"},
        ],
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
            "replyer": {"model_list": ["m2"]},
        },
    }
    mock_backend1 = _make_mock_backend()
    mock_backend2 = _make_mock_backend()

    with patch.dict(
        _client_impls,
        {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])},
    ):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            assert llm_manager._provider_clients["p1"] is mock_backend1
            assert llm_manager._provider_clients["p2"] is mock_backend2


# =============================================================================
# RetryConfig 测试
# =============================================================================


def test_retry_config_defaults():
    config = RetryConfig()

    assert config.max_retries == 3
    assert config.base_delay == 1.0
    assert config.max_delay == 10.0


def test_retry_config_custom_values():
    config = RetryConfig(max_retries=5, base_delay=2.0, max_delay=20.0)

    assert config.max_retries == 5
    assert config.base_delay == 2.0
    assert config.max_delay == 20.0


# =============================================================================
# LLMResponse 测试
# =============================================================================


def test_llm_response_defaults():
    response = LLMResponse(success=True)

    assert response.success is True
    assert response.content is None
    assert response.model is None
    assert response.usage is None
    assert response.tool_calls == []
    assert response.reasoning_content is None
    assert response.error is None


def test_llm_response_with_all_fields():
    response = LLMResponse(
        success=True,
        content="Test content",
        model="gpt-4",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        tool_calls=[{"id": "call_1"}],
        reasoning_content="Chain of thought",
        error=None,
    )

    assert response.success is True
    assert response.content == "Test content"
    assert response.model == "gpt-4"
    assert response.usage["total_tokens"] == 15
    assert len(response.tool_calls) == 1
    assert response.reasoning_content == "Chain of thought"


def test_llm_response_error_case():
    response = LLMResponse(success=False, content=None, error="API Error")

    assert response.success is False
    assert response.content is None
    assert response.error == "API Error"


# =============================================================================
# ProfileNames 回归保护（新用途名）
# =============================================================================


def test_profile_names_includes_all_six_purposes():
    assert "planner" in ProfileNames.ALL
    assert "replyer" in ProfileNames.ALL
    assert "summary" in ProfileNames.ALL
    assert "minecraft" in ProfileNames.ALL
    assert "vision" in ProfileNames.ALL
    assert "simulator" in ProfileNames.ALL


def test_profile_names_is_valid():
    assert ProfileNames.is_valid("planner") is True
    assert ProfileNames.is_valid("vision") is True
    assert ProfileNames.is_valid("unknown") is False


def test_client_type_alias_points_to_profile_names():
    """ClientType 兼容别名仍指向 ProfileNames（防止误以为旧 ClientType 仍可独立构造）"""
    assert ClientType is ProfileNames


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])