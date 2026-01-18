# 测试目录结构

本目录包含按照 7 层架构组织的测试。

## 目录结构

```
tests/
├── layers/                          # 分层测试
│   ├── input/                       # 输入层测试（Layer 1-2）
│   │   ├── test_input_layer.py      # InputLayer 核心功能
│   │   ├── test_console_provider.py # ConsoleInputProvider
│   │   ├── test_mock_danmaku_provider.py  # MockDanmakuProvider
│   │   └── test_multi_provider_integration.py  # 多Provider集成
│   ├── decision/                    # 决策层测试（Layer 3）
│   │   └── test_decision_manager.py
│   └── rendering/                   # 渲染层测试（Layer 5-7）
│       └── test_vts_provider.py
├── core/                            # 核心模块测试
│   ├── test_event_system.py         # EventBus 测试
│   └── test_event_data_contract.py  # 事件数据契约测试
├── integration/                     # 集成测试
│   └── (待添加)
└── (过时的测试文件，待清理)
    ├── test_*.py                    # 旧插件测试
    └── test_phase*.py               # 旧架构阶段测试
```

## 运行测试

### 运行所有测试
```bash
uv run pytest tests/ -v
```

### 运行特定层的测试
```bash
# 输入层测试
uv run pytest tests/layers/input/ -v

# 决策层测试
uv run pytest tests/layers/decision/ -v

# 渲染层测试
uv run pytest tests/layers/rendering/ -v

# 核心模块测试
uv run pytest tests/core/ -v
```

### 运行特定 Provider 测试
```bash
# ConsoleInputProvider
uv run pytest tests/layers/input/test_console_provider.py -v

# MockDanmakuProvider
uv run pytest tests/layers/input/test_mock_danmaku_provider.py -v

# VTSProvider
uv run pytest tests/layers/rendering/test_vts_provider.py -v
```

## 测试命名规范

- `test_<layer>_<provider>.py` - Provider 测试（如 `test_input_console_provider.py`）
- `test_<layer>_layer.py` - 层管理器测试（如 `test_input_layer.py`）
- `test_<component>.py` - 核心组件测试（如 `test_event_bus.py`）

## 待清理的测试

✅ **已完成清理** - 所有过时的测试文件已被删除（2025-02-01）

已删除的测试：
- 21个旧插件和旧架构测试文件
- 依赖 BasePlugin 的测试
- 旧架构阶段测试（test_phase*.py）
- 已废弃的数据类型测试（test_canonical_message.py）
- 一次性迁移脚本测试（test_config_migration.py）
