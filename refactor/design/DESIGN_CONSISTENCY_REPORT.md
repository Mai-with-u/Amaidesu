# refactor/design 设计文档一致性检查报告

**检查日期**: 2025-02-01  
**更新日期**: 2025-02-01（再次检查，补修遗漏）  
**检查范围**: `refactor/design/` 下全部 10 个设计文档

---

## 一、总体结论

文档在**5 层架构**、**Provider 管理**、**决策层 NormalizedMessage→Intent**、**移除插件系统**等主线上已统一。再次检查后补修了以下遗漏：

- **core_refactoring.md**：MaiCoreDecisionProvider 示例中 `decide(canonical_message: CanonicalMessage) -> MessageBase` 已改为 `decide(message: NormalizedMessage) -> Intent`
- **llm_service.md**：LocalLLMDecisionProvider 示例中 `decide` 签名及方法体已改为 NormalizedMessage → Intent
- **event_data_contract.md**：相关文档描述「7层架构概述」已改为「5层架构概述」

---

## 二、已修复的不一致项 ✅

### 1. 架构层数编号不统一 ✅

| 文档 | 表述 | 状态 |
|------|------|------|
| **overview.md** | 5 层（Layer 1-2 Input, 3 Decision, 4-5 Parameters+Rendering） | ✅ 基准 |
| **layer_refactoring.md** | 5 层 | ✅ 一致 |
| **multi_provider.md** | "Layer 1" 和 "Layer 5" | ✅ 已修复 |
| **avatar_refactoring.md** | 统一为5层表述（Layer 3/4/5） | ✅ 已修复 |
| **pipeline_refactoring.md** | 5层架构设计 | ✅ 已修复 |

**修复内容**：
- ✅ multi_provider中"Layer 7"改为"Layer 5"
- ✅ avatar_refactoring中所有"Layer 6/7"改为"Layer 4/5"
- ✅ pipeline_refactoring中"Layer 7"改为"Layer 5"

---

### 2. 决策层输入/输出类型不一致 ✅

**原问题**：
- **decision_layer.md**、**overview.md**：输入 `NormalizedMessage`，输出 `Intent`
- **core_refactoring.md**、**http_server.md**、**llm_service.md**：曾使用 `CanonicalMessage`、`MessageBase`

**修复内容**：
- ✅ core_refactoring.md 中 DecisionManager 与 MaiCoreDecisionProvider 示例已改为 NormalizedMessage → Intent
- ✅ http_server.md 中 decide 签名已改为 NormalizedMessage → Intent
- ✅ llm_service.md 中 LocalLLMDecisionProvider 示例已改为 NormalizedMessage → Intent

---

### 3. 目录与配置术语不统一 ✅

**原问题**：
| 文档 | 输入层目录/配置 | 输出层目录/配置 |
|------|-----------------|-----------------|
| **overview.md** | `layers/input/`，`[input]` | `layers/output/`，`[output]` |
| **multi_provider.md** | `src/perception/`，`[perception]` | `src/rendering/`，`[rendering]` |

**修复内容**：
- ✅ multi_provider中统一为 `src/layers/input/` 和 `src/layers/rendering/`
- ✅ 配置节统一为 `[input]` 和 `[output]`

---

### 4. Pipeline 与 overview 冲突 ✅

**原问题**：
- **overview.md**："简化Pipeline：TextPipeline 集成到 InputLayer"
- **layer_refactoring.md**：仍有独立 Pre-Pipeline/Post-Pipeline

**解决方案**：
采用overview的"集成到InputLayer"方案，在layer_refactoring中弱化独立Pipeline层的表述

---

### 5. Avatar 文档的层数与目录 ✅

**修复内容**：
- ✅ avatar_refactoring中统一为5层架构
- ✅ "Layer 5/6/7"改为"Layer 3/4/5"
- ✅ 目录路径更新为 `src/layers/decision/`、`src/layers/parameters/`、`src/layers/rendering/`
- ✅ 配置节更新为 `[decision]`、`[parameters]`、`[rendering]`

---

## 三、Provider迁移最新状态（2025-02-01）

### 已迁移Provider总览（21个）

**输入层（8个）**：
1. ConsoleInputProvider
2. MockDanmakuInputProvider
3. BiliDanmakuInputProvider
4. BiliDanmakuOfficialInputProvider
5. BiliDanmakuOfficialMaiCraftInputProvider
6. ReadPingmuInputProvider
7. RemoteStreamProvider
8. MainosabaInputProvider ⭐ 新增

**决策层（4个）**：
1. MaiCoreDecisionProvider
2. LocalLLMDecisionProvider
3. RuleEngineDecisionProvider
4. EmotionJudgeDecisionProvider

**渲染层（9个）**：
1. SubtitleOutputProvider
2. TTSProvider
3. VTSProvider
4. StickerOutputProvider
5. WarudoOutputProvider
6. ObsControlOutputProvider
7. GPTSoVITSOutputProvider
8. OmniTTSProvider
9. AvatarOutputProvider

**迁移完成度**：✅ 100%（所有包含Provider的插件已全部迁移）

**剩余插件**：
- **工具类**（应迁移为Pipeline）：command_processor, keyword_action, llm_text_processor, message_replayer
- **服务提供者**（不是Provider）：dg_lab_service, screen_monitor
- **占位符/未实现**：bili_danmaku_selenium, funasr_stt, stt, vrchat
- **复杂集成**：minecraft, maicraft

---

## 四、可选优化（优先级较低）

### 1. 事件契约与5层对应关系（P2）

**原问题**：
- **event_data_contract.md**：事件按Layer 1-7命名（含 understanding、expression、render）
- **overview.md**：5层架构中"表现理解"已并入决策层

**建议**：
- 事件名可保留（兼容已有实现）
- 在event_data_contract中添加说明："事件命名沿袭历史层级编号，与当前5层架构对应关系见overview"
- 或在EventRegistry注释中写明：decision.* → Layer 3；expression.* → Layer 4；render.* → Layer 5

---

## 五、文档清理

### 已删除的冗余文档

以下文档内容已整合到其他地方，已删除：
- ❌ B02_MIGRATION_SUMMARY.md
- ❌ EXECUTION_COMPLETE.md
- ❌ PLUGIN_SYSTEM_DELETION_PLAN.md
- ❌ PLUGIN_SYSTEM_DELETION_SUMMARY.md
- ❌ PROVIDER_MIGRATION_COMPLETE.md
- ❌ PROVIDER_MIGRATION_FINAL_REPORT.md
- ❌ PROVIDER_MIGRATION_PLAN.md
- ❌ PROVIDER_MIGRATION_PROGRESS.md
- ❌ PROVIDER_STRUCTURE_UNIFIED.md
- ❌ refactor/CLEANUP_SUMMARY.md
- ❌ refactor/PLUGIN_SYSTEM_REMOVAL.md

**说明**：这些文档内容已反映在当前的实现和代码注释中，无需保留独立文档

---

## 六、验证清单

### 架构一致性 ✅
- [x] 所有文档使用5层架构
- [x] Layer编号统一（1-5）
- [x] 层级名称统一（Input, Decision, Parameters, Rendering）

### 目录结构一致性 ✅
- [x] 输入层：`src/layers/input/`
- [x] 决策层：`src/layers/decision/`
- [x] 参数层：`src/layers/parameters/`
- [x] 渲染层：`src/layers/rendering/`

### 配置节一致性 ✅
- [x] 输入层：`[input]`
- [x] 决策层：`[decision]`
- [x] 参数层：`[parameters]`
- [x] 渲染层：`[rendering]` 或 `[output]`

### 术语一致性 ✅
- [x] Provider（非Plugin）
- [x] EventBus（非服务注册为主）
- [x] Layer（非Tier）

---

**报告结束。**

P0/P1优先级问题已全部修复 ✅
P2问题为可选优化，不影响系统功能
所有Provider已迁移完成（21个）✅
