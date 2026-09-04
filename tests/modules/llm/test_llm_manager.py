"""
LLMManager 单元测试（provider-reference 配置格式）

测试 LLMManager 的所有核心功能：
- 初始化和配置（provider-reference 格式）
- 聊天接口（chat、stream_chat）
- 工具调用（call_tools）
- 视觉理解（vision）
- 简化接口（simple_chat、simple_vision）
- 重试机制（_call_with_retry）
- 统计信息（token usage、backend info）
- 新增：provider fail-fast、role 覆盖、共享 client 等

运行: uv run pytest tests/modules/llm/test_llm_manager.py -v

=== 新配置格式说明 ===

旧格式（已废弃）：
    {
        "llm": {"backend": "openai", "api_key": "...", "model": "..."},
        "llm_fast": {"backend": "openai", "api_key": "...", "model": "..."},
    }

新格式（provider-reference）：
    {
        "llm_providers": [
            {"name": "test", "client_type": "openai", "base_url": "...", "api_key": "..."}
        ],
        "llm": {"provider": "test", "model": "..."},
        "llm_fast": {"provider": "test", "model": "..."},
    }

=== Mock 策略 ===

每个 Client 实现通过 src.modules.llm.clients.base.register_client() 在
模块导入时注册到 _client_impls 字典。注册时存的是原始类对象的引用，
而不是通过模块属性查找。

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
from src.modules.llm.manager import LLMManager, LLMResponse, RetryConfig, ClientType

# =============================================================================
# Test Constants - 标准配置
# =============================================================================

# 一个标准的 provider 配置（用于验证 provider config 直接传给 Client 构造器）
PROVIDER_CONFIG = {
    "name": "test",
    "client_type": "openai",
    "base_url": "https://api.test.com/v1",
    "api_key": "test-api-key",
}

# 标准 mock_config：1 个 provider "test"，3 个 role（llm / llm_fast / vlm）共享同一 provider
STANDARD_MOCK_CONFIG: Dict[str, Any] = {
    "llm_providers": [PROVIDER_CONFIG],
    "llm": {
        "provider": "test",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_tokens": 2048,
    },
    "llm_fast": {
        "provider": "test",
        "model": "gpt-3.5-turbo",
        "temperature": 0.2,
        "max_tokens": 1024,
    },
    "vlm": {
        "provider": "test",
        "model": "gpt-4-vision-preview",
        "temperature": 0.3,
        "max_tokens": 1024,
    },
}


# =============================================================================
# Helper: 标准 mock backend
# =============================================================================


def _make_mock_backend() -> MagicMock:
    """构造一个标准 mock backend，覆盖 chat/stream_chat/vision/cleanup/get_info。"""
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
    """标准测试配置（1 个 provider 被 3 个 role 共享）"""
    return STANDARD_MOCK_CONFIG


@pytest.fixture
def llm_manager() -> LLMManager:
    """未初始化的 LLMManager 实例"""
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
async def test_setup_initializes_backends(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 setup 初始化 providers 和 roles"""
    mock_backend = _make_mock_backend()
    mock_backend_class = MagicMock(return_value=mock_backend)

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # mock_config 有 1 个 provider + 3 个 role
            # 每个 role 用 merged config 独立初始化，所以共 3 次
            assert mock_backend_class.call_count == 3
            assert len(llm_manager._providers) == 1
            assert "test" in llm_manager._providers
            assert len(llm_manager._clients) == 3
            assert "llm" in llm_manager._clients
            assert "llm_fast" in llm_manager._clients
            assert "vlm" in llm_manager._clients


@pytest.mark.asyncio
async def test_setup_with_custom_config(llm_manager: LLMManager):
    """测试使用自定义 provider 配置初始化（验证 pcfg 是 provider config 而非 role config）"""
    custom_provider = {
        "name": "custom",
        "client_type": "openai",
        "model": "custom-model",
        "api_key": "custom-key",
        "base_url": "https://custom.api.com/v1",
    }
    config = {
        "llm_providers": [custom_provider],
        "llm": {"provider": "custom", "model": "gpt-4"},
    }

    mock_backend_class = MagicMock(return_value=MagicMock())
    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # 验证 client 使用 merged config（provider + role 合并），model 是 role 的 gpt-4
            call_args = mock_backend_class.call_args[0][0]
            assert call_args["model"] == "gpt-4"
            assert call_args["api_key"] == "custom-key"

            # role config 应该 merge 到 _profile_configs
            # merged = {**provider_cfg, **role_raw}
            # 所以 role 的 model 优先级高于 provider
            role_config = llm_manager._profile_configs["llm"]
            assert role_config["model"] == "gpt-4"
            assert role_config["api_key"] == "custom-key"
            assert role_config["base_url"] == "https://custom.api.com/v1"


@pytest.mark.asyncio
async def test_setup_unknown_client_type_raises_error(llm_manager: LLMManager):
    """测试未注册的 client_type 立即 fail-fast（不再静默 fallback 到 OpenAI）"""
    config = {
        "llm_providers": [
            {"name": "test", "client_type": "unknown_backend"},
        ],
        "llm": {"provider": "test", "model": "test-model"},
    }

    with pytest.raises(ValueError, match="未注册的客户端类型"):
        await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_unknown_provider_raises_error(llm_manager: LLMManager):
    """测试 role 引用了不存在的 provider 时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [{"name": "real", "client_type": "openai"}],
        "llm": {"provider": "nonexistent", "model": "test-model"},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="provider 'nonexistent' 不存在"):
            await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_duplicate_provider_name_raises_error(llm_manager: LLMManager):
    """测试 llm_providers 中存在重复 name 时 fail-fast"""
    mock_backend_class = MagicMock(return_value=MagicMock())
    config = {
        "llm_providers": [
            {"name": "test", "client_type": "openai"},
            {"name": "test", "client_type": "openai"},  # 重复
        ],
        "llm": {"provider": "test", "model": "x"},
    }

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="重复的 provider name"):
            await llm_manager.setup(config)


@pytest.mark.asyncio
async def test_setup_initializes_token_manager(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 setup 初始化 TokenUsageManager"""
    mock_backend_class = MagicMock(return_value=_make_mock_backend())

    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager") as mock_token_manager:
            await llm_manager.setup(mock_config)

            assert llm_manager._token_manager is not None
            mock_token_manager.assert_called_once_with(use_global=True)


# =============================================================================
# 聊天接口测试
# =============================================================================


@pytest.mark.asyncio
async def test_chat_basic(setup_llm_manager):
    """测试基本聊天功能"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello, world!")

    assert response.success is True
    assert response.content == "Test response"
    assert response.model == "gpt-4o-mini"
    assert response.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_chat_with_system_message(setup_llm_manager):
    """测试带系统消息的聊天"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", system_message="You are a helpful assistant")

    assert response.success is True
    # 验证消息格式正确
    call_args = mock_backend.chat.call_args
    messages = call_args[1]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful assistant"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_chat_with_temperature(setup_llm_manager):
    """测试带温度参数的聊天"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", temperature=0.9)

    assert response.success is True
    call_args = mock_backend.chat.call_args
    assert call_args[1]["temperature"] == 0.9


@pytest.mark.asyncio
async def test_chat_with_max_tokens(setup_llm_manager):
    """测试带 max_tokens 参数的聊天"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", max_tokens=100)

    assert response.success is True
    call_args = mock_backend.chat.call_args
    assert call_args[1]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_chat_with_custom_backend(setup_llm_manager):
    """测试使用自定义 client_type"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", client_type="llm_fast")

    assert response.success is True


@pytest.mark.asyncio
async def test_chat_records_token_usage(setup_llm_manager):
    """测试聊天记录 token 使用量"""
    llm_manager, mock_backend, mock_token_manager = setup_llm_manager

    await llm_manager.chat("Hello")

    # 验证 token 使用量被记录
    mock_token_manager.return_value.record_usage.assert_called_once_with(
        model_name="gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )


# =============================================================================
# 流式聊天测试
# =============================================================================


@pytest.mark.asyncio
async def test_stream_chat_basic(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试基本流式聊天"""

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
    """测试流式聊天支持停止事件"""

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


@pytest.mark.asyncio
async def test_stream_chat_with_system_message(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试流式聊天带系统消息"""

    async def mock_stream(**kwargs):
        messages = kwargs.get("messages", [])
        if len(messages) == 2 and messages[0]["role"] == "system":
            yield "OK"
        else:
            yield "FAIL"

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            chunks = []
            async for chunk in llm_manager.stream_chat("Hello", system_message="You are helpful"):
                chunks.append(chunk)

            assert chunks == ["OK"]


# =============================================================================
# 工具调用测试
# =============================================================================


@pytest.mark.asyncio
async def test_call_tools_basic(setup_llm_manager):
    """测试基本工具调用"""
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
async def test_call_tools_with_system_message(setup_llm_manager):
    """测试工具调用带系统消息"""
    llm_manager, mock_backend, _ = setup_llm_manager

    tools = [{"type": "function", "function": {"name": "test"}}]

    await llm_manager.call_tools("Test", tools, system_message="You are a tool-using assistant")

    call_args = mock_backend.chat.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a tool-using assistant"


@pytest.mark.asyncio
async def test_call_tools_passes_tools_parameter(setup_llm_manager):
    """测试工具调用正确传递 tools 参数"""
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
    """测试基本视觉理解"""
    llm_manager, mock_backend, _ = setup_llm_manager

    images = ["https://example.com/image.jpg"]

    response = await llm_manager.chat_vision("Describe this image", images)

    assert response.success is True
    assert response.content == "Image description"
    assert response.model == "gpt-4-vision-preview"


@pytest.mark.asyncio
async def test_vision_with_multiple_images(setup_llm_manager):
    """测试多图片视觉理解"""
    llm_manager, mock_backend, _ = setup_llm_manager

    images = [
        "https://example.com/image1.jpg",
        "https://example.com/image2.jpg",
    ]

    response = await llm_manager.chat_vision("Compare these images", images)

    assert response.success is True
    call_args = mock_backend.vision.call_args
    assert call_args[1]["images"] == images


@pytest.mark.asyncio
async def test_vision_with_system_message(setup_llm_manager):
    """测试视觉理解带系统消息"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat_vision(
        "Describe this", ["https://example.com/image.jpg"], system_message="You are a vision expert"
    )

    assert response.success is True
    call_args = mock_backend.vision.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"


@pytest.mark.asyncio
async def test_vision_uses_vlm_backend_by_default(setup_llm_manager):
    """测试 vision 默认使用 vlm client"""
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat_vision("Test", ["image.jpg"])

    mock_backend.vision.assert_called_once()


# =============================================================================
# 简化接口测试
# =============================================================================


@pytest.mark.asyncio
async def test_simple_chat_returns_text(setup_llm_manager):
    """测试 simple_chat 直接返回文本"""
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_chat("Hello")

    assert result == "Test response"


@pytest.mark.asyncio
async def test_simple_chat_with_error_returns_error_message(setup_llm_manager):
    """测试 simple_chat 错误处理"""
    llm_manager, mock_backend, _ = setup_llm_manager

    mock_backend.chat.return_value = LLMResponse(success=False, content=None, error="API Error")

    result = await llm_manager.simple_chat("Hello")

    assert result == "错误: API Error"


@pytest.mark.asyncio
async def test_simple_chat_with_backend_parameter(setup_llm_manager):
    """测试 simple_chat 支持 client_type 参数"""
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_chat("Test", client_type="llm_fast")

    assert result == "Test response"


@pytest.mark.asyncio
async def test_simple_vision_returns_text(setup_llm_manager):
    """测试 simple_vision 直接返回文本"""
    llm_manager, mock_backend, _ = setup_llm_manager

    result = await llm_manager.simple_vision("Describe this", ["image.jpg"])

    assert result == "Image description"


@pytest.mark.asyncio
async def test_simple_vision_with_error_returns_error_message(setup_llm_manager):
    """测试 simple_vision 错误处理"""
    llm_manager, mock_backend, _ = setup_llm_manager

    mock_backend.vision.return_value = LLMResponse(success=False, content=None, error="Vision API Error")

    result = await llm_manager.simple_vision("Test", ["image.jpg"])

    assert result == "错误: Vision API Error"


# =============================================================================
# 重试机制测试
# =============================================================================


@pytest.mark.asyncio
async def test_retry_on_failure(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试失败时自动重试"""
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
            assert call_count == 3  # 失败2次，第3次成功


@pytest.mark.asyncio
async def test_retry_exhaustion(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试重试次数耗尽"""

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
            assert call_count == 2  # max_retries=2
            # 验证有延迟（base_delay=0.1）
            assert elapsed >= 0.1


@pytest.mark.asyncio
async def test_retry_exponential_backoff(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试指数退避"""
    llm_manager._retry_config = RetryConfig(max_retries=3, base_delay=0.1)

    call_times = []

    async def failing_chat(**kwargs):
        call_times.append(asyncio.get_event_loop().time())
        raise Exception("Error")

    mock_backend = MagicMock()
    mock_backend.chat = failing_chat
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            await llm_manager.chat("Test")

            # 验证延迟逐渐增加（指数退避）
            assert len(call_times) == 3
            delay1 = call_times[1] - call_times[0]
            delay2 = call_times[2] - call_times[1]
            # 第二次延迟应该大于第一次（近似）
            assert delay2 >= delay1 * 0.9  # 允许一些误差


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_token_usage_summary(setup_llm_manager):
    """测试获取 token 使用摘要"""
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
    """测试 token 管理器未初始化时的摘要"""
    summary = llm_manager.get_token_usage_summary()

    assert summary == "Token 管理器未初始化"


@pytest.mark.asyncio
async def test_get_client_info(setup_llm_manager):
    """测试获取 client 信息"""
    llm_manager, mock_backend, _ = setup_llm_manager

    info = llm_manager.get_client_info()

    assert isinstance(info, dict)
    assert "llm" in info
    assert "llm_fast" in info
    assert "vlm" in info
    # 验证信息结构
    assert "client" in info["llm"]
    assert "config" in info["llm"]


@pytest.mark.asyncio
async def test_get_client_info_returns_correct_structure(setup_llm_manager):
    """测试 client 信息结构"""
    llm_manager, mock_backend, _ = setup_llm_manager

    info = llm_manager.get_client_info()

    for _client_name, client_info in info.items():
        assert "client" in client_info
        assert "config" in client_info


@pytest.mark.asyncio
async def test_get_client_config_returns_merged_config(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 get_client_config 返回 role merged config（provider + role，role 字段优先）"""
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            cfg = llm_manager.get_client_config("llm")
            assert cfg is not None
            # role 的 model 应覆盖 provider 的 model（如果有）
            assert cfg["model"] == "gpt-4o-mini"
            # provider 的字段被合并进来
            assert cfg["api_key"] == "test-api-key"
            assert cfg["base_url"] == "https://api.test.com/v1"


@pytest.mark.asyncio
async def test_get_client_config_returns_none_for_unconfigured(setup_llm_manager):
    """测试 get_client_config 对未配置角色返回 None"""
    llm_manager, _, _ = setup_llm_manager

    # 用 vlm role 测试不存在的角色
    assert llm_manager.get_client_config("nonexistent") is None


# =============================================================================
# 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_nonexistent_backend_raises_error(setup_llm_manager):
    """测试获取不存在的 client 抛出错误"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'nonexistent' 未配置"):
        llm_manager._get_client("nonexistent")


@pytest.mark.asyncio
async def test_chat_with_nonexistent_backend(setup_llm_manager):
    """测试使用不存在的 client 进行聊天"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'unknown_backend' 未配置"):
        await llm_manager.chat("Test", client_type="unknown_backend")


@pytest.mark.asyncio
async def test_vision_with_nonexistent_backend(setup_llm_manager):
    """测试使用不存在的 client 进行视觉理解"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'unknown_backend' 未配置"):
        await llm_manager.chat_vision("Test", ["image.jpg"], client_type="unknown_backend")


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_all_backends(llm_manager: LLMManager):
    """测试清理所有 client（多 provider 各清理一次）"""
    # 用 3 个独立 provider，cleanup 会被调用 3 次
    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai"},
            {"name": "p2", "client_type": "openai"},
            {"name": "p3", "client_type": "openai"},
        ],
        "llm": {"provider": "p1", "model": "m1"},
        "llm_fast": {"provider": "p2", "model": "m2"},
        "vlm": {"provider": "p3", "model": "m3"},
    }
    # 每个 provider 一个独立 mock backend，cleanup 才能分别计数
    mock_backends = []
    for _ in range(3):
        mb = MagicMock()
        mb.cleanup = AsyncMock()
        mb.get_info.return_value = {"name": "OpenAIClient"}
        mock_backends.append(mb)

    with patch.dict(_client_impls, {"openai": MagicMock(side_effect=mock_backends)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)
            assert len(llm_manager._clients) == 3

            await llm_manager.cleanup()

            # 3 个独立 provider，cleanup 各调用 1 次
            for mb in mock_backends:
                assert mb.cleanup.call_count == 1
            assert len(llm_manager._clients) == 0
            assert len(llm_manager._profile_configs) == 0
            assert len(llm_manager._providers) == 0


@pytest.mark.asyncio
async def test_cleanup_handles_backend_errors(llm_manager: LLMManager):
    """测试清理时处理单个 client 错误（不中断其他清理）"""

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
        "llm": {"provider": "p1", "model": "m1"},
        "llm_fast": {"provider": "p2", "model": "m2"},
    }

    with patch.dict(_client_impls, {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # cleanup 不抛出异常，继续清理其他 client
            await llm_manager.cleanup()

            # backend1 的 cleanup 抛错但不影响 backend2 被清理
            mock_backend2.cleanup.assert_awaited_once()
            assert len(llm_manager._clients) == 0


@pytest.mark.asyncio
async def test_cleanup_dedups_same_client_instance(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试共享同一 client 实例的多个 role 只清理一次（id-based dedup）"""
    mock_backend = MagicMock()
    mock_backend.cleanup = AsyncMock()
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)
            # 3 个 role 共享同一个 client（同一 provider）
            assert llm_manager._clients["llm"] is llm_manager._clients["llm_fast"]
            assert llm_manager._clients["llm"] is llm_manager._clients["vlm"]

            await llm_manager.cleanup()

            # 同一个 client 按 id 去重，只调用 cleanup 一次
            assert mock_backend.cleanup.call_count == 1


# =============================================================================
# 消息构建测试
# =============================================================================


@pytest.mark.asyncio
async def test_build_messages_without_system(setup_llm_manager):
    """测试不带系统消息的消息构建"""
    llm_manager, _, _ = setup_llm_manager

    messages = llm_manager._build_messages("Hello", None)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_build_messages_with_system(setup_llm_manager):
    """测试带系统消息的消息构建"""
    llm_manager, _, _ = setup_llm_manager

    messages = llm_manager._build_messages("Hello", "You are helpful")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


# =============================================================================
# Provider/Role 配置合并测试（新增）
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_roles_share_same_client_instance(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试多个 role 共享同一 provider 时共享同一 client 实例"""
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # 3 个 role 都指向 provider "test"，应该共享同一 client 实例
            assert llm_manager._clients["llm"] is llm_manager._clients["llm_fast"]
            assert llm_manager._clients["llm"] is llm_manager._clients["vlm"]
            # provider 仅注册 1 次
            assert len(llm_manager._providers) == 1


@pytest.mark.asyncio
async def test_role_config_overrides_provider_model(llm_manager: LLMManager):
    """测试 role 的 model 覆盖 provider 的 model（merged config）"""
    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai", "model": "provider-default-model"},
        ],
        "llm": {"provider": "p1", "model": "role-specific-model"},
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # role config 中明确指定 model，应保留 role 的值
            assert llm_manager._profile_configs["llm"]["model"] == "role-specific-model"


@pytest.mark.asyncio
async def test_role_config_overrides_provider_base_url(llm_manager: LLMManager):
    """测试 role 的 base_url 覆盖 provider 的 base_url"""
    config = {
        "llm_providers": [
            {
                "name": "p1",
                "client_type": "openai",
                "base_url": "https://provider.api.com/v1",
                "api_key": "provider-key",
            }
        ],
        "llm": {
            "provider": "p1",
            "model": "m",
            "base_url": "https://role.api.com/v2",
        },
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # role 的 base_url 应覆盖 provider 的 base_url
            assert llm_manager._profile_configs["llm"]["base_url"] == "https://role.api.com/v2"


@pytest.mark.asyncio
async def test_role_config_overrides_provider_api_key(llm_manager: LLMManager):
    """测试 role 的 api_key 覆盖 provider 的 api_key"""
    config = {
        "llm_providers": [
            {
                "name": "p1",
                "client_type": "openai",
                "base_url": "https://api.com/v1",
                "api_key": "provider-key",
            }
        ],
        "llm": {
            "provider": "p1",
            "model": "m",
            "api_key": "role-key",
        },
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # role 的 api_key 应覆盖 provider 的 api_key
            assert llm_manager._profile_configs["llm"]["api_key"] == "role-key"


@pytest.mark.asyncio
async def test_role_config_inherits_unspecified_provider_fields(llm_manager: LLMManager):
    """测试 role config 未指定的字段应继承 provider config"""
    config = {
        "llm_providers": [
            {
                "name": "p1",
                "client_type": "openai",
                "base_url": "https://provider.api.com/v1",
                "api_key": "provider-key",
                "temperature": 0.3,
            }
        ],
        "llm": {
            "provider": "p1",
            "model": "role-model",
            # 没指定 api_key / base_url / temperature
        },
    }
    mock_backend = _make_mock_backend()
    with patch.dict(_client_impls, {"openai": MagicMock(return_value=mock_backend)}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            merged = llm_manager._profile_configs["llm"]
            # 指定的字段保留
            assert merged["model"] == "role-model"
            # 未指定的字段从 provider 继承
            assert merged["api_key"] == "provider-key"
            assert merged["base_url"] == "https://provider.api.com/v1"
            assert merged["temperature"] == 0.3


@pytest.mark.asyncio
async def test_multiple_independent_providers_create_separate_clients(llm_manager: LLMManager):
    """测试不同 provider 指向不同 name 时会创建独立的 client 实例"""
    config = {
        "llm_providers": [
            {"name": "p1", "client_type": "openai"},
            {"name": "p2", "client_type": "openai"},
        ],
        "llm": {"provider": "p1", "model": "m1"},
        "llm_fast": {"provider": "p2", "model": "m2"},
    }
    mock_backend1 = _make_mock_backend()
    mock_backend2 = _make_mock_backend()

    # 用 side_effect 控制 provider1 → mock1, provider2 → mock2
    with patch.dict(
        _client_impls,
        {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])},
    ):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # 不同的 provider 应有独立的 client 实例
            assert llm_manager._clients["llm"] is not llm_manager._clients["llm_fast"]
            # 但 _providers 有 2 个独立条目
            assert len(llm_manager._providers) == 2


# =============================================================================
# RetryConfig 测试
# =============================================================================


def test_retry_config_defaults():
    """测试 RetryConfig 默认值"""
    config = RetryConfig()

    assert config.max_retries == 3
    assert config.base_delay == 1.0
    assert config.max_delay == 10.0


def test_retry_config_custom_values():
    """测试自定义 RetryConfig"""
    config = RetryConfig(max_retries=5, base_delay=2.0, max_delay=20.0)

    assert config.max_retries == 5
    assert config.base_delay == 2.0
    assert config.max_delay == 20.0


# =============================================================================
# LLMResponse 测试
# =============================================================================


def test_llm_response_defaults():
    """测试 LLMResponse 默认值"""
    response = LLMResponse(success=True)

    assert response.success is True
    assert response.content is None
    assert response.model is None
    assert response.usage is None
    assert response.tool_calls == []
    assert response.reasoning_content is None
    assert response.error is None


def test_llm_response_with_all_fields():
    """测试 LLMResponse 所有字段"""
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
    """测试 LLMResponse 错误情况"""
    response = LLMResponse(success=False, content=None, error="API Error")

    assert response.success is False
    assert response.content is None
    assert response.error == "API Error"


# =============================================================================
# A1.6 回归保护：llm_agenda profile 注册（由 llm_outline 改名）
# =============================================================================


def test_client_type_includes_llm_agenda():
    assert "llm_agenda" in ClientType.ALL
    assert ClientType.LLM_AGENDA == "llm_agenda"
    assert ClientType.LLM_OUTLINE == "llm_agenda"
    assert ClientType.LLM_AGENDA == ClientType.LLM_OUTLINE
    assert len(ClientType.ALL) == len(set(ClientType.ALL))


def test_client_type_is_valid_recognizes_llm_agenda():
    assert ClientType.is_valid("llm_agenda") is True


@pytest.mark.asyncio
async def test_setup_registers_llm_agenda_profile(llm_manager: LLMManager):
    config = {
        "llm_providers": [PROVIDER_CONFIG],
        "llm_agenda": {
            "provider": "test",
            "model": "agenda-model",
            "temperature": 0.5,
        },
    }
    mock_backend_class = MagicMock(return_value=MagicMock())
    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

    assert "llm_agenda" in llm_manager._clients
    assert "llm_agenda" in llm_manager._profile_configs
    profile = llm_manager._profile_configs["llm_agenda"]
    assert profile["model"] == "agenda-model"
    assert profile["temperature"] == 0.5


@pytest.mark.asyncio
async def test_setup_does_not_register_legacy_llm_outline_as_separate(
    llm_manager: LLMManager,
):
    config = {
        "llm_providers": [PROVIDER_CONFIG],
        "llm_outline": {
            "provider": "test",
            "model": "outline-model",
        },
    }
    mock_backend_class = MagicMock(return_value=MagicMock())
    with patch.dict(_client_impls, {"openai": mock_backend_class}):
        with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

    assert "llm_outline" not in llm_manager._clients


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
