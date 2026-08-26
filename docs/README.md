# 项目文档

本目录包含 Amaidesu 项目的完整技术文档。

## 文档导航

### 新手入门

| 文档 | 说明 |
|------|------|
| [快速开始](getting-started.md) | 环境搭建和基本使用 |
| [开发规范](development-guide.md) | 代码风格和约定 |

### 架构理解

| 文档 | 说明 |
|------|------|
| [v2.0.0 架构叙事](architecture/v2-architecture.md) | 重构缘由与设计推导（连续叙事，先读这篇） |
| [架构总览](architecture/overview.md) | v2.0.0 Agent+工具+存储+编排架构详解（速查） |
| [数据流规则](architecture/data-flow.md) | 数据流约束和规则 |
| [事件系统](architecture/event-system.md) | EventBus 使用指南 |
| [事件命名规范](architecture/event-naming-convention.md) | 语义域事件命名规则 |
| [架构决策记录](architecture/adr/README.md) | ADR 格式说明与决策清单 |

### 开发指南

| 文档 | 说明 |
|------|------|
| [组件开发指南](development/component-guide.md) | 采集器/工具/Agent 三范式开发详解 |
| [事件系统](architecture/event-system.md#事件拦截器interceptor) | EventBus 与事件拦截器（含开发指南） |
| [提示词管理](development/prompt-management.md) | PromptManager 使用 |
| [依赖注入](development/dependency-injection.md) | 依赖注入约定与决策清单 |
| [测试指南](development/testing-guide.md) | 测试规范和最佳实践 |
| [模拟直播间工具](development/simulator-guide.md) | 模拟直播间服务（SimulatorService）使用 |
| [文档维护规范](development/documentation-guide.md) | 文档编写、单一事实源与 ADR 规范 |

## 快速链接

- [README](../README.md) - 项目主页
- [AGENTS.md](../AGENTS.md) - AI 代理规则
