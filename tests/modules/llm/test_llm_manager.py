"""LLMManager 单元测试（三层配置：providers / models / profiles）

测试 LLMManager 的核心功能：
- 三层结构初始化（llm_providers + llm_models + llm_profiles）
- 唯一入口 generate（中立 payload 契约）路径上的重试与故障切换
- 模型选择策略（sequential / balance / random）
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

client 实现在 src.modules.llm.clients._CLIENT_DISPATCH 显式调度表中登记，
调度表持有的是类对象引用，不是通过模块属性查找。

因此 `patch("src.modules.llm.clients.openai.client.OpenAIClient")` 不能拦截
manager 内部 `get_client_impl("openai")` 的查询——它会拿到未 patch 的原类。

正确的 mock 方式是 patch 调度表：
    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_class}):
        ...
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.bootstrap import KNOWN_PROFILE_NAMES, ProfileNames
from src.modules.llm.client import LLMResponse
from src.modules.llm.clients import _CLIENT_DISPATCH as _CLIENT_DISPATCH
from src.modules.llm.clients.openai.compat import normalize_tool_calls_for_protocol
from src.modules.llm.engine import LLMManager, RetryConfig
from src.modules.llm.payload import Response as PayloadResponse
from src.modules.llm.payload import Usage as PayloadUsage


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
# Helper: 标准 mock backend（中立 payload 契约的 generate 能力）
# =============================================================================


def _make_mock_backend() -> MagicMock:
    mock_backend = MagicMock()
    mock_backend.generate = AsyncMock(
        return_value=PayloadResponse(
            success=True,
            content="Test response",
            model="gpt-4o-mini",
            usage=PayloadUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
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
    """完整初始化 LLMManager（mock OpenAIClient）"""
    mock_backend = _make_mock_backend()
    mock_backend_class = MagicMock(return_value=mock_backend)

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
        await llm_manager.setup(mock_config)
        yield llm_manager, mock_backend


# =============================================================================
# 初始化和配置测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_initializes_providers_and_profiles(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试 setup 初始化 providers / models / profiles"""
    mock_backend_class = MagicMock(return_value=_make_mock_backend())

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
        await llm_manager.setup(mock_config)

        # 1 个 provider → 1 个客户端实例（profile 维度不再各自构造 client）
        assert mock_backend_class.call_count == 1
        assert list(llm_manager._providers) == ["test"]
        assert len(llm_manager._models) == 3
        assert set(llm_manager._profiles) == {"planner", "replyer", "vision"}


@pytest.mark.asyncio
async def test_setup_builds_model_price_table(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """setup 构建按 model_identifier 键入的价格表（费用计算唯一口径的输入）"""
    mock_backend_class = MagicMock(return_value=_make_mock_backend())
    config = {
        **mock_config,
        "llm_models": [
            {"name": "free", "model_identifier": "free-model-id", "api_provider": "test"},
            {
                "name": "paid",
                "model_identifier": "paid-model-id",
                "api_provider": "test",
                "price_in": 1.0,
                "price_out": 2.0,
            },
        ],
        "llm_profiles": {
            "planner": {"model_list": ["paid"]},
            "replyer": {"model_list": ["free"]},
            "vision": {"model_list": ["free"]},
        },
    }

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
        await llm_manager.setup(config)

        assert set(llm_manager._model_prices) == {"paid-model-id"}
        assert llm_manager._model_prices["paid-model-id"]["price_in"] == 1.0


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
    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
        with pytest.raises(ValueError, match="model_list 为空"):
            await llm_manager.setup(config)


# =============================================================================
# generate 入口测试（唯一对外入口；契约细节见 test_generate_contract.py）
# =============================================================================


@pytest.mark.asyncio
async def test_generate_with_unknown_profile_raises(setup_llm_manager):
    """测试调用未注册的 profile 立即 fail-fast"""
    llm_manager, _ = setup_llm_manager

    with pytest.raises(ValueError, match="未配置"):
        await llm_manager.generate("Test", profile="unknown_profile")


# =============================================================================
# 重试机制测试（单模型内的重试，不跨模型；分类异常口径见 test_retry_policy.py）
# =============================================================================


@pytest.mark.asyncio
async def test_retry_on_failure(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试失败时在单模型上自动重试"""
    call_count = 0

    async def failing_generate(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("API Error")
        return PayloadResponse(
            success=True,
            content="Success after retries",
            model="gpt-4o-mini",
            usage=PayloadUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    mock_backend = MagicMock()
    mock_backend.generate = failing_generate
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(mock_config)

        response = await llm_manager.generate("Test", profile="planner")

        assert response.success is True
        assert call_count == 3  # 失败 2 次，第 3 次成功


@pytest.mark.asyncio
async def test_retry_exhaustion(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试单模型重试耗尽（max_retries 默认 3）"""

    async def always_failing_generate(**kwargs):
        raise Exception("Persistent API Error")

    mock_backend = MagicMock()
    mock_backend.generate = always_failing_generate
    mock_backend.get_info.return_value = {"name": "OpenAIBackend"}

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(mock_config)

        response = await llm_manager.generate("Test", profile="planner")

        assert response.success is False
        assert "Persistent API Error" in response.error


@pytest.mark.asyncio
async def test_retry_with_custom_config(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """测试自定义重试配置"""
    llm_manager._retry_config = RetryConfig(max_retries=2, base_delay=0.1)

    call_count = 0

    async def failing_generate(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception("Error")

    mock_backend = MagicMock()
    mock_backend.generate = failing_generate
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        import time

        await llm_manager.setup(mock_config)

        start = time.time()
        response = await llm_manager.generate("Test", profile="planner")
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
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(config)

        # 连续调用 3 次：每次都应该用 m1
        for _ in range(3):
            await llm_manager.generate("Test", profile="planner")

        used_models = [c[1]["model"] for c in mock_backend.generate.call_args_list]
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
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(config)

        await llm_manager.generate("Test", profile="planner")
        first = mock_backend.generate.call_args_list[0][1]["model"]

        await llm_manager.generate("Test", profile="planner")
        second = mock_backend.generate.call_args_list[1][1]["model"]

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
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend1)}):
        await llm_manager.setup(config)
        await llm_manager.generate("Test", profile="planner")
        chosen_1 = mock_backend1.generate.call_args[1]["model"]

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend2)}):
        await llm_manager.setup(config)
        await llm_manager.generate("Test", profile="planner")
        chosen_2 = mock_backend2.generate.call_args[1]["model"]

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
# 故障切换测试（跨模型；超时/中断口径见 test_hard_timeout.py / test_retry_policy.py）
# =============================================================================


@pytest.mark.asyncio
async def test_failover_advances_to_second_model(llm_manager: LLMManager):
    """当前模型失败切到下一个"""
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
    call_count = {"m1": 0, "m2": 0}

    async def flaky_generate(**kwargs):
        model = kwargs.get("model")
        call_count[model] = call_count.get(model, 0) + 1
        if model == "id-m1":
            raise Exception("upstream error")
        return PayloadResponse(
            success=True,
            content="success from m2",
            model="id-m2",
            usage=PayloadUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        )

    mock_backend = MagicMock()
    mock_backend.generate = flaky_generate
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(config)

        response = await llm_manager.generate("Test", profile="planner")

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
    mock_backend.generate = always_fail
    mock_backend.get_info.return_value = {"name": "OpenAIClient"}

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(config)

        response = await llm_manager.generate("Test", profile="planner")

        assert response.success is False
        assert "全部模型失败" in response.error
        assert "m1" in response.error
        assert "m2" in response.error


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

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(side_effect=mock_backends)}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])}):
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

    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(mock_config)
        # 3 个 profile 共享同一 provider 客户端
        assert llm_manager._provider_clients["test"] is mock_backend

        await llm_manager.cleanup()

        assert mock_backend.cleanup.call_count == 1


# =============================================================================
# Provider 共享 / 引用测试
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_profiles_share_same_provider_client(llm_manager: LLMManager, mock_config: Dict[str, Any]):
    """3 个 profile 共享同一 provider 客户端实例"""
    mock_backend = _make_mock_backend()
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=mock_backend)}):
        await llm_manager.setup(mock_config)

        # 3 个 profile 都指向 provider "test"
        assert llm_manager._provider_clients["test"] is mock_backend
        assert list(llm_manager._providers) == ["test"]


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
        _CLIENT_DISPATCH,
        {"openai": MagicMock(side_effect=[mock_backend1, mock_backend2])},
    ):
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
# LLMResponse 测试（遗留形状：请求历史/记账链路仍消费）
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


def test_profile_names_includes_all_purposes() -> None:
    """用途命名常量包含独立建筑设计，且与配置 Schema 的封闭集合一致。"""
    assert KNOWN_PROFILE_NAMES == {
        ProfileNames.PLANNER,
        ProfileNames.REPLYER,
        ProfileNames.SUMMARY,
        ProfileNames.MINECRAFT,
        ProfileNames.MINECRAFT_BUILDER,
        ProfileNames.VISION,
        ProfileNames.SIMULATOR,
    }


def test_validate_profile_binding_rejects_unknown():
    """封闭集合校验：未知 profile 声明在装配期硬错"""
    from src.modules.llm.bootstrap import validate_profile_binding

    assert validate_profile_binding("planner") is None
    assert validate_profile_binding("vision") is None
    with pytest.raises(ValueError, match="unknown"):
        validate_profile_binding("unknown")


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
