# E2E Tests

端到端测试，验证完整的 5 层数据流。

## 测试覆盖

### 测试文件

- **test_smoke.py** - 基础冒烟测试
  - 验证 Provider 注册
  - 验证 EventBus 功能
  - 验证 Mock Provider 基本功能

- **test_basic_data_flow.py** - 基本数据流测试
  - 完整数据流测试（RawData → Intent）
  - InputLayer 标准化测试
  - DecisionProvider 决策测试
  - 多条连续消息处理测试

- **test_provider_switching.py** - Provider 切换测试
  - 运行时切换 DecisionProvider
  - Provider 状态管理测试
  - 切换失败回滚测试
  - 并发访问测试

- **test_error_recovery.py** - 错误恢复测试
  - Provider 失败隔离测试
  - 无效数据处理测试
  - EventBus 错误隔离测试
  - Provider 初始化失败处理测试
  - 资源清理测试

### 辅助工具

- **conftest.py** - pytest fixtures
  - `event_bus` - EventBus 实例
  - `input_layer` - InputDomain 实例
  - `decision_manager` - DecisionManager 实例
  - `sample_raw_data` - 示例 RawData
  - `sample_normalized_message` - 示例 NormalizedMessage
  - `wait_for_event` - 等待事件的辅助函数

- **test_helpers.py** - 测试辅助函数
  - `create_normalized_message()` - 创建 NormalizedMessage
  - `create_raw_data()` - 创建 RawData

## 运行测试

### 运行所有 E2E 测试
```bash
uv run pytest tests/e2e/ -v
```

### 运行特定测试文件
```bash
uv run pytest tests/e2e/test_smoke.py -v
```

### 运行特定测试用例
```bash
uv run pytest tests/e2e/test_smoke.py::test_provider_registry_has_providers -v
```

### 显示详细输出
```bash
uv run pytest tests/e2e/ -vv -s
```

## 测试原则

1. **独立性** - 每个测试独立运行，不依赖其他测试
2. **可重复性** - 测试结果可重复，不受环境影响
3. **快速** - 使用 Mock Provider，避免外部依赖
4. **清晰** - 测试名称和断言清晰表达意图

## Mock Provider

测试使用以下 Mock Provider：

- **MockDecisionProvider** - 模拟决策，返回预设响应
- **MockOutputProvider** - 记录收到的参数，不实际渲染
- **MockDanmakuInputProvider** - 模拟弹幕输入

## 注意事项

- 测试前确保已导入 Provider 模块（触发注册）
- 异步测试使用 `@pytest.mark.asyncio` 装饰器
- 使用 `await` 调用异步方法
- 测试完成后清理资源（cleanup）
