# 架构设计审查报告

> **审查日期**: 2026-02-01（更新：B-01 核心功能已实现，B-03 服务注册调用方已迁移）
> **审查范围**: 重构后项目中**尚未解决**的架构问题
> **严重程度**: 🔴 高 | 🟡 中 | 🟢 低

**说明**：历史上已关闭的问题（A-01～A-10）已从正文移除，仅在下文「已解决问题摘要」中一笔带过。正文只保留**当前待办**和**新发现**的问题，便于审阅时聚焦。

---

## 📋 已解决问题摘要（供参考）

以下问题在既往审阅中已标记为完成，此处不再展开描述：

- **A-01** AmaidesuCore 职责过重 → 已引入 FlowCoordinator，Core 为纯组合根
- **A-02** 服务注册与 EventBus 并存 → 已从 AmaidesuCore 移除接口（**已迁移调用方，见 B-03**）
- **A-03** Provider 构造函数不一致 → 已统一为 `__init__(config)` + `setup(event_bus, dependencies)`
- **A-04** MaiCoreDecisionProvider 过重 → 已拆分为 WebSocketConnector + RouterAdapter
- **A-05** Provider/Plugin 边界不清 → 设计已确定（迁移计划见下文 B-02）
- **A-06** 输出层 Provider 依赖 core → 已移除 core 参数
- **A-07** Layer 2 / DataCache → Layer 2 已实现，DataCache 保留为扩展点
- **A-08** 配置分散 → 已引入 ConfigService
- **A-09** 循环依赖 → 已通过 CoreServices 接口与 TYPE_CHECKING 缓解
- **A-10** 废弃代码未清理 → 已移除 BasePlugin、avatar 等
- **B-01** 管道系统未重构成功 → TextPipeline 加载机制已实现，限流和相似文本过滤已接入 Layer 2→3 数据流
- **B-03** 服务注册调用方未迁移 → 已迁移4处代码到EventBus或直接方法调用  

---

## 📋 当前问题总览（未解决）

| 问题编号 | 问题名称                         | 严重程度 | 影响范围   | 状态   |
|----------|----------------------------------|----------|------------|--------|
| **B-01** | 管道系统未重构成功               | 🟡       | 数据流/管道 | ✅ 核心已修复 |
| **B-02** | A-05 迁移计划未实施（Provider 目录与 Registry） | 🟡       | 目录结构   | ⏳ 待实施 |
| **B-03** | A-02 未完成：服务注册调用方未迁移 | 🔴       | 运行时崩溃 | ✅ 已修复 |

---

## 🟡 中等严重度问题

### B-01: 管道系统未重构成功 ✅ 核心功能已实现

**问题描述**（2026-02-01 更新）：

设计文档（`pipeline_refactoring.md`）规定管道应位于 **Layer 2 与 Layer 3 之间**，处理 **Text**（`TextPipeline` 接口），并在 CanonicalLayer 中调用。

**已修复的问题**：

1. ✅ **TextPipeline 加载机制已实现**
   - 在 `PipelineManager` 中添加 `load_text_pipelines()` 方法（lines 655-771）
   - 自动扫描并注册 `TextPipelineBase` 子类
   - `main.py` 调用该方法，确保 TextPipeline 被正确加载

2. ✅ **TextPipeline 已接入 Layer 2→3 数据流**
   - `RateLimitTextPipeline`（限流管道）已实现并接入
   - `SimilarTextFilterPipeline`（相似文本过滤管道）已实现并接入
   - CanonicalLayer 的 `process_text()` 调用路径完整

3. ⏳ **CommandRouter 仍依赖已废弃机制**（待迁移）
   - `command_router/pipeline.py` 仍使用 `self.core.get_service(service_name)`
   - 需要迁移到事件订阅模式（见 B-03）

4. ⚠️ **MessagePipeline 保留用于特定场景**
   - 保留用于 inbound/outbound 场景（如 command_processor）
   - 与 TextPipeline 共存，保持向后兼容

**设计 vs 实现对照**：

| 设计（pipeline_refactoring.md）     | 当前实现 | 状态 |
|------------------------------------|----------|------|
| Pipeline 位于 Layer 2→3，处理 Text | CanonicalLayer 调用 `process_text()`，TextPipeline 已注册 | ✅ 已实现 |
| TextPipeline：process(text, metadata) -> Optional[str] | RateLimitTextPipeline、SimilarTextFilterPipeline 已实现 | ✅ 已实现 |
| 保留 RateLimit、Filter 等           | 已接入 Layer 2→3 数据流 | ✅ 已实现 |
| 移除 CommandRouter（用 Provider/事件替代） | CommandRouter 仍存在，仍使用 get_service | ⏳ 待实施（见 B-03） |

**修复内容**（2026-02-01 实施）：

1. **PipelineManager 扩展**（`src/core/pipeline_manager.py`）：
   - 新增 `load_text_pipelines()` 方法（lines 655-771）
   - 扫描 `TextPipelineBase` 子类并自动注册
   - 配置合并逻辑与 MessagePipeline 一致

2. **main.py 更新**（`main.py:128-164`）：
   - 调用 `load_text_pipelines()` 加载 TextPipeline
   - 日志显示 TextPipeline 加载数量

3. **配置文件更新**（`config-template.toml:176-191`）：
   - 添加 TextPipeline 配置示例（rate_limit、similar_text_filter）
   - 清晰标注新旧架构用途

**影响**：

- ✅ **限流、相似文本过滤**等 TextPipeline 功能已接入 Layer 2→3 数据流，正常生效
- ⚠️ **MessagePipeline** 保留用于特定场景（command_processor 等）
- ⏳ CommandRouter 迁移待实施（与 B-03 一并处理）

**相关代码位置**：

- `src/core/pipeline_manager.py`：TextPipeline 协议（lines 64-100）、TextPipelineBase（lines 103-183）、`process_text()`（lines 397-482）、**新增 `load_text_pipelines()`**（lines 655-771）
- `src/canonical/canonical_layer.py:109`：`_on_normalized_text_ready` 调用 `pipeline_manager.process_text()`
- `main.py:147-148`：调用 `load_text_pipelines()`
- TextPipeline 实现：
  - `src/pipelines/rate_limit/pipeline.py`：RateLimitTextPipeline
  - `src/pipelines/similar_text_filter/pipeline.py`：SimilarTextFilterPipeline

**后续待办**：

- CommandRouter 迁移到事件订阅模式（见 B-03）
- 逐步迁移其他 MessagePipeline 到 TextPipeline（可选）

---

### B-03: A-02 未完成：服务注册调用方未迁移 ✅ 已修复

**问题描述**（已归档）：

A-02 标记「服务注册已废弃」，但实际只完成了一半：**接口从 AmaidesuCore 移除了，调用方却没有迁移**。

**已修复的代码**（2026-02-01 实施）：

| 文件 | 原调用方式 | 修复方式 | 状态 |
|------|-----------|---------|------|
| `src/pipelines/command_router/pipeline.py:132` | `self.core.get_service(service_name)` | 改用 EventBus 发布 `command_router.received` 事件 | ✅ 已修复 |
| `src/pipelines/command_processor/pipeline.py:113` | `self.core.get_service(service_name)` | 改用 EventBus 发布 `command_processor.{event}` 事件 | ✅ 已修复 |
| `src/plugins/bili_danmaku_official/message/base.py:131` | `core.get_service("prompt_context")` | 改用 `core.get_context_manager()` 直接访问 | ✅ 已修复 |
| `src/plugins/keyword_action/actions/dg_lab_shock.py:15` | `core.get_service("dg_lab_control")` | 改用 EventBus 发布 `dg_lab.shock` 事件 | ✅ 已修复 |

**修复详情**：

1. **command_router/pipeline.py**（lines 29-88）：
   - 移除 `_forward_message_to_subscribers()` 和 `_notify_subscriber()` 方法
   - 完全依赖 EventBus 发布 `command_router.received` 事件
   - 移除所有 `self.core.get_service()` 调用

2. **command_processor/pipeline.py**（lines 34-42, 91-135）：
   - 配置格式从 `{"service": "xxx", "method": "yyy"}` 改为 `{"event": "xxx", "event_key": "yyy"}`
   - 不再直接调用服务方法，改为发布 `command_processor.{event}` 事件
   - EventBus 不可用时记录警告并忽略命令

3. **bili_danmaku_official/message/base.py**（lines 131-143）：
   - 使用 `core.get_context_manager()` 替代 `core.get_service("prompt_context")`
   - 直接调用 ContextManager 的方法获取上下文

4. **keyword_action/actions/dg_lab_shock.py**（lines 10-23）：
   - 改用 EventBus 发布 `dg_lab.shock` 事件
   - DG-Lab 插件需要订阅该事件来触发电击动作

**影响**：

- ✅ **运行时崩溃已修复**：所有 `get_service()` 调用已迁移
- ⏳ **插件兼容性**：依赖旧服务注册机制的插件需要更新为事件订阅模式
- ⏳ **文档更新**：README 文档仍需更新（15+ 个文件引用旧模式）

**后续待办**：

- 更新 README 文档，移除 `core.get_service()` 示例
- 确保相关插件订阅新的事件

---

## 🟡 中等严重度问题

### B-02: A-05 迁移计划未实施（Provider 目录与 Registry）⏳ 待实施

**问题描述**：

A-05 已确定「Provider = 原子能力、Plugin = 场景整合」及目录与注册方式，但**代码迁移未做**：

- 设计规定：内置 OutputProvider 放在 `src/rendering/providers/`，由 ProviderRegistry 统一注册，OutputProviderManager 通过 Registry 创建/管理。  
- 当前：内置输出仍位于 `src/providers/`，无 `ProviderRegistry`，官方插件仍自行创建 Provider 实例。

**待实施步骤**（与 A-05 迁移计划一致）：

1. 创建 `src/rendering/provider_registry.py`（或等效注册表）。  
2. 将 `src/providers/` 下内置 OutputProvider 迁至 `src/rendering/providers/`。  
3. 令 OutputProviderManager 基于 Registry 创建/管理 Provider。  
4. 更新官方 Plugin：不再创建 Provider，仅声明依赖（如 `get_required_providers()`）。  
5. 删除空的或仅剩兼容代码的 `src/providers/` 目录。

**说明**：此变更不影响社区插件目录 `plugins/`，社区插件仍可通过 Registry 注册自定义 Provider。

---

## ✅ 做得好的地方（保持不变）

1. **EventBus 设计良好**：优先级、错误隔离、统计功能完善  
2. **DecisionManager 工厂模式**：支持运行时切换 Provider  
3. **LLMService 设计清晰**：统一后端管理、重试、token 统计  
4. **Plugin Protocol 设计**：不继承基类，依赖注入清晰  
5. **FlowCoordinator**：Layer 4→5→6 数据流独立、职责清晰  
6. **AmaidesuCore 纯组合根**：只做组件组合与生命周期  

---

## 📝 优先级建议

### 高优先级

- 无（B-01、B-03 已修复）

### 中优先级

- **B-02** A-05 迁移计划：实施 ProviderRegistry、目录迁移与 Plugin 声明式依赖

### 低优先级

- 更新 README 文档，移除 `core.get_service()` 示例（B-03 后续待办）
- 逐步迁移其他 MessagePipeline 到 TextPipeline（可选，B-01 后续待办）

---

## 🔗 相关文档

- [架构设计总览](./overview.md)
- [Pipeline 重新设计](./pipeline_refactoring.md)（目标架构；**实现未完成**，见本文 B-01）
- [插件系统设计](./plugin_system.md)
- [Avatar 系统重构](./avatar_refactoring.md)
