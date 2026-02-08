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

### API 参考
- [InputProvider API](api/input_provider.md)
- [OutputProvider API](api/output_provider.md)
- [DecisionProvider API](api/decision_provider.md)
- [EventBus API](api/event_bus.md)
- [MaiBot Action 集成](api/maibot_action_integration.md)

### 工具和验证
- [架构验证器](architecture/architectural-validator.md) - 运行时架构约束验证
- [人工测试需求](development/manual-testing-requirements.md) - 需要真实环境测试的组件

### 历史归档
- [配置迁移指南](archive/CONFIG_UPGRADE_GUIDE.md) - 旧配置系统迁移（历史参考）
- [E2E测试缺口分析](archive/VTUBER_FLOW_E2E_GAP_ANALYSIS.md) - E2E测试分析（历史参考）
- [Git历史重组计划](archive/git-history-squash-plan.md) - Git历史整理计划（历史参考）
- [重构优化分析](archive/refactor-optimization-analysis.md) - 已完成的重构项记录

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
├── api/                               # API 参考
│   ├── input_provider.md
│   ├── output_provider.md
│   ├── decision_provider.md
│   ├── event_bus.md
│   └── maibot_action_integration.md
└── archive/                           # 历史归档
    ├── CONFIG_UPGRADE_GUIDE.md
    ├── VTUBER_FLOW_E2E_GAP_ANALYSIS.md
    ├── git-history-squash-plan.md
    └── refactor-optimization-analysis.md
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

- **Input Domain**: 数据采集 + 标准化 + 预处理
- **Decision Domain**: 决策（可替换）
- **Output Domain**: 参数生成 + 渲染

详细设计：[refactor/design/overview.md](../refactor/design/overview.md)

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
- **refactor/design/** - 架构设计文档
- **CLAUDE.md** - Claude Code 专属规则

---

*最后更新：2026-02-09*
