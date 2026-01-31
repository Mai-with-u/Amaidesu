# 尚未完成的重构项

> 基于 `refactor/design/` 设计文档与当前代码库对比整理。  
> 更新日期：2026-01-31

---

## 一、管道系统（Pipeline）

**设计文档**：[pipeline_refactoring.md](../refactor/design/pipeline_refactoring.md)

| 状态 | 说明 |
|------|------|
| ✅ 已实现 | **TextPipeline** 接口：`TextPipeline` 协议、`TextPipelineBase` 基类、`PipelineErrorHandling` 枚举、`PipelineStats` 统计类、`PipelineException` 异常已在 `pipeline_manager.py` 中实现。 |
| ✅ 已实现 | **PipelineManager.process_text()**：新版方法已实现，支持超时控制、错误处理策略（CONTINUE/STOP/DROP）、统计信息、并发保护（asyncio.Lock）。 |
| ✅ 已接入 | Layer 2→3 之间已插入 **process_text**：`CanonicalLayer._on_normalized_text_ready` 中调用 `pipeline_manager.process_text(text, metadata)`，支持文本预处理和丢弃逻辑。 |
| ✅ 已实现 | **TextPipeline 示例**：`RateLimitTextPipeline`（限流）和 `SimilarTextFilterPipeline`（相似文本过滤）已实现。 |
| ⏳ 待迁移 | **现有 MessagePipeline**：command_router、throttle、similar_message_filter、message_logger 等仍为 MessagePipeline，可保留向后兼容或逐步替换。 |
| ⏳ 可移除 | **CommandRouterPipeline** 设计上由 DecisionProvider 替代，可考虑移除。 |

**已完成**：
- TextPipeline 协议与 `process_text()` 已实现并接入 CanonicalLayer
- `src/pipelines/rate_limit/` - RateLimitTextPipeline（限流管道，ThrottlePipeline 的 TextPipeline 版本）
- `src/pipelines/similar_text_filter/` - SimilarTextFilterPipeline（相似文本过滤管道）

**待做**：如有需要，可继续迁移其他 MessagePipeline 到 TextPipeline 接口。现有 MessagePipeline 可保留向后兼容。

---

## 二、事件数据契约（Event Data Contract）

**设计文档**：[event_data_contract.md](../refactor/design/event_data_contract.md)

| 状态 | 说明 |
|------|------|
| ❌ 未实现 | **EventRegistry**（事件注册表）不存在，无 `src/core/events/` 或 `register_core_event`。 |
| ❌ 未实现 | 事件 payload 的 **Pydantic 模型** 与按事件名校验未实现，当前仍为 `data: Any` 与魔法字符串。 |
| ❌ 未实现 | 核心事件（如 `perception.raw_data.generated`、`normalization.text.ready`）的契约类型与校验未落地。 |

**待做**：实现 EventRegistry、为核心事件定义 Pydantic 契约，并在 emit/on 处接入校验（可先 debug 模式）。

---

## 三、HTTP 服务器（独立 FastAPI）✅ 已完成

**设计文档**：[http_server.md](../refactor/design/http_server.md)

| 状态 | 说明 |
|------|------|
| ✅ 已实现 | 独立的 **HttpServer**（FastAPI + `register_route`）已实现：`src/core/http_server.py`。 |
| ✅ 已实现 | **AmaidesuCore** 已添加 `http_server` 属性和 `register_http_callback()` 方法，管理 HttpServer 生命周期（connect/disconnect）。 |
| ✅ 已完成 | **MaiCoreDecisionProvider** 已迁移到使用 HttpServer.register_route() 模式，移除 aiohttp 内部 HTTP 服务器。 |

**已完成**：
- `src/core/http_server.py` - 基于 FastAPI 的独立 HTTP 服务器，支持 `register_route()`
- `src/core/amaidesu_core.py` - AmaidesuCore 管理 HttpServer 生命周期，提供 `register_http_callback()` 便捷方法
- connect() 中启动 HttpServer 并发布 `core.ready` 事件
- disconnect() 中停止 HttpServer
- `src/providers/maicore_decision_provider.py` - 订阅 `core.ready` 事件，通过 `register_http_callback()` 注册 HTTP 路由
- MaiCoreDecisionProvider 移除 aiohttp 依赖，改用 FastAPI 兼容的处理器

---

## 四、Layer 2→Layer 3 桥接（Canonical 层）✅ 已完成

**设计文档**：[layer_refactoring.md](../refactor/design/layer_refactoring.md)、[pipeline_refactoring.md](../refactor/design/pipeline_refactoring.md)

| 状态 | 说明 |
|------|------|
| ✅ 已完成 | **CanonicalLayer** 已实现：订阅 `normalization.text.ready`，构建 CanonicalMessage，发出 `canonical.message_ready`。 |
| ✅ 已完成 | **main.py 已启动 InputLayer 和 CanonicalLayer**：Layer 1→2→3 数据流已打通。 |
| ✅ 已完成 | **输入插件已迁移**：`console_input` 和 `mock_danmaku` 已使用标准事件 `perception.raw_data.generated`。 |

**已完成**：
- `src/canonical/canonical_layer.py` - 实现 Layer 2→3 桥接
- `main.py` - 启动 InputLayer 和 CanonicalLayer
- `src/plugins/console_input/plugin.py` - 使用标准事件格式
- `src/plugins/mock_danmaku/plugin.py` - 使用标准事件格式
- `src/providers/maicore_decision_provider.py` - 兼容新事件 payload 格式

---

## 五、服务注册与 EventBus 迁移

**设计文档**：[overview.md](../refactor/design/overview.md)、[plugin_system.md](../refactor/design/plugin_system.md)

| 状态 | 说明 |
|------|------|
| ❌ 未达标 | **服务注册仍大量使用**：约 62 处 `register_service` / `get_service`，设计目标「服务注册减少 80%」未达成。 |
| ⚠️ 进行中 | 插件已普遍改为 `setup(event_bus, config)` 并返回 Provider 列表，但许多仍通过 `get_service` 获取 TTS、VTS 等，未完全改为 EventBus 事件。 |

**待做**：将剩余强依赖服务注册的调用改为 EventBus 事件或显式依赖注入，减少对 `register_service`/`get_service` 的依赖。

---

## 六、DataCache（可选）

**设计文档**：[data_cache.md](../refactor/design/data_cache.md)

| 状态 | 说明 |
|------|------|
| ⚪ 有意未实现 | 代码注释标明「DataCache 已移除（未实际使用）」；NormalizedText/RawData 保留 `data_ref` 仅为兼容。设计保留，实现为可选。 |

**待做**：若后续需要「按引用传递大对象、按需加载」，再实现 DataCache；否则可维持现状。

---

## 七、AmaidesuCore 与 main 的收尾

**设计文档**：[core_refactoring.md](../refactor/design/core_refactoring.md)

| 状态 | 说明 |
|------|------|
| ✅ 已做 | WebSocket/HTTP/Router 已迁出 Core，由 MaiCoreDecisionProvider 负责。 |
| ✅ 已改进 | **PipelineManager** 已添加 `process_text(text, metadata)` 方法和 TextPipeline 支持；仍保留旧的 MessagePipeline 支持以保持向后兼容。CanonicalLayer 中已接入 `process_text`。 |
| ⚠️ 未完全对齐 | **main.py** 仍向 AmaidesuCore 传入 `maicore_host`、`maicore_port`、`http_host`、`http_port` 等；若决策与 HTTP 完全由 Provider 管理，这些可考虑从 Core 构造中移除或仅作兼容保留。 |

**待做**：管道改为 TextPipeline 并接入后，将 PipelineManager 改为依赖 event_bus 并处理文本；视情况清理 main 对 Core 的 MaiCore/HTTP 相关传参。

---

## 八、实施计划文档

| 状态 | 说明 |
|------|------|
| ❌ 缺失 | [overview.md](../refactor/design/overview.md) 中引用的 **refactor/plan/**（如 `phase1_infrastructure.md` ~ `phase6_cleanup.md`）在仓库中不存在。 |

**待做**：若需按阶段执行重构，可补全 `refactor/plan/` 下的实施计划文档。

---

## 汇总表

| 类别           | 状态       | 优先级建议 |
|----------------|------------|------------|
| Pipeline 重设计 | ✅ 核心+示例已完成 | -          |
| Layer 2→3 桥接 | ✅ 已完成   | -          |
| 事件数据契约   | ⏳ 进行中（另一 AI） | 中         |
| 服务注册瘦身   | 未达标     | 中         |
| HTTP 服务器    | ✅ 已完成（含 Provider 迁移） | -          |
| DataCache      | 有意未实现 | 低/可选    |
| 实施计划文档   | 缺失       | 低         |

---

## 建议实施顺序

1. ~~**Layer 2→3 桥接**~~：✅ 已完成（CanonicalLayer 实现，main.py 启动 InputLayer 和 CanonicalLayer）。
2. ~~**Pipeline（TextPipeline + process_text）**~~：✅ 核心已完成（TextPipeline 协议、PipelineManager.process_text()、CanonicalLayer 接入）。
3. ~~**main 启动输入层**~~：✅ 已完成（InputLayer、CanonicalLayer 在 main.py 中启动）。
4. **事件契约**：⏳ 另一 AI 正在进行 EventRegistry 与 Pydantic 事件 payload 的实现。
5. **服务注册**：逐步用 EventBus/依赖注入替代剩余 `get_service` 调用。
6. **Pipeline 迁移**：将现有 MessagePipeline（throttle、filter 等）迁移到 TextPipeline 接口。

以上顺序可根据排期与风险调整。

---

## 与事件数据契约并行可做的重构

> 若已有 AI/人力在做**事件数据契约**（EventRegistry、Pydantic 事件 payload、emit/on 校验），以下重构可与该工作**并行进行**，且基本不修改同一批文件或同一批事件的 payload 结构，合并冲突风险低。

### 可并行项一览

| 重构项 | 为何可并行 | 建议注意点 |
|--------|------------|------------|
| ~~**1. Layer 2→3 桥接（Canonical 层）**~~ | ✅ **已完成** | - |
| ~~**2. 管道系统（TextPipeline + process_text）**~~ | ✅ **核心已完成**：TextPipeline 协议、PipelineManager.process_text()、CanonicalLayer 接入均已实现。待迁移现有 MessagePipeline。 | - |
| ~~**3. main 中启动 InputLayer / InputProviderManager**~~ | ✅ **已完成** | - |
| ~~**4. HTTP 服务器（独立 FastAPI + register_route）**~~ | ✅ **核心已完成**：HttpServer 类已实现，AmaidesuCore 已集成 HttpServer 管理。待迁移 MaiCoreDecisionProvider 使用新 HttpServer。 | - |
| **5. 服务注册瘦身（用 EventBus 替代 get_service）** | 可先做「用 `event_bus.emit/on` 替代部分 `get_service` 调用」，**不在此处新增或修改事件的 Pydantic 模型**；新事件名与 payload 保持简单 dict，由契约方后续补类型。 | 避免在服务注册瘦身时**新增**或**修改**契约文档中已列出的核心事件（如 `perception.raw_data.generated`、`normalization.text.ready`、`decision.response_generated` 等）的 payload 结构；新事件（如 `tts.synthesize.request`）保持 dict，由契约方统一补契约。 |

### 不建议与事件契约并行的部分

- **不要**在并行分支里：新增/修改 `src/core/events/`、EventRegistry、或现有核心事件的 Pydantic 模型（易与契约分支冲突）。
- **不要**在并行分支里：改动 `event_bus.emit` / `event_bus.on` 的**签名或入参类型**（契约方可能在同一处加校验或类型）。

### 推荐并行组合

- ~~**组合 A（数据流打通）**~~：✅ **已完成**（Layer 2→3 桥接 + main 启动 InputLayer/CanonicalLayer）。

- ~~**组合 B（在桥接上接管道）**~~：✅ **已完成**（TextPipeline + process_text 已接入 CanonicalLayer）。

- ~~**组合 C（独立模块）**~~：✅ **已完成**（HttpServer + AmaidesuCore 集成）。

- ~~**组合 D（Pipeline 迁移）**~~：✅ **已完成**（RateLimitTextPipeline 和 SimilarTextFilterPipeline 已实现，现有 MessagePipeline 可保留向后兼容）。

- ~~**组合 E（Provider 迁移）**~~：✅ **已完成**（MaiCoreDecisionProvider 已迁移到使用 HttpServer.register_route()，移除 aiohttp 内部 HTTP 服务器）。
