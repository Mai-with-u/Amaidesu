# Amaidesu 架构重构文档索引

> **版本**: v3.0
> **日期**: 2026-02-01
> **状态**: 插件系统已移除，采用纯Provider架构

---

## ⚠️ 当前架构问题

> **重要**: 设计与实现存在不一致，详见 [架构问题分析报告](./ARCHITECTURE_ISSUES_REPORT.md)

| 优先级 | 问题 | 影响 |
|--------|------|------|
| 🔴 P0 | 插件系统残留引用 | 应用无法启动 |
| 🔴 P0 | 输入层主流程未接线 | 数据流完全断裂 |
| 🟡 P1 | LLMService 依赖注入技术债 | 架构不清晰 |

---

## 📋 快速导航

### 我想了解...

**⚠️ 当前有什么问题？**
→ [架构问题分析报告](./ARCHITECTURE_ISSUES_REPORT.md)（**推荐先看**）

**整体架构是什么？**
→ [设计总览](./design/overview.md)

**5层架构如何工作？**
→ [5层架构设计](./design/layer_refactoring.md)

**决策层如何可替换？**
→ [决策层设计](./design/decision_layer.md)

**多个Provider如何并发？**
→ [多Provider并发设计](./design/multi_provider.md)

**⚠️ 插件系统为什么移除？**
→ [插件系统移除说明](./PLUGIN_SYSTEM_REMOVAL.md)

**AmaidesuCore如何重构？**
→ [核心重构设计](./design/core_refactoring.md)

**HTTP服务器如何管理？**
→ [HTTP服务器设计](./design/http_server.md)

**如何实施重构？**
→ [5层架构重构实施计划](./plan/5_layer_refactoring_plan.md)

---

## 📁 文档结构

```
refactor/
├── README.md                            # 本文件 - 文档索引
├── ARCHITECTURE_ISSUES_REPORT.md        # ⚠️ 架构问题分析报告（必读）
├── PLUGIN_SYSTEM_REMOVAL.md             # 插件系统移除说明
│
├── design/                              # 设计文档
│   ├── overview.md                       # 架构总览（2025年新架构）
│   ├── layer_refactoring.md              # 5层架构设计
│   ├── decision_layer.md                 # 决策层设计
│   ├── multi_provider.md                 # 多Provider并发设计
│   ├── core_refactoring.md               # AmaidesuCore重构设计
│   ├── http_server.md                    # HTTP服务器设计
│   ├── llm_service.md                    # LLM服务设计
│   ├── event_data_contract.md            # 事件数据契约设计
│   ├── pipeline_refactoring.md           # Pipeline重新设计
│   ├── avatar_refactoring.md             # 虚拟形象重构设计
│   ├── DESIGN_CONSISTENCY_REPORT.md      # 设计文档一致性检查报告
│   └── plugin_system.md                  # ⚠️ 已废弃
│
└── plan/                                # 实施计划
    └── 5_layer_refactoring_plan.md       # 5层架构重构实施计划
```

---

## 🎯 重构核心要点

### 1. 5层核心数据流（2025年架构）

```
Layer 1-2: Input（输入感知 + 标准化）
    ↓ NormalizedMessage
Layer 3: Decision（决策层，可替换）
    ↓ Intent
Layer 4: Parameters（参数生成）
    ↓ RenderParameters
Layer 5: Rendering（渲染呈现，多Provider并发）
```

### 2. 核心变化

| 变化 | 旧架构（7层） | 新架构（5层） |
|------|-------------|-------------|
| **层级数** | 7层 | 5层 |
| **Layer 1-2** | Input + Normalization | 合并为InputLayer |
| **Layer 3** | Canonical（中间表示） | 移除，功能合并到Layer 2 |
| **Layer 4** | Decision（决策层） | 不变，可替换 |
| **Layer 5** | Understanding（理解层） | 移除，功能由DecisionProvider负责 |
| **Layer 6** | Parameters（参数生成） | 不变 |
| **Layer 7** | Rendering（渲染层） | 不变，重编号为Layer 5 |
| **插件系统** | 存在 | **已移除**，采用纯Provider架构 |

### 3. 为什么移除插件系统？

详见：[插件系统移除说明](./PLUGIN_SYSTEM_REMOVAL.md)

**核心原因**：
- ❌ Plugin在创建Provider，违背了"不创建Provider"的设计原则
- ❌ 与"消灭插件化"的重构目标直接矛盾
- ❌ 增加了一层不必要的抽象，反而使架构更复杂

**新架构优势**：
- ✅ Provider由Manager统一管理，配置驱动启用
- ✅ 职责边界明确：Provider = 原子能力
- ✅ 代码组织更清晰：按数据流层级组织

---

## 🔑 关键设计概念

### Provider（提供者）

| 类型 | 位置 | 职责 | 示例 |
|------|------|------|------|
| **InputProvider** | Layer 1 | 接收外部数据，生成RawData | ConsoleInputProvider, BiliDanmakuProvider |
| **DecisionProvider** | Layer 3 | 处理NormalizedMessage，决策并返回Intent | MaiCoreDecisionProvider, LocalLLMDecisionProvider |
| **OutputProvider** | Layer 5 | 接收渲染参数，执行实际输出 | TTSProvider, SubtitleProvider, VTSProvider |

### Manager（管理者）

- **InputProviderManager**：管理输入Provider的生命周期
- **DecisionManager**：管理决策Provider，支持运行时切换
- **OutputProviderManager**：管理输出Provider的生命周期

### 配置驱动

```toml
# 输入Provider配置
[input]
enabled = ["console", "bili_danmaku", "minecraft"]

[input.providers.console]
source = "stdin"

# 决策Provider配置
[decision]
default_provider = "maicore"

# 输出Provider配置
[output]
enabled = ["tts", "subtitle", "vts"]
```

---

## 📊 架构演进

### v1.0（2024年）

- 24个插件，18个服务注册
- 过度插件化，依赖地狱
- 模块定位模糊

### v2.0（2025年初）

- 插件系统 + Provider系统双轨并行
- Plugin创建和管理Provider
- 仍然存在职责边界模糊的问题

### v3.0（2025年2月，当前）

- **移除插件系统**
- Provider由Manager统一管理
- 配置驱动启用/禁用
- 5层架构，职责清晰

---

## ✅ 成功标准

### 技术指标
- ✅ 所有现有功能正常运行
- ✅ 配置文件行数减少40%以上
- ✅ 核心功能响应时间无增加
- ✅ 代码重复率降低30%以上
- ✅ 服务注册调用减少80%以上
- ✅ EventBus事件调用覆盖率90%以上
- ✅ 插件系统已移除，Provider由Manager统一管理

### 架构指标
- ✅ 清晰的5层核心数据流架构
- ✅ 决策层可替换（支持多种DecisionProvider）
- ✅ 多Provider并发支持（输入层和输出层）
- ✅ 层级间依赖关系清晰（单向依赖）
- ✅ EventBus为内部主要通信模式
- ✅ Provider模式替代重复插件
- ✅ 配置驱动，无需修改代码即可启用/禁用Provider
- ✅ 插件系统已完全移除

---

## 🔗 相关资源

### 状态报告
- [架构问题分析报告](./ARCHITECTURE_ISSUES_REPORT.md) - **当前架构问题和修复建议**
- [设计文档一致性检查报告](./design/DESIGN_CONSISTENCY_REPORT.md) - 文档一致性验证

### 设计文档
- [设计总览](./design/overview.md) - 2025年新架构总览
- [5层架构设计](./design/layer_refactoring.md) - 详细描述5层核心数据流
- [决策层设计](./design/decision_layer.md) - 可替换的决策Provider系统
- [多Provider并发设计](./design/multi_provider.md) - Provider管理架构

### 实施计划
- [5层架构重构实施计划](./plan/5_layer_refactoring_plan.md) - 详细的重构步骤

### 迁移指南
- [插件系统移除说明](./PLUGIN_SYSTEM_REMOVAL.md) - 配置和代码迁移指南

### docs 目录相关文档
- [尚未完成的重构项](../docs/REFACTOR_REMAINING.md) - 重构剩余工作
- [VTuber 全流程 E2E 测试缺口分析](../docs/VTUBER_FLOW_E2E_GAP_ANALYSIS.md) - E2E 测试缺口

---

## ❓ 常见问题

### Q: 为什么要从7层改为5层？

**A**: 简化架构，消除冗余：
- Layer 2（Normalization）和Layer 3（Canonical）合并
- Layer 5（Understanding）的功能由DecisionProvider承担
- 减少数据转换开销，提高性能

### Q: 插件系统为什么要移除？

**A**: 插件系统与"消灭插件化"的重构目标不兼容：
- Plugin在创建Provider，违背了设计原则
- 增加了一层不必要的抽象
- 纯Provider架构更简单、更清晰

详见：[插件系统移除说明](./PLUGIN_SYSTEM_REMOVAL.md)

### Q: 社区开发者如何扩展功能？

**A**: 直接添加Provider：

1. 在对应层创建Provider文件：`src/layers/{layer}/providers/my_provider.py`
2. 在配置中启用：`[input]enabled = ["console", "my_provider"]`
3. 无需创建Plugin

详见：[设计总览 - 社区扩展](./design/overview.md#社区扩展)

---

**最后更新**：2026年2月1日
**维护者**：Amaidesu Team
