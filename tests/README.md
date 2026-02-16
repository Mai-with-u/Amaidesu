# 测试目录结构

本目录包含按照 3 域架构组织的测试，目录结构与 `src/` 目录对应。

## 目录结构

```
tests/
├── architecture/                    # 架构约束测试
│   ├── test_dependency_direction.py # 依赖方向验证
│   └── test_event_flow_constraints.py # 事件流约束验证
├── modules/                         # 模块层测试（对应 src/modules/）
│   ├── base/                        # 基类测试
│   │   ├── test_input_provider.py
│   │   ├── test_decision_provider.py
│   │   ├── test_output_provider.py
│   │   └── test_normalized_message.py
│   ├── config/                      # 配置系统测试
│   │   ├── test_config_service.py
│   │   ├── test_config_loading.py
│   │   ├── test_schema_completeness.py
│   │   └── schemas/
│   ├── context/                     # 上下文服务测试
│   ├── di/                          # 依赖注入测试
│   ├── events/                      # 事件系统测试
│   │   ├── test_event_bus.py
│   │   ├── test_event_registry.py
│   │   ├── test_event_typed_handler.py
│   │   └── test_payloads.py
│   ├── llm/                         # LLM 服务测试
│   │   ├── test_llm_manager.py
│   │   └── backends/
│   ├── logging/                     # 日志测试
│   │   └── test_logger.py
│   ├── prompts/                     # 提示词测试
│   │   ├── test_prompt_manager.py
│   │   └── test_prompt_quality.py
│   ├── streaming/                   # 流媒体测试
│   ├── tts/                         # TTS 服务测试
│   │   ├── test_audio_device_manager.py
│   │   └── test_gptsovits_client.py
│   ├── types/                       # 类型测试
│   │   └── test_intent.py
│   ├── test_provider_registry.py    # Provider 注册表测试
│   └── test_provider_registry_schema.py
├── domains/                         # 3域测试（对应 src/domains/）
│   ├── input/                       # 输入域测试
│   │   ├── providers/               # InputProvider 测试
│   │   ├── pipelines/               # Pipeline 测试
│   │   ├── normalization/           # 标准化测试
│   │   └── shared/                  # 共享组件测试
│   ├── decision/                    # 决策域测试
│   │   ├── providers/               # DecisionProvider 测试
│   │   └── test_decision_provider_manager.py
│   └── output/                      # 输出域测试
│       ├── providers/               # OutputProvider 测试
│       │   ├── avatar/              # Avatar 相关 Provider
│       │   └── gptsovits/           # TTS Provider
│       ├── pipelines/               # Output Pipeline 测试
│       └── test_output_provider_manager.py
├── integration/                     # 集成测试
├── mocks/                           # Mock 对象
│   ├── mock_input_provider.py
│   ├── mock_decision_provider.py
│   └── mock_output_provider.py
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

### 运行特定域的测试
```bash
# 输入域测试
uv run pytest tests/domains/input/ -v

# 决策域测试
uv run pytest tests/domains/decision/ -v

# 输出域测试
uv run pytest tests/domains/output/ -v
```

### 运行特定 Provider 测试
```bash
# VTS Provider
uv run pytest tests/domains/output/providers/avatar/vts/ -v

# GPTSoVITS Provider
uv run pytest tests/domains/output/providers/gptsovits/ -v
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

- `test_<provider_name>_provider.py` - Provider 测试（如 `test_stt_input_provider.py`）
- `test_<manager_name>_manager.py` - Manager 测试（如 `test_input_provider_manager.py`）
- `test_<pipeline_name>_pipeline.py` - Pipeline 测试（如 `test_rate_limit_pipeline.py`）
- `test_<component>.py` - 核心组件测试（如 `test_event_bus.py`）

## 目录结构与源码对应关系

| 源码位置 | 测试位置 | 说明 |
|---------|---------|------|
| `src/modules/` | `tests/modules/` | 通用模块（配置、事件、LLM、日志等） |
| `src/domains/input/` | `tests/domains/input/` | 输入域 |
| `src/domains/decision/` | `tests/domains/decision/` | 决策域 |
| `src/domains/output/` | `tests/domains/output/` | 输出域 |
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
- [3域架构](../docs/architecture/overview.md) - 架构设计详解
- [Provider 开发](../docs/development/provider-guide.md) - Provider 开发指南
