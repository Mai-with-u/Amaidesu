# Amaidesu 架构设计总览

## 📋 文档结构

本文档是Amaidesu架构重构的设计总览。详细设计请查看：

 - [6层架构设计](./layer_refactoring.md) - 核心数据流的6层架构
 - [决策层设计](./decision_layer.md) - 可替换的决策Provider系统
 - [多Provider并发设计](./multi_provider.md) - 输入/输出层并发处理
 - [插件系统设计](./plugin_system.md) - 插件系统和Provider接口
 - [核心重构设计](./core_refactoring.md) - AmaidesuCore的彻底解耦
 - [事件数据契约设计](./event_data_contract.md) - 类型安全的事件数据契约系统
 - [LLM服务设计](./llm_service.md) - 统一的LLM调用服务
 - [Avatar系统重构](./avatar_refactoring.md) - 虚拟形象控制系统重构到6层架构

---

## 🎯 重构目标

### 核心问题

1. **过度插件化**：24个插件中，核心功能也作为插件
2. **依赖地狱**：18个插件使用服务注册，形成复杂依赖链
3. **模块定位模糊**：核心功能、可选插件、测试工具都作为插件

### 重构目标

1. **消灭插件化**：核心功能全部模块化
2. **统一接口**：同一功能收敛到统一接口，用Provider模式动态选择实现
3. **消除依赖**：推广EventBus通信，替代服务注册模式
4. **按数据流组织**：按AI VTuber数据处理流程组织层级
5. **职责分离**：驱动与渲染分离，决策层可替换

---

## 🏗️ 架构概览

### 核心理念

**按AI VTuber数据处理的完整流程组织层级，每层有明确的输入和输出格式。**

 - **核心数据流**（Layer 1-6）：按AI VTuber数据处理流程组织
 - **决策层**（Decision Layer）：可替换的决策Provider系统
 - **Provider/Plugin 职责分离**：Provider = 原子能力，Plugin = 场景整合（详见下文）
 - **EventBus**：唯一的跨层通信机制，实现松耦合
 - **事件数据契约**（Event Contract）：类型安全的事件数据格式，支持社区扩展
 - **LLM服务**（LLM Service）：统一的LLM调用基础设施，与EventBus同级

### Provider/Plugin 职责边界

```
Provider = 原子能力（单一职责、可复用、统一管理）
Plugin = 能力组合（整合 Provider、提供业务场景、不创建 Provider）
```

| 参与者 | 职责 | 创建 Provider | 管理方式 |
|--------|------|--------------|----------|
| **内置 Provider** | 核心原子能力 | 放在层目录下 | Manager 直接管理 |
| **官方 Plugin** | 场景整合 | 不创建，只声明依赖 | 配置驱动 |
| **第三方插件** | 扩展能力 | 通过 Registry 注册 | 统一注册机制 |

**为什么这样设计？**

如果 Plugin 创建并管理自己的 Provider，会导致：
1. 管理分散，没有统一入口
2. 插件之间可能绕过 EventBus，直接服务注册
3. 重蹈重构前的覆辙（24个插件，18个服务注册）

→ 详见 [插件系统设计](./plugin_system.md) 和 [架构设计审查](./architecture_review.md)（当前未解决问题含 Provider 迁移计划 B-02、管道系统 B-01）

### 架构分层

```
外部输入（弹幕、游戏、语音）
  ↓
【Layer 1: 输入感知】多个InputProvider并发采集
  ↓
【Layer 2: 输入标准化】统一转换为Text
  ↓
【Layer 3: 中间表示】构建CanonicalMessage
  ↓
【决策层：DecisionProvider】⭐ 可替换、可扩展
  ├─ MaiCoreDecisionProvider (默认）
  ├─ LocalLLMDecisionProvider (可选）
  └─ RuleEngineDecisionProvider (可选)
  ↓
DecisionProvider返回MessageBase
  ↓
【Layer 4: 表现理解】解析MessageBase → Intent
  ↓
【Layer 5: 表现生成】生成RenderParameters
  ↓
【Layer 6: 渲染呈现】多个OutputProvider并发渲染
  ↓
【插件系统：Plugin】社区开发的插件能力
```

---

## 🔗 相关文档

### 设计文档
 
 - [6层架构设计](./layer_refactoring.md) - 详细描述6层核心数据流（含元数据管理）
 - [决策层设计](./decision_layer.md) - 可替换的决策Provider系统
 - [多Provider并发设计](./multi_provider.md) - 输入/输出层并发处理（含错误处理和生命周期）
 - [插件系统设计](./plugin_system.md) - 插件系统和Provider接口（含迁移指南）
 - [核心重构设计](./core_refactoring.md) - AmaidesuCore的彻底解耦（含HTTP服务器管理）
 - [事件数据契约设计](./event_data_contract.md) - 类型安全的事件数据契约系统（Pydantic + 开放式注册表）
 - [LLM服务设计](./llm_service.md) - 统一的LLM调用服务（核心基础设施）
 - [Avatar系统重构](./avatar_refactoring.md) - 虚拟形象控制系统重构（消除与6层架构的重复）
 - [架构设计审查](./architecture_review.md) - **未解决**的架构问题（含管道系统未重构成功 B-01、Provider 迁移 B-02）
 - [DataCache设计](./data_cache.md) - 原始数据缓存服务
 - [Pipeline重新设计](./pipeline_refactoring.md) - TextPipeline 目标设计（**实现未完成**，见 [审查 B-01](./architecture_review.md#b-01-管道系统未重构成功--待修复)）
 - [HTTP服务器设计](./http_server.md) - 基于FastAPI的HTTP服务器

### 实施计划

- [实施计划总览](../plan/overview.md) - 重构实施计划总览
- [Phase 1: 基础设施](../plan/phase1_infrastructure.md) - Provider接口和EventBus
- [Phase 2: 输入层](../plan/phase2_input.md) - Layer 1-2实现
- [Phase 3: 决策层](../plan/phase3_decision.md) - 决策层+Layer 3-4实现
- [Phase 4: 输出层](../plan/phase4_output.md) - Layer 5-6实现
 - [Phase 5: 插件系统](../plan/phase5_plugins.md) - 插件系统实现
- [Phase 6: 清理和测试](../plan/phase6_cleanup.md) - 清理、测试和迁移

---

## ✅ 成功标准

### 技术指标
- ✅ 所有现有功能正常运行
- ✅ 配置文件行数减少40%以上
- ✅ 核心功能响应时间无增加
- ✅ 代码重复率降低30%以上
- ✅ 服务注册调用减少80%以上
- ✅ EventBus事件调用覆盖率90%以上
 - ✅ 插件系统正常加载官方插件和社区插件

### 架构指标
- ✅ 清晰的6层核心数据流架构
- ✅ 决策层可替换（支持多种DecisionProvider）
- ✅ 多Provider并发支持（输入层和输出层）
- ✅ 层级间依赖关系清晰（单向依赖）
- ✅ EventBus为内部主要通信模式
- ✅ Provider模式替代重复插件
- ✅ 工厂模式支持动态切换
- ✅ 插件系统支持社区开发
- ✅ 事件数据契约类型安全（Pydantic Model + 开放式注册表）

---

## 🚀 快速导航

### 我想知道...

**整体架构是什么？**
→ 阅读[6层架构设计](./layer_refactoring.md)

**决策层如何工作？**
→ 阅读[决策层设计](./decision_layer.md)

**多个Provider如何并发？**
→ 阅读[多Provider并发设计](./multi_provider.md)

**如何开发插件？**
→ 阅读[插件系统设计](./plugin_system.md)

**如何定义事件数据格式？**
→ 阅读[事件数据契约设计](./event_data_contract.md)

**AmaidesuCore如何重构？**
→ 阅读[核心重构设计](./core_refactoring.md)

**LLM调用如何统一管理？**
→ 阅读[LLM服务设计](./llm_service.md)

**Avatar系统如何重构？**
→ 阅读[Avatar系统重构](./avatar_refactoring.md)

**如何实施重构？**
→ 阅读[实施计划总览](../plan/overview.md)
