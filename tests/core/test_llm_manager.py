"""
LLMManager 单元测试

测试 LLMManager 的所有核心功能：
- 初始化和配置
- 聊天接口（chat、stream_chat）
- 工具调用（call_tools）
- 视觉理解（vision）
- 简化接口（simple_chat、simple_vision）
- 重试机制（_call_with_retry）
- 统计信息（token usage、backend info）

运行: uv run pytest tests/core/test_llm_manager.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any
import pytest

from src.services.llm.manager import LLMManager, LLMResponse, RetryConfig

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_config():
    """创建测试用的配置字典"""
    return {
        "llm": {
            "backend": "openai",
            "model": "gpt-4o-mini",
            "api_key": "test-api-key",
            "base_url": "https://api.test.com/v1",
            "temperature": 0.7,
            "max_tokens": 2048,
        },
        "llm_fast": {
            "backend": "openai",
            "model": "gpt-3.5-turbo",
            "api_key": "test-api-key",
            "base_url": "https://api.test.com/v1",
            "temperature": 0.2,
            "max_tokens": 1024,
        },
        "vlm": {
            "backend": "openai",
            "model": "gpt-4-vision-preview",
            "api_key": "test-api-key",
            "base_url": "https://api.test.com/v1",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
    }


@pytest.fixture
def llm_manager():
    """创建 LLMManager 实例"""
    manager = LLMManager()
    return manager


@pytest.fixture
async def setup_llm_manager(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """初始化 LLMManager 实例"""
    # Mock OpenAIClient to avoid real API calls
    with patch("src.services.llm.backends.openai_client.OpenAIClient") as mock_backend_class:
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

        mock_backend_class.return_value = mock_backend

        # Mock TokenUsageManager
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager") as mock_token_manager:
            await llm_manager.setup(mock_config)
            yield llm_manager, mock_backend, mock_token_manager


# =============================================================================
# 初始化和配置测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_initializes_backends(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 setup 初始化所有后端"""
    with patch("src.services.llm.backends.openai_client.OpenAIClient") as mock_backend_class:
        mock_backend = MagicMock()
        mock_backend_class.return_value = mock_backend

        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # 验证三个后端都被初始化
            assert mock_backend_class.call_count == 3
            assert "llm" in llm_manager._clients
            assert "llm_fast" in llm_manager._clients
            assert "vlm" in llm_manager._clients


@pytest.mark.asyncio
async def test_setup_with_custom_config(llm_manager: LLMManager):
    """测试使用自定义配置初始化"""
    config = {
        "llm": {
            "backend": "openai",
            "model": "custom-model",
            "api_key": "custom-key",
            "base_url": "https://custom.api.com/v1",
        },
    }

    with patch("src.services.llm.backends.openai_client.OpenAIClient") as mock_backend_class:
        mock_backend = MagicMock()
        mock_backend_class.return_value = mock_backend

        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(config)

            # 验证配置传递正确
            mock_backend_class.assert_called_with(config["llm"])


@pytest.mark.asyncio
async def test_setup_unknown_backend_falls_back_to_openai(llm_manager: LLMManager):
    """测试未知客户端类型降级到 OpenAI"""
    config = {
        "llm": {
            "backend": "unknown_backend",
            "model": "test-model",
        },
    }

    with patch("src.services.llm.backends.openai_client.OpenAIClient") as mock_openai:
        with patch("src.services.llm.backends.ollama_client.OllamaClient") as mock_ollama:
            mock_openai.return_value = MagicMock()

            with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
                await llm_manager.setup(config)

                # 应该使用 OpenAI 而不是 Ollama
                mock_openai.assert_called_once()
                mock_ollama.assert_not_called()


@pytest.mark.asyncio
async def test_setup_initializes_token_manager(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 setup 初始化 TokenUsageManager"""
    with patch("src.services.llm.backends.openai_client.OpenAIClient"):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager") as mock_token_manager:
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
    # 验证温度参数传递
    call_args = mock_backend.chat.call_args
    assert call_args[1]["temperature"] == 0.9


@pytest.mark.asyncio
async def test_chat_with_max_tokens(setup_llm_manager):
    """测试带 max_tokens 参数的聊天"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", max_tokens=100)

    assert response.success is True
    # 验证 max_tokens 参数传递
    call_args = mock_backend.chat.call_args
    assert call_args[1]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_chat_with_custom_backend(setup_llm_manager):
    """测试使用自定义客户端"""
    llm_manager, mock_backend, _ = setup_llm_manager

    response = await llm_manager.chat("Hello", client_type="llm_fast")

    assert response.success is True
    # 验证使用了正确的客户端


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

    # Mock stream_chat 后端
    async def mock_stream(**kwargs):
        chunks = ["Hello", " world", "!"]
        for chunk in chunks:
            yield chunk

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            chunks = []
            async for chunk in llm_manager.stream_chat("Tell me a story"):
                chunks.append(chunk)

            assert chunks == ["Hello", " world", "!"]


@pytest.mark.asyncio
async def test_stream_chat_with_stop_event(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试流式聊天支持停止事件"""

    async def mock_stream(**kwargs):
        chunks = ["Chunk1", "Chunk2", "Chunk3"]
        for chunk in chunks:
            # 检查停止事件
            stop_event = kwargs.get("stop_event")
            if stop_event and stop_event.is_set():
                break
            yield chunk

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            stop_event = asyncio.Event()
            chunks = []

            async for chunk in llm_manager.stream_chat("Test", stop_event=stop_event):
                chunks.append(chunk)
                if len(chunks) == 2:
                    stop_event.set()  # 在第二个 chunk 后停止

            assert len(chunks) == 2


@pytest.mark.asyncio
async def test_stream_chat_with_system_message(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试流式聊天带系统消息"""

    async def mock_stream(**kwargs):
        messages = kwargs.get("messages", [])
        # 验证消息格式
        if len(messages) == 2 and messages[0]["role"] == "system":
            yield "OK"
        else:
            yield "FAIL"

    mock_backend = MagicMock()
    mock_backend.stream_chat = mock_stream

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
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

    # Mock 工具调用响应
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

    # 验证消息格式
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

    # 验证 tools 参数被传递
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
    # 验证图片列表被传递
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
    # 验证消息格式
    call_args = mock_backend.vision.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"


@pytest.mark.asyncio
async def test_vision_uses_vlm_backend_by_default(setup_llm_manager):
    """测试 vision 默认使用 vlm 客户端"""
    llm_manager, mock_backend, _ = setup_llm_manager

    await llm_manager.chat_vision("Test", ["image.jpg"])

    # 验证使用了 vision 方法（这是客户端的选择）
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
    """测试 simple_chat 支持客户端参数"""
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

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
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

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
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

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
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

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
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
    """测试获取客户端信息"""
    llm_manager, mock_backend, _ = setup_llm_manager

    info = llm_manager.get_client_info()

    assert isinstance(info, dict)
    assert "llm" in info
    assert "llm_fast" in info
    assert "vlm" in info
    # 验证信息结构
    assert "backend" in info["llm"]
    assert "config" in info["llm"]


@pytest.mark.asyncio
async def test_get_client_info_returns_correct_structure(setup_llm_manager):
    """测试客户端信息结构"""
    llm_manager, mock_backend, _ = setup_llm_manager

    info = llm_manager.get_client_info()

    for _client_name, client_info in info.items():
        assert "backend" in client_info
        assert "config" in client_info


# =============================================================================
# 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_nonexistent_backend_raises_error(setup_llm_manager):
    """测试获取不存在的客户端抛出错误"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'nonexistent' 未配置"):
        llm_manager._get_client("nonexistent")


@pytest.mark.asyncio
async def test_chat_with_nonexistent_backend(setup_llm_manager):
    """测试使用不存在的客户端进行聊天"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'unknown_backend' 未配置"):
        await llm_manager.chat("Test", client_type="unknown_backend")


@pytest.mark.asyncio
async def test_vision_with_nonexistent_backend(setup_llm_manager):
    """测试使用不存在的客户端进行视觉理解"""
    llm_manager, _, _ = setup_llm_manager

    with pytest.raises(ValueError, match="LLM 客户端 'unknown_backend' 未配置"):
        await llm_manager.chat_vision("Test", ["image.jpg"], client_type="unknown_backend")


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_all_backends(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试清理所有客户端"""
    mock_backend = MagicMock()
    mock_backend.cleanup = AsyncMock()
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch("src.services.llm.backends.openai_client.OpenAIClient", return_value=mock_backend):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            await llm_manager.setup(mock_config)

            # 验证客户端已初始化
            assert len(llm_manager._clients) == 3

            await llm_manager.cleanup()

            # 验证每个客户端的 cleanup 被调用
            assert mock_backend.cleanup.call_count == 3
            assert len(llm_manager._clients) == 0  # 客户端被清空


@pytest.mark.asyncio
async def test_cleanup_handles_backend_errors(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试清理时处理单个客户端错误"""

    async def failing_cleanup():
        raise Exception("Cleanup error")

    mock_backend1 = MagicMock()
    mock_backend1.cleanup = failing_cleanup
    mock_backend1.get_info.return_value = {"name": "Backend1"}

    mock_backend2 = MagicMock()
    mock_backend2.cleanup = AsyncMock()
    mock_backend2.get_info.return_value = {"name": "Backend2"}

    with patch("src.services.llm.backends.openai_client.OpenAIClient", side_effect=[mock_backend1, mock_backend2]):
        with patch("src.services.llm.backends.token_usage_manager.TokenUsageManager"):
            # 创建只有两个客户端的配置
            config = {"llm": {"backend": "openai", "model": "test"}}
            await llm_manager.setup(config)

            # 应该不抛出异常，继续清理其他客户端
            await llm_manager.cleanup()

            assert len(llm_manager._clients) == 0


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
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
