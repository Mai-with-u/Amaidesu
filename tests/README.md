# 测试目录结构

本目录包含按照 3 阶段架构组织的测试，目录结构与 `src/` 目录对应。

## 目录结构

```
tests/
├── architecture/                    # 架构约束测试
│   ├── test_dependency_direction.py
│   └── test_event_flow_constraints.py
├── characterization/                 # 表征测试
├── modules/                         # 模块层测试（对应 src/modules/）
│   ├── base/                        # 基类测试
│   │   ├── test_normalized_message.py
│   │   └── test_pipeline_stats.py
│   ├── config/                      # 配置系统测试
│   │   ├── test_config_loading.py
│   │   ├── test_regression_fixes.py
│   │   └── schemas/
│   ├── context/                     # 上下文服务测试
│   │   └── test_context_service.py
│   ├── dashboard/                   # Dashboard 测试
│   │   ├── api/
│   │   └── overlay/
│   ├── di/                          # 依赖注入测试
│   ├── events/                      # 事件系统测试
│   │   ├── test_event_bus.py
│   │   ├── test_event_debug.py
│   │   ├── test_event_registry.py
│   │   └── test_event_typed_handler.py
│   ├── llm/                         # LLM 服务测试
│   │   ├── test_llm_manager.py
│   │   └── backends/
│   │       └── test_token_usage_manager.py
│   ├── logging/                     # 日志测试
│   │   └── test_logger.py
│   ├── mcp/                         # MCP 服务测试
│   │   └── test_mcp_server.py
│   ├── prompts/                     # 提示词测试
│   │   ├── test_prompt_manager.py
│   │   └── test_prompt_quality.py
│   ├── services/                    # 共享服务测试
│   ├── streaming/                   # 流媒体测试
│   ├── tts/                         # TTS 服务测试
│   │   ├── test_audio_device_manager.py
│   │   └── test_gptsovits_client.py
│   └── types/                       # 类型测试
│       └── test_intent.py
├── stages/                          # 阶段测试（对应 src/stages/）
│   ├── input/                       # 输入阶段测试
│   │   ├── collectors/              # InputCollector 测试
│   │   │   └── test_stt_input_collector.py
│   │   ├── normalization/           # 标准化测试
│   │   │   └── normalizers/
│   │   ├── pipelines/               # Pipeline 测试
│   │   │   ├── test_message_pipeline.py
│   │   │   ├── test_rate_limit_pipeline.py
│   │   │   └── test_similar_filter_pipeline.py
│   │   ├── shared/                  # 共享组件测试
│   │   │   └── bili_messages/
│   │   └── test_input_pipeline_manager.py
│   ├── decision/                    # 决策阶段测试
│   │   └── deciders/                # Decider 测试
│   └── output/                      # 输出阶段测试
│       ├── handlers/                # OutputHandler 测试
│       │   ├── avatar/
│       │   │   ├── test_base.py
│       │   │   ├── vrchat/
│       │   │   ├── vts/
│       │   │   └── warudo/
│       │   │       ├── test_mood_manager.py
│       │   │       ├── test_state_manager.py
│       │   │       └── test_warudo_handler.py
│       │   └── gptsovits/
│       ├── pipelines/               # Output Pipeline 测试
│       │   ├── test_base_pipeline.py
│       │   ├── test_manager.py
│       │   ├── test_output_pipeline.py
│       │   └── test_profanity_filter.py
│       └── parameters/              # 渲染参数测试
├── integration/                     # 集成测试
├── mocks/                           # Mock 对象
│   ├── mock_decision_decider.py
│   ├── mock_input_provider.py
│   └── mock_output_handler.py
└── conftest.py                      # pytest 配置和共享 fixtures
```

## 运行测试

### 运行所有测试
```bash
uv run pytest tests/ -v
```

### 运行特定模块的测试
```bash
# 模块层测试（全部）
uv run pytest tests/modules/ -v

# 事件系统测试
uv run pytest tests/modules/events/ -v

# 配置系统测试
uv run pytest tests/modules/config/ -v

# LLM 服务测试
uv run pytest tests/modules/llm/ -v
```

### 运行特定阶段的测试
```bash
# 输入阶段测试
uv run pytest tests/stages/input/ -v

# 决策阶段测试
uv run pytest tests/stages/decision/ -v

# 输出阶段测试
uv run pytest tests/stages/output/ -v
```

### 运行特定 Handler 测试
```bash
# VTS Handler
uv run pytest tests/stages/output/handlers/avatar/vts/ -v

# Warudo Handler
uv run pytest tests/stages/output/handlers/avatar/warudo/ -v

# VRChat Handler
uv run pytest tests/stages/output/handlers/avatar/vrchat/ -v
```

### 运行架构约束测试
```bash
uv run pytest tests/architecture/ -v
```

### 排除慢速测试
```bash
uv run pytest tests/ -v -m "not slow"
```

## 测试命名规范

- `test_<name>_collector.py` - InputCollector 测试（如 `test_stt_input_collector.py`）
- `test_<name>_decider.py` - Decider 测试（如 `test_maibot_decider.py`）
- `test_<name>_handler.py` - OutputHandler 测试（如 `test_vts_handler.py`）
- `test_<name>_pipeline.py` - Pipeline 测试（如 `test_rate_limit_pipeline.py`）
- `test_<component>.py` - 核心组件测试（如 `test_event_bus.py`）
- `test_<manager_name>_manager.py` - Manager 测试

## 目录结构与源码对应关系

| 源码位置 | 测试位置 | 说明 |
|---------|---------|------|
| `src/modules/` | `tests/modules/` | 通用模块（配置、事件、LLM、日志等） |
| `src/stages/input/` | `tests/stages/input/` | 输入阶段 |
| `src/stages/decision/` | `tests/stages/decision/` | 决策阶段 |
| `src/stages/output/` | `tests/stages/output/` | 输出阶段 |
| - | `tests/architecture/` | 架构约束测试 |
| - | `tests/integration/` | 集成测试 |
| - | `tests/mocks/` | Mock 对象 |

## 测试覆盖率

```bash
# 安装 coverage 工具（如果未安装）
uv add --dev pytest-cov

# 运行测试并生成覆盖率报告
uv run pytest tests/ --cov=src --cov-report=html

# 查看报告
# Windows
start htmlcov/index.html
# macOS
open htmlcov/index.html
# Linux
xdg-open htmlcov/index.html
```

## 相关文档

- [测试指南](../docs/development/testing-guide.md) - 测试规范和最佳实践
- [3阶段架构](../docs/architecture/overview.md) - 架构设计详解
- [阶段参与者开发](../docs/development/component-guide.md) - Collector/Decider/Handler 开发指南
