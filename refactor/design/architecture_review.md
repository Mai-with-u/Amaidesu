# 架构设计审查报告

> **审查日期**: 2026-02-01（更新：B-05 Decision Layer 数据流集成已修复）
> **审查范围**: 重构后项目中**尚未解决**的架构问题
> **严重程度**: 🔴 高 | 🟡 中 | 🟢 低

**说明**：历史上已关闭的问题（A-01～A-10、B-01～B-04）已从正文移除，仅在下文「已解决问题摘要」中一笔带过。正文只保留**当前待办**和**新发现**的问题，便于审阅时聚焦。

---

## 📋 已解决问题摘要（供参考）

以下问题在既往审阅中已标记为完成，此处不再展开描述：

- **A-01** AmaidesuCore 职责过重 → 已引入 FlowCoordinator，Core 为纯组合根
- **A-02** 服务注册与 EventBus 并存 → 已从 AmaidesuCore 移除接口（**已迁移调用方，见 B-03**）
- **A-03** Provider 构造函数不一致 → 已统一为 `__init__(config)` + `setup(event_bus, dependencies)`
- **A-04** MaiCoreDecisionProvider 过重 → 已拆分为 WebSocketConnector + RouterAdapter
- **A-05** Provider/Plugin 边界不清 → 已实施 ProviderRegistry 和目录迁移（**见 B-02**）
- **A-06** 输出层 Provider 依赖 core → 已移除 core 参数
- **A-07** Layer 2 / DataCache → Layer 2 已实现，DataCache 保留为扩展点
- **A-08** 配置分散 → 已引入 ConfigService
- **A-09** 循环依赖 → 已通过 CoreServices 接口与 TYPE_CHECKING 缓解
- **A-10** 废弃代码未清理 → 已移除 BasePlugin、avatar 等
- **B-01** 管道系统未重构成功 → TextPipeline 加载机制已实现，限流和相似文本过滤已接入 Layer 2→3 数据流
- **B-02** A-05 迁移计划未实施 → ProviderRegistry 已实现，内置 Provider 已迁移到 `src/rendering/providers/`，OutputProviderManager 已重构使用 Registry
- **B-03** 服务注册调用方未迁移 → 已迁移4处代码到EventBus或直接方法调用
- **B-04** 事件数据 IDE 类型识别缺失 → 已创建 payloads.py 并更新 EventBus 文档
- **B-05** Decision Layer 数据流集成缺失 → 已实现 DecisionManager 订阅 canonical.message_ready、创建 UnderstandingLayer 解析 MessageBase → Intent

---

## 📋 当前问题总览（未解决）

| 问题编号 | 问题名称 | 严重程度 | 影响范围 | 状态 |
|----------|----------|----------|----------|------|

**所有高优先级问题已解决！** ✅

---

## ✅ B-05: Decision Layer 数据流集成缺失（已解决）

### 问题描述（已修复）

当前实现中 **Layer 3 → Layer 4 决策层 → Layer 5** 的数据流存在三处断裂：

1. **DecisionManager 未在 main.py 中创建**：`DecisionManager` 存在但未被实例化和启动
2. **`canonical.message_ready` 无订阅者**：`CanonicalLayer` 发射该事件后，无核心组件订阅并触发决策
3. **Decision 响应未解析为 Intent**：`MaiCoreDecisionProvider` 发射 `maicore.message`，但无组件将 `MessageBase` 解析为 `Intent` 并发射 `understanding.intent_generated`

### 解决方案实施

#### 1. main.py 中创建 DecisionManager ✅

- 在 `create_app_components()` 中创建 `DecisionManager` 实例
- 创建 `DecisionProviderFactory` 并注册 `MaiCoreDecisionProvider`
- 调用 `setup()` 初始化 provider
- 在 `run_shutdown()` 中添加 cleanup

#### 2. DecisionManager 订阅 canonical.message_ready ✅

- 在 `DecisionManager.setup()` 中订阅 `canonical.message_ready` 事件
- 添加 `_on_canonical_message_ready()` 事件处理方法
- 调用当前活动的 Provider 的 `decide()` 方法

#### 3. 创建 UnderstandingLayer ✅

- 新建 `src/understanding/understanding_layer.py`
- 订阅 `decision.response_generated` 事件
- 使用 `ResponseParser` 解析 `MessageBase` → `Intent`
- 发射 `understanding.intent_generated` 事件

#### 4. 更新 MaiCoreDecisionProvider ✅

- 将事件名从 `maicore.message` 改为 `decision.response_generated`

#### 5. 在 main.py 中集成 UnderstandingLayer ✅

- 在 `create_app_components()` 中创建 `UnderstandingLayer`
- 在 `run_shutdown()` 中添加 cleanup

### 完整数据流（实施后）

```
InputProvider → perception.raw_data.generated
       ↓
InputLayer → normalization.text.ready
       ↓
CanonicalLayer → canonical.message_ready
       ↓
DecisionManager → active_provider.decide() → decision.response_generated
       ↓
UnderstandingLayer → ResponseParser → understanding.intent_generated
       ↓
FlowCoordinator → ExpressionGenerator → OutputProviderManager
```

### 修改的文件

- `src/core/decision_manager.py` - 添加事件订阅和处理
- `src/core/providers/maicore_decision_provider.py` - 更新事件名称
- `src/understanding/understanding_layer.py` - 新建组件
- `main.py` - 集成 DecisionManager 和 UnderstandingLayer

---

## ✅ 做得好的地方（保持不变）

1. **EventBus 设计良好**：优先级、错误隔离、统计功能完善
2. **DecisionManager 工厂模式**：支持运行时切换 Provider
3. **LLMService 设计清晰**：统一后端管理、重试、token 统计
4. **Plugin Protocol 设计**：不继承基类，依赖注入清晰
5. **FlowCoordinator**：Layer 5→6→7 数据流独立、职责清晰
6. **AmaidesuCore 纯组合根**：只做组件组合与生命周期
7. **7层架构数据流完整**：从 InputProvider 到 OutputProvider 的完整链路已打通

---

## 📝 优先级建议

### 高优先级

**暂无高优先级问题！** 🎉

### 中优先级

- 无

### 低优先级

- 更新 README 文档，移除 `core.get_service()` 示例（B-03 后续待办）
- Plugin 迁移到声明式依赖（B-02 后续优化）
- 扩展事件类型定义（为更多事件添加 payloads 类型注解，B-04 后续优化）

---

## 🔗 相关文档

- [架构设计总览](./overview.md)
- [Pipeline 重新设计](./pipeline_refactoring.md)（目标架构；**已实现**，见已解决问题摘要 B-01）
- [插件系统设计](./plugin_system.md)
- [Avatar 系统重构](./avatar_refactoring.md)
