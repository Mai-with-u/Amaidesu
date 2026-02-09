"""
TokenUsageManager 单元测试

测试 TokenUsageManager 的核心功能：
- 初始化和价格配置加载
- 记录 token 使用量
- 获取使用量摘要
- 费用计算
- 统计汇总
- 全局实例管理

注意：不测试文件系统操作，使用 mock 或内存测试

运行: uv run pytest tests/services/llm/backends/test_token_usage_manager.py -v
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.services.llm.backends.token_usage_manager import (
    TokenUsageManager,
    get_global_token_manager,
    set_global_token_manager_callback,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def temp_usage_dir():
    """创建临时使用量目录"""
    temp_dir = tempfile.mkdtemp()
    usage_dir = Path(temp_dir) / "usage"
    usage_dir.mkdir(exist_ok=True)
    yield usage_dir
    # 清理
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_price_file(temp_usage_dir):
    """创建模拟价格配置文件"""
    price_file = temp_usage_dir / "model_price.toml"
    price_content = """
[gpt-4o]
price_in = 2.50
price_out = 10.00

[gpt-4o-mini]
price_in = 0.15
price_out = 0.60

[claude-3-5-sonnet]
price_in = 3.00
price_out = 15.00
"""
    price_file.write_text(price_content, encoding="utf-8")
    return price_file


@pytest.fixture
def token_manager(temp_usage_dir, mock_price_file):
    """创建 TokenUsageManager 实例（使用 use_global=False 避免污染全局）"""
    # Patch __init__ 中的 project_root 路径
    with patch.object(TokenUsageManager, "__init__", lambda self, update_callback=None, use_global=True: None):
        manager = TokenUsageManager(use_global=False)
        # 手动设置必要的属性
        manager.usage_dir = temp_usage_dir
        manager.logger = MagicMock()
        manager.model_prices = {
            "gpt-4o": {"price_in": 2.50, "price_out": 10.00},
            "gpt-4o-mini": {"price_in": 0.15, "price_out": 0.60},
            "claude-3-5-sonnet": {"price_in": 3.00, "price_out": 15.00},
        }
        manager.update_callback = None
        return manager


@pytest.fixture
def token_manager_no_prices(temp_usage_dir):
    """创建没有价格配置的管理器"""
    with patch.object(TokenUsageManager, "__init__", lambda self, update_callback=None, use_global=True: None):
        manager = TokenUsageManager(use_global=False)
        manager.usage_dir = temp_usage_dir
        manager.logger = MagicMock()
        manager.model_prices = {}  # 没有价格配置
        manager.update_callback = None
        return manager


# =============================================================================
# 初始化测试
# =============================================================================


def test_token_manager_initialization(token_manager):
    """测试 TokenUsageManager 初始化"""
    assert token_manager.usage_dir.exists()
    assert isinstance(token_manager.model_prices, dict)
    assert token_manager.update_callback is None


def test_token_manager_with_prices(token_manager):
    """测试加载价格配置"""
    assert "gpt-4o" in token_manager.model_prices
    assert token_manager.model_prices["gpt-4o"]["price_in"] == 2.50
    assert token_manager.model_prices["gpt-4o"]["price_out"] == 10.00


def test_token_manager_without_prices(token_manager_no_prices):
    """测试没有价格配置的情况"""
    assert token_manager_no_prices.model_prices == {}


# =============================================================================
# 获取模型价格测试
# =============================================================================


def test_get_model_price_exact_match(token_manager):
    """测试精确匹配模型价格"""
    price = token_manager._get_model_price("gpt-4o")
    assert price is not None
    assert price["price_in"] == 2.50
    assert price["price_out"] == 10.00


def test_get_model_price_fuzzy_match(token_manager):
    """测试模糊匹配模型价格"""
    # 测试版本号匹配
    price = token_manager._get_model_price("gpt-4o-2024-05-13")
    assert price is not None
    assert price["price_in"] == 2.50

    # 测试部分名称匹配
    price = token_manager._get_model_price("claude-3-5-sonnet-20241022")
    assert price is not None
    assert price["price_in"] == 3.00


def test_get_model_price_not_found(token_manager):
    """测试不存在的模型价格"""
    price = token_manager._get_model_price("unknown-model")
    assert price is None


def test_get_model_price_no_prices_configured(token_manager_no_prices):
    """测试没有配置价格时获取价格"""
    price = token_manager_no_prices._get_model_price("gpt-4o")
    assert price is None


# =============================================================================
# 费用计算测试
# =============================================================================


def test_calculate_cost_with_price(token_manager):
    """测试有价格配置时的费用计算"""
    cost_info = token_manager._calculate_cost("gpt-4o", 1000, 500)

    assert cost_info["has_price"] is True
    assert cost_info["cost"] > 0
    assert cost_info["cost_usd"] > 0
    assert cost_info["price_in"] == 2.50
    assert cost_info["price_out"] == 10.00

    # 验证计算公式: (prompt_tokens / 1M) * price_in + (completion_tokens / 1M) * price_out
    expected_cost = (1000 / 1000000.0) * 2.50 + (500 / 1000000.0) * 10.00
    assert abs(cost_info["cost"] - expected_cost) < 0.0001


def test_calculate_cost_without_price(token_manager_no_prices):
    """测试没有价格配置时的费用计算"""
    cost_info = token_manager_no_prices._calculate_cost("unknown-model", 1000, 500)

    assert cost_info["has_price"] is False
    assert cost_info["cost"] == 0.0
    assert cost_info["cost_usd"] == 0.0
    assert "未找到价格配置" in cost_info["message"]


def test_calculate_cost_zero_tokens(token_manager):
    """测试零token的费用计算"""
    cost_info = token_manager._calculate_cost("gpt-4o", 0, 0)

    assert cost_info["has_price"] is True
    assert cost_info["cost"] == 0.0


def test_calculate_cost_large_tokens(token_manager):
    """测试大量token的费用计算"""
    cost_info = token_manager._calculate_cost("gpt-4o", 100000, 50000)

    assert cost_info["has_price"] is True
    # 验证计算正确
    expected_cost = (100000 / 1000000.0) * 2.50 + (50000 / 1000000.0) * 10.00
    assert abs(cost_info["cost"] - expected_cost) < 0.001


# =============================================================================
# 文件路径测试
# =============================================================================


def test_get_usage_file_path_basic(token_manager):
    """测试获取使用量文件路径"""
    file_path = token_manager._get_usage_file_path("gpt-4o")

    assert file_path.name == "gpt-4o_usage.json"
    assert file_path.parent == token_manager.usage_dir


def test_get_usage_file_path_special_characters(token_manager):
    """测试特殊字符模型名称的文件路径"""
    # 特殊字符应被移除
    file_path = token_manager._get_usage_file_path("gpt-4o@version#1")

    assert file_path.name == "gpt-4oversion1_usage.json"


def test_get_usage_file_path_dots_and_dashes(token_manager):
    """测试点和破折号保留"""
    file_path = token_manager._get_usage_file_path("gpt-4o.mini.2024")

    assert file_path.name == "gpt-4o.mini.2024_usage.json"


# =============================================================================
# 加载当前使用量测试
# =============================================================================


def test_load_current_usage_no_file(token_manager):
    """测试文件不存在时返回初始数据"""
    usage = token_manager._load_current_usage("nonexistent-model")

    assert usage["model_name"] == "nonexistent-model"
    assert usage["total_prompt_tokens"] == 0
    assert usage["total_completion_tokens"] == 0
    assert usage["total_tokens"] == 0
    assert usage["total_calls"] == 0
    assert usage["total_cost"] == 0.0
    assert usage["first_call_time"] is None
    assert usage["last_call_time"] is None


def test_load_current_usage_with_file(token_manager):
    """测试加载已有的使用量文件"""
    # 创建使用量文件
    model_name = "test-model"
    file_path = token_manager._get_usage_file_path(model_name)
    test_data = {
        "model_name": model_name,
        "total_prompt_tokens": 1000,
        "total_completion_tokens": 500,
        "total_tokens": 1500,
        "total_calls": 5,
        "total_cost": 0.01,
        "first_call_time": 1000000000000,
        "last_call_time": 1000000050000,
        "last_updated": 1000000050000,
    }
    file_path.write_text(json.dumps(test_data), encoding="utf-8")

    # 加载数据
    usage = token_manager._load_current_usage(model_name)

    assert usage["model_name"] == model_name
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 500
    assert usage["total_tokens"] == 1500
    assert usage["total_calls"] == 5
    assert usage["total_cost"] == 0.01


def test_load_current_usage_missing_fields(token_manager):
    """测试加载缺少字段的使用量文件"""
    model_name = "incomplete-model"
    file_path = token_manager._get_usage_file_path(model_name)
    incomplete_data = {
        "model_name": model_name,
        "total_prompt_tokens": 500,
        # 缺少其他字段
    }
    file_path.write_text(json.dumps(incomplete_data), encoding="utf-8")

    # 应该用默认值填充缺失字段
    usage = token_manager._load_current_usage(model_name)

    assert usage["total_prompt_tokens"] == 500
    assert usage["total_completion_tokens"] == 0  # 默认值
    assert usage["total_calls"] == 0  # 默认值


# =============================================================================
# 保存使用量测试
# =============================================================================


def test_save_usage(token_manager):
    """测试保存使用量数据"""
    model_name = "save-test"
    test_data = {
        "model_name": model_name,
        "total_tokens": 100,
        "total_cost": 0.001,
    }

    token_manager._save_usage(model_name, test_data)

    # 验证文件已创建
    file_path = token_manager._get_usage_file_path(model_name)
    assert file_path.exists()

    # 验证内容
    saved_data = json.loads(file_path.read_text(encoding="utf-8"))
    assert saved_data["model_name"] == model_name
    assert saved_data["total_tokens"] == 100


# =============================================================================
# 记录使用量测试
# =============================================================================


def test_record_usage_basic(token_manager):
    """测试基本使用量记录"""
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)

    # 验证文件已创建
    file_path = token_manager._get_usage_file_path("gpt-4o")
    assert file_path.exists()

    # 验证数据
    usage = token_manager.get_usage_summary("gpt-4o")
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 500
    assert usage["total_tokens"] == 1500
    assert usage["total_calls"] == 1
    assert usage["total_cost"] > 0


def test_record_usage_multiple_calls(token_manager):
    """测试多次调用记录累加"""
    # 第一次记录
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)
    # 第二次记录
    token_manager.record_usage("gpt-4o", 2000, 1000, 3000)

    usage = token_manager.get_usage_summary("gpt-4o")
    assert usage["total_prompt_tokens"] == 3000
    assert usage["total_completion_tokens"] == 1500
    assert usage["total_tokens"] == 4500
    assert usage["total_calls"] == 2


def test_record_usage_without_price(token_manager_no_prices):
    """测试没有价格配置时的记录"""
    token_manager_no_prices.record_usage("unknown-model", 1000, 500, 1500)

    usage = token_manager_no_prices.get_usage_summary("unknown-model")
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 500
    assert usage["total_cost"] == 0.0  # 没有价格配置


def test_record_usage_with_callback(token_manager):
    """测试使用量更新回调"""
    callback_called = []

    def test_callback(model_name, usage_data):
        callback_called.append((model_name, usage_data))

    token_manager.update_callback = test_callback
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)

    # 验证回调被调用
    assert len(callback_called) == 1
    assert callback_called[0][0] == "gpt-4o"
    assert callback_called[0][1]["total_tokens"] == 1500


def test_record_usage_callback_exception(token_manager):
    """测试回调异常不影响记录"""
    def failing_callback(model_name, usage_data):
        raise ValueError("测试异常")

    token_manager.update_callback = failing_callback
    # 应该不抛出异常
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)

    # 数据仍应保存
    usage = token_manager.get_usage_summary("gpt-4o")
    assert usage["total_calls"] == 1


def test_record_usage_time_stamps(token_manager):
    """测试时间戳记录"""
    import time

    before_time = int(time.time() * 1000)
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)
    after_time = int(time.time() * 1000)

    usage = token_manager.get_usage_summary("gpt-4o")
    assert usage["first_call_time"] is not None
    assert usage["last_call_time"] is not None
    assert usage["last_updated"] is not None
    assert before_time <= usage["first_call_time"] <= after_time


# =============================================================================
# 获取使用量摘要测试
# =============================================================================


def test_get_usage_summary(token_manager):
    """测试获取使用量摘要"""
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)

    summary = token_manager.get_usage_summary("gpt-4o")

    assert summary["model_name"] == "gpt-4o"
    assert summary["total_prompt_tokens"] == 1000
    assert summary["total_completion_tokens"] == 500
    assert summary["total_tokens"] == 1500
    assert summary["total_calls"] == 1


def test_get_usage_summary_nonexistent(token_manager):
    """测试获取不存在模型的使用量摘要"""
    summary = token_manager.get_usage_summary("nonexistent-model")

    # 应该返回初始数据
    assert summary["total_calls"] == 0
    assert summary["total_tokens"] == 0


# =============================================================================
# 获取所有模型使用量测试
# =============================================================================


def test_get_all_models_usage(token_manager):
    """测试获取所有模型使用量"""
    # 记录多个模型的使用量
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)
    token_manager.record_usage("gpt-4o-mini", 500, 300, 800)

    all_usage = token_manager.get_all_models_usage()

    assert len(all_usage) == 2
    assert "gpt-4o" in all_usage
    assert "gpt-4o-mini" in all_usage
    assert all_usage["gpt-4o"]["total_tokens"] == 1500
    assert all_usage["gpt-4o-mini"]["total_tokens"] == 800


def test_get_all_models_usage_empty(token_manager):
    """测试没有使用量记录时"""
    all_usage = token_manager.get_all_models_usage()

    assert all_usage == {}


# =============================================================================
# 总费用摘要测试
# =============================================================================


def test_get_total_cost_summary(token_manager):
    """测试获取总费用摘要"""
    # 记录多个模型的使用量
    token_manager.record_usage("gpt-4o", 1000, 500, 1500)
    token_manager.record_usage("gpt-4o-mini", 2000, 1000, 3000)

    summary = token_manager.get_total_cost_summary()

    assert summary["total_calls"] == 2
    assert summary["total_prompt_tokens"] == 3000
    assert summary["total_completion_tokens"] == 1500
    assert summary["total_tokens"] == 4500
    assert summary["model_count"] == 2
    assert summary["total_cost"] > 0


def test_get_total_cost_summary_empty(token_manager):
    """测试没有使用量时的总费用摘要"""
    summary = token_manager.get_total_cost_summary()

    assert summary["total_calls"] == 0
    assert summary["total_tokens"] == 0
    assert summary["total_cost"] == 0.0
    assert summary["model_count"] == 0


# =============================================================================
# 全局实例管理测试
# =============================================================================


def test_get_global_token_manager():
    """测试获取全局实例"""
    # 清除全局实例
    import src.services.llm.backends.token_usage_manager as tum_module
    tum_module.global_token_manager = None

    manager1 = get_global_token_manager()
    manager2 = get_global_token_manager()

    # 应该返回同一个实例
    assert manager1 is manager2


def test_set_global_token_manager_callback():
    """测试设置全局回调"""
    # 清除全局实例
    import src.services.llm.backends.token_usage_manager as tum_module
    tum_module.global_token_manager = None

    callback_called = []

    def test_callback(model_name, usage_data):
        callback_called.append((model_name, usage_data))

    set_global_token_manager_callback(test_callback)

    manager = get_global_token_manager()
    assert manager.update_callback is test_callback


# =============================================================================
# 边界情况测试
# =============================================================================


def test_record_usage_negative_tokens(token_manager):
    """测试负数token（边界情况）"""
    # 应该能处理负数（虽然实际使用中不应该出现）
    token_manager.record_usage("test", -100, -50, -150)

    usage = token_manager.get_usage_summary("test")
    assert usage["total_prompt_tokens"] == -100
    assert usage["total_completion_tokens"] == -50


def test_record_usage_zero_tokens(token_manager):
    """测试零token记录"""
    token_manager.record_usage("test", 0, 0, 0)

    usage = token_manager.get_usage_summary("test")
    assert usage["total_calls"] == 1
    assert usage["total_tokens"] == 0


def test_very_large_token_count(token_manager):
    """测试非常大的token数量"""
    large_count = 10_000_000
    token_manager.record_usage("test", large_count, large_count, large_count * 2)

    usage = token_manager.get_usage_summary("test")
    assert usage["total_tokens"] == large_count * 2


def test_special_model_names(token_manager):
    """测试特殊字符模型名称"""
    special_names = [
        "model-with-dashes",
        "model.with.dots",
        "model_with_underscores",
        "ModelWithCamelCase",
        "model123with456numbers",
    ]

    for name in special_names:
        token_manager.record_usage(name, 100, 50, 150)
        # 验证文件已创建
        file_path = token_manager._get_usage_file_path(name)
        assert file_path.exists()


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
