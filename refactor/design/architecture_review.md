# 架构设计审查报告

> **审查日期**: 2026-02-01（更新：B-01 核心功能已实现，B-03 服务注册调用方未迁移）
> **审查范围**: 重构后项目中**尚未解决**的架构问题
> **严重程度**: 🔴 高 | 🟡 中 | 🟢 低

**说明**：历史上已关闭的问题（A-01～A-10）已从正文移除，仅在下文「已解决问题摘要」中一笔带过。正文只保留**当前待办**和**新发现**的问题，便于审阅时聚焦。

---

## 📋 已解决问题摘要（供参考）

以下问题在既往审阅中已标记为完成，此处不再展开描述：

- **A-01** AmaidesuCore 职责过重 → 已引入 FlowCoordinator，Core 为纯组合根
- **A-02** 服务注册与 EventBus 并存 → 已从 AmaidesuCore 移除接口（**但调用方未迁移，见 B-03**）
- **A-03** Provider 构造函数不一致 → 已统一为 `__init__(config)` + `setup(event_bus, dependencies)`
- **A-04** MaiCoreDecisionProvider 过重 → 已拆分为 WebSocketConnector + RouterAdapter
- **A-05** Provider/Plugin 边界不清 → 设计已确定（迁移计划见下文 B-02）
- **A-06** 输出层 Provider 依赖 core → 已移除 core 参数
- **A-07** Layer 2 / DataCache → Layer 2 已实现，DataCache 保留为扩展点
- **A-08** 配置分散 → 已引入 ConfigService
- **A-09** 循环依赖 → 已通过 CoreServices 接口与 TYPE_CHECKING 缓解
- **A-10** 废弃代码未清理 → 已移除 BasePlugin、avatar 等
- **B-01** 管道系统未重构成功 → TextPipeline 加载机制已实现，限流和相似文本过滤已接入 Layer 2→3 数据流  

---

## 📋 当前问题总览（未解决）

| 问题编号 | 问题名称                         | 严重程度 | 影响范围   | 状态   |
|----------|----------------------------------|----------|------------|--------|
| **B-01** | 管道系统未重构成功               | 🟡       | 数据流/管道 | ✅ 核心已修复 |
| **B-02** | A-05 迁移计划未实施（Provider 目录与 Registry） | 🟡       | 目录结构   | ⏳ 待实施 |
| **B-03** | A-02 未完成：服务注册调用方未迁移 | 🔴       | 运行时崩溃 | ⏳ 待修复 |

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

### B-03: A-02 未完成：服务注册调用方未迁移 ⏳ 待修复

**问题描述**：

A-02 标记「服务注册已废弃」，但实际只完成了一半：**接口从 AmaidesuCore 移除了，调用方却没有迁移**。

1. **AmaidesuCore 已移除服务注册**  
   - `register_service()` 和 `get_service()` 方法已从 `src/core/amaidesu_core.py` 删除  
   - `_services: Dict[str, Any]` 字典也不存在

2. **仍有代码调用 `core.get_service()`（运行时会崩溃）**  

   | 文件 | 调用代码 | 影响 |
   |------|---------|------|
   | `src/pipelines/command_router/pipeline.py:132` | `self.core.get_service(service_name)` | ❌ AttributeError |
   | `src/pipelines/command_processor/pipeline.py:113` | `self.core.get_service(service_name)` | ❌ AttributeError |
   | `src/plugins/bili_danmaku_official/message/base.py:131` | `core.get_service("prompt_context")` | ❌ AttributeError |
   | `src/plugins/keyword_action/actions/dg_lab_shock.py:15` | `core.get_service("dg_lab_control")` | ❌ AttributeError |

3. **15+ 个 README 文档仍引用旧模式**  
   - `vtube_studio/README.md`、`tts/README.md`、`subtitle/README.md` 等仍示例 `core.get_service()`  
   - 社区开发者参照文档会写出无法运行的代码

**影响**：

- **运行时崩溃**：上述代码一旦执行到，会抛出 `AttributeError: 'AmaidesuCore' object has no attribute 'get_service'`
- **目前"能跑"的原因**：管道系统未接入 6 层数据流（B-01），旧插件可能未启用
- **文档误导**：README 仍指引使用已删除的 API

**建议（修复方向）**：

1. **代码迁移**  
   - 管道（command_router、command_processor）：改用 EventBus 发布/订阅（与 B-01 一并处理）  
   - bili_danmaku_official：改为通过依赖注入获取 ContextManager，或订阅事件  
   - keyword_action：改用 EventBus 调用 dg_lab 服务

2. **文档更新**  
   - 批量更新 README，移除 `core.get_service()` 示例  
   - 改为 EventBus 事件订阅或依赖注入模式

3. **ContextManager 的访问方式**  
   - 方案 A：通过 EventBus 请求/响应模式获取上下文  
   - 方案 B：在 CanonicalLayer 统一附加上下文到 CanonicalMessage  
   - 方案 C：创建 PromptBuilder 服务，通过依赖注入使用

**相关代码位置**：

- 已删除的接口：`src/core/amaidesu_core.py`（无 `get_service`）  
- 仍在调用的代码：见上表  
- 过时文档：`src/plugins/*/README.md`（15+ 个文件）

**与其他问题的关联**：

- **B-01**：command_router、command_processor 的 `get_service` 调用属于管道系统问题，可一并修复  
- **A-02**：本问题是 A-02 的遗留，A-02 应标记为「部分完成」

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

- **B-03** 服务注册调用方未迁移：迁移 4 处代码到 EventBus、更新 15+ 个 README 文档、确定 ContextManager 新访问方式。（**会导致运行时崩溃，优先修复**）

### 中优先级

- **B-01** 管道系统：核心功能已实现，后续可选迁移 CommandRouter 到事件订阅模式
- **B-02** A-05 迁移计划：实施 ProviderRegistry、目录迁移与 Plugin 声明式依赖。

---

## 🔗 相关文档

- [架构设计总览](./overview.md)
- [Pipeline 重新设计](./pipeline_refactoring.md)（目标架构；**实现未完成**，见本文 B-01）
- [插件系统设计](./plugin_system.md)
- [Avatar 系统重构](./avatar_refactoring.md)
