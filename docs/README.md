# Amaidesu 文档

为 AI 编码代理和协作者提供的项目文档。

## 📖 快速导航

### 新手入门
- [快速开始](getting-started.md) - 环境设置和基本使用
- [开发规范](development-guide.md) - 代码风格和开发约定

### 架构理解
- [3域架构总览](architecture/overview.md) - 项目核心架构
- [数据流规则](architecture/data-flow.md) - 3域数据流和事件约束
- [事件系统](architecture/event-system.md) - EventBus 使用指南

### 开发指南
- [Provider 开发](development/provider-guide.md) - Input/Decision/Output Provider
- [管道开发](development/pipeline-guide.md) - TextPipeline 开发
- [提示词管理](development/prompt-management.md) - PromptManager 使用
- [测试规范](development/testing-guide.md) - 测试编写规范

### 核心模块
- [事件系统](modules/events.md) - EventBus 事件总线
- [配置管理](modules/config.md) - 配置加载和验证
- [对话上下文](modules/context.md) - 会话管理和历史
- [LLM 客户端](modules/llm.md) - 大语言模型调用
- [日志系统](modules/logging.md) - 统一日志配置
- [提示词管理](modules/prompts.md) - PromptManager 使用
- [音频流传输](modules/streaming.md) - 音频数据传输
- [TTS 服务](modules/tts.md) - 语音合成管理
- [核心类型](modules/types.md) - 共享类型定义
- [Provider 注册表](modules/registry.md) - Provider 注册

### API 参考
- [InputProvider API](api/input_provider.md)
- [OutputProvider API](api/output_provider.md)
- [DecisionProvider API](api/decision_provider.md)
- [EventBus API](api/event_bus.md)
- [MaiBot Action 集成](api/maibot_action_integration.md)

### 工具和验证
- [架构验证器](architecture/architectural-validator.md) - 运行时架构约束验证
- [人工测试需求](development/manual-testing-requirements.md) - 需要真实环境测试的组件

## 🎯 按需求查找

### 我想...

**了解项目结构** → [架构总览](architecture/overview.md)

**开发新功能** → [开发规范](development-guide.md) → [Provider开发](development/provider-guide.md)

**修复 Bug** → [测试规范](development/testing-guide.md)

**理解数据流** → [数据流规则](architecture/data-flow.md)

**添加新 Provider** → [Provider 开发指南](development/provider-guide.md)

**配置 LLM 提示词** → [提示词管理](development/prompt-management.md)

**运行测试** → [测试规范](development/testing-guide.md)

**理解事件系统** → [事件系统](architecture/event-system.md)

## 📝 文档结构

```
docs/
├── README.md                          # 本文档
├── getting-started.md                 # 快速开始
├── development-guide.md               # 开发规范
├── architecture/                      # 架构文档
│   ├── overview.md                    # 3域架构总览
│   ├── data-flow.md                   # 数据流规则
│   ├── event-system.md                # 事件系统
│   └── architectural-validator.md     # 架构验证器
├── development/                       # 开发指南
│   ├── provider-guide.md              # Provider 开发
│   ├── pipeline-guide.md              # Pipeline 开发
│   ├── prompt-management.md           # 提示词管理
│   ├── testing-guide.md               # 测试规范
│   └── manual-testing-requirements.md # 人工测试需求
├── modules/                           # 核心模块文档
│   ├── events.md                      # 事件系统
│   ├── config.md                      # 配置管理
│   ├── context.md                     # 对话上下文
│   ├── llm.md                        # LLM 客户端
│   ├── logging.md                    # 日志系统
│   ├── prompts.md                     # 提示词管理
│   ├── streaming.md                   # 音频流传输
│   ├── tts.md                        # TTS 服务
│   ├── types.md                      # 核心类型
│   └── registry.md                   # Provider 注册表
├── api/                               # API 参考
│   ├── input_provider.md
│   ├── output_provider.md
│   ├── decision_provider.md
│   ├── event_bus.md
│   └── maibot_action_integration.md
└── configuration/                      # 配置示例
    └── examples.md
```


## 🚀 快速开始

1. **环境准备**
   ```bash
   # 安装 uv 包管理器
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

   # 同步依赖
   uv sync
   ```

2. **运行项目**
   ```bash
   # 正常运行
   uv run python main.py

   # 调试模式
   uv run python main.py --debug
   ```

3. **运行测试**
   ```bash
   uv run pytest tests/
   ```

详见：[快速开始](getting-started.md)

## ⚠️ 重要注意事项

### 当前架构

项目采用 **3域架构**（Input → Decision → Output）：

```
src/
├── modules/               # 核心基础设施（跨域共享）
│   ├── types/            # 共享类型定义（intent.py, base/）
│   ├── events/           # 事件系统（EventBus, CoreEvents）
│   ├── logging/          # 日志系统
│   ├── context/          # 上下文服务
│   ├── llm/              # LLM 客户端
│   ├── tts/              # TTS 引擎
│   ├── streaming/        # 音频流通道
│   ├── config/           # 配置管理
│   └── prompts/          # 提示词管理
│
├── domains/
│   ├── input/            # Input Domain
│   │   ├── providers/    # 8 个输入 Provider
│   │   │   ├── bili_danmaku/
│   │   │   ├── bili_danmaku_official/
│   │   │   ├── bili_danmaku_official_maicraft/
│   │   │   ├── console_input/
│   │   │   ├── mainosaba/
│   │   │   ├── mock_danmaku/
│   │   │   ├── read_pingmu/
│   │   │   ├── remote_stream/
│   │   │   └── stt/
│   │   └── pipelines/    # 文本预处理管道
│   │
│   ├── decision/         # Decision Domain
│   │   └── providers/    # 3 个决策 Provider
│   │       ├── llm/
│   │       ├── maicore/
│   │       └── maicraft/
│   │
│   └── output/           # Output Domain
│       └── providers/    # 10 个输出 Provider
│           ├── audio/           # TTS 音频输出
│           ├── avatar/          # 虚拟形象控制
│           │   ├── vts/
│           │   ├── warudo/
│           │   └── vrchat/
│           ├── subtitle/        # 字幕输出
│           ├── sticker/         # 贴图输出
│           ├── obs_control/     # OBS 控制
│           ├── remote_stream/   # 远程流输出
│           ├── warudo/          # Warudo 协议
│           └── mock/            # 模拟输出
│
└── amaidesu_core.py      # 核心协调器（组合根）
```

**数据流**：Input Domain → Decision Domain → Output Domain（单向）

详细设计：[架构总览](architecture/overview.md)

### 已废弃的功能

- ❌ **插件系统**：已移除，不再有 `Plugin` 基类
- ❌ **服务注册机制**：改用 EventBus 通信
- ❌ **7层/5层架构**：统一为3域架构

### 核心约定

- 使用 **Pydantic BaseModel** 作为数据类型
- 使用 **EventBus** 进行跨域通信（发布-订阅模式）
- **严格遵守单向数据流**：Input → Decision → Output
- 所有 Provider 通过 **配置驱动** 启用

## 📚 核心规范速查

### 命名约定

- 类名：PascalCase（如 `MyProvider`）
- 函数/变量：snake_case（如 `my_function`）
- Provider 类：以 `Provider` 结尾
- 管道类：以 `Pipeline` 结尾

### 数据类型优先级

1. **Pydantic BaseModel** - 所有数据模型、配置 Schema
2. **dataclass** - 仅用于简单内部统计类
3. **Protocol** - 定义接口协议

### 导入顺序

1. 标准库
2. 第三方库
3. 本地项目导入（从 `src` 开始）

详见：[开发规范](development-guide.md)

## 🔗 相关链接

- **AGENTS.md** - 给 AI 代理的完整指南（项目根目录）
- **CLAUDE.md** - Claude Code 专属规则

---

*最后更新：2026-02-09*
