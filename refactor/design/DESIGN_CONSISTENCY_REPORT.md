# refactor/design 设计文档一致性检查报告

**检查日期**: 2025-02-01  
**检查范围**: `refactor/design/` 下全部 10 个设计文档

---

## 一、总体结论

文档在**5 层架构**、**Provider 管理**、**移除插件系统**等主线上一致，但存在以下不一致，建议按本报告修正，使所有文档统一到**5 层架构 + 当前术语**。

---

## 二、发现的不一致项

### 1. 架构层数编号不统一

| 文档 | 表述 | 问题 |
|------|------|------|
| **overview.md** | 5 层（Layer 1-2 Input, 3 Decision, 4-5 Parameters+Rendering） | ✅ 基准 |
| **layer_refactoring.md** | 5 层 | ✅ 一致 |
| **multi_provider.md** | "Layer 1" 和 "**Layer 7**" | ❌ 应为 Layer 1 与 **Layer 5**（输出层） |
| **avatar_refactoring.md** | 开头“6 层架构”，正文“7 层架构”、Layer 5/6/7 | ❌ 应统一为 5 层表述（参数生成=Layer 4，渲染=Layer 5） |
| **event_data_contract.md** | 事件按 Layer 1-7 命名（含 understanding、expression、render） | ⚠️ 与 5 层命名不完全对应，但事件名可保留以兼容 |
| **pipeline_refactoring.md** | 文末相关文档写“**7 层**架构设计” | ❌ 应改为“5 层架构设计” |
| **core_refactoring.md** | 未强调层数 | - |

**建议**：以 **overview.md** 的 5 层为准，其余文档中“7 层”“6 层”均改为 5 层，且 multi_provider 中“Layer 7”改为“Layer 5”。

---

### 2. Pipeline 与 overview 冲突

- **overview.md**：  
  - “**简化 Pipeline**：TextPipeline **集成到 InputLayer**”  
  - “**不再单独的 Pre-Pipeline 层**”
- **layer_refactoring.md**：  
  - 仍有独立 **Pre-Pipeline**（Layer 2 之后）、**Post-Pipeline**（Layer 3 之后）  
  - 目录中有 `core/pipelines/` 下 pre/post/render 三类

**建议**：二选一统一表述：  
- **方案 A**：采用 overview 的“TextPipeline 集成到 InputLayer、无单独 Pre-Pipeline 层”，则 layer_refactoring 中删除“Pre-Pipeline”“Post-Pipeline”作为独立层的描述，改为“在 InputLayer 内部的文本处理/可选后处理”。  
- **方案 B**：若实现上仍保留 Pre/Post Pipeline，则修改 overview，改为“保留 Pre-Pipeline（在 Layer 2 之后）、可选 Post-Pipeline”，与 layer_refactoring 一致。

当前 **pipeline_refactoring.md** 实现状态写的是“TextPipeline 已接入 Layer 2→3”，与 overview 的“集成到 InputLayer”更接近，建议采用**方案 A**，在 layer_refactoring 中弱化/移除“独立 Pre-Pipeline 层”的写法。

---

### 3. 决策层输入/输出类型不一致

- **decision_layer.md**、**overview.md**：  
  - 输入：**NormalizedMessage**  
  - 输出：**Intent**
- **core_refactoring.md**：  
  - `decide(canonical_message: **CanonicalMessage**)` → 返回未写，但上下文像 MessageBase  
  - DecisionManager 代码示例用 **CanonicalMessage**
- **http_server.md**：  
  - `decide(canonical_message: **CanonicalMessage**) -> **MessageBase**`

**建议**：全库统一为 **NormalizedMessage → Intent**。在 core_refactoring.md、http_server.md 中，将 `CanonicalMessage` 改为 `NormalizedMessage`，返回类型改为 `Intent`（若仍写返回值）。

---

### 4. 目录与配置术语不统一

| 文档 | 输入层目录/配置 | 输出层目录/配置 |
|------|-----------------|-----------------|
| **overview.md** | `layers/input/`，配置 `[input]`、`[input.providers.xxx]` | `layers/output/`（渲染在 layers 下），`[output]` |
| **multi_provider.md** | `src/perception/`，配置 `[perception]`、`[perception.inputs.xxx]` | `src/rendering/`，`[rendering]`、`[rendering.outputs.xxx]` |
| **layer_refactoring.md** | `layers/input/` | `layers/rendering/` |

**建议**：以 **overview.md** 为准：  
- 目录：`layers/input/`、`layers/decision/`、`layers/parameters/`、`layers/output/`（或 `layers/rendering/` 二选一，但需与 overview 一致）。  
- 配置：`[input]`、`[decision]`、`[output]`。  
在 **multi_provider.md** 中把 perception/rendering 改为 input/output（或明确标注“旧称 perception/rendering，现统一为 input/output”）。

---

### 5. 事件名称与 5 层对应关系

- **event_data_contract.md**：  
  - 使用 Layer 4=决策、Layer 5=表现理解、Layer 6=表现生成、Layer 7=渲染。  
  - 事件：`decision.*`、`understanding.intent_generated`、`expression.parameters_generated`、`render.*`
- **overview.md**：  
  - 5 层中“表现理解”已并入决策层（Intent 由 DecisionProvider 输出），无独立 Understanding 层；Layer 4=参数生成，Layer 5=渲染。

**建议**：  
- 事件名可保留（兼容已有实现），但在 event_data_contract 中加一句说明：“事件命名沿袭历史层级编号，与当前 5 层架构对应关系见 overview。”  
- 或在 EventRegistry 注释中写明：decision.* → Layer 3；expression.* → Layer 4；render.* → Layer 5。

---

### 6. Avatar 文档的层数与目录

- **avatar_refactoring.md**：  
  - “6 层架构”“7 层架构”混用；  
  - Layer 5=Understanding、Layer 6=Expression、Layer 7=Rendering；  
  - 目录 `understanding/`、`expression/`、`rendering/`、`platform/`。

**建议**：  
- 统一为 5 层：Layer 3 决策（含意图/情感）、Layer 4 参数生成（ExpressionMapper）、Layer 5 渲染（AvatarOutputProvider）。  
- 文中“Layer 5/6/7”改为“Layer 3/4/5”或“表现理解/参数生成/渲染”并注明对应 5 层的哪一层。  
- 相关文档链接“7 层架构设计”改为“5 层架构设计”。

---

### 7. 已移除/不存在的文档引用

- **overview.md**、**layer_refactoring.md** 提到“已移除的文档”：  
  - `plugin_system.md`、`architecture_review.md`  
若这些文件已删除，引用保留为“已移除”即可；若仍存在，建议移动到 `refactor/design/archive/` 或删除，避免与“已移除”矛盾。

---

## 三、建议修正优先级

| 优先级 | 项 | 建议操作 |
|--------|----|----------|
| P0 | 决策层类型 | core_refactoring、http_server 中 CanonicalMessage/MessageBase → NormalizedMessage/Intent |
| P0 | 层数编号 | multi_provider 中 Layer 7 → Layer 5；avatar、pipeline 中 7 层 → 5 层 |
| P1 | Pipeline 表述 | overview 与 layer_refactoring 二选一统一（建议采用 overview 的“集成到 InputLayer”） |
| P1 | 目录/配置 | multi_provider 中 perception/rendering 改为与 overview 一致的 input/output 或加注释 |
| P2 | 事件契约 | event_data_contract 中补充与 5 层架构的对应说明 |
| P2 | Avatar | avatar_refactoring 层号与 5 层对齐，相关链接改为 5 层架构设计 |

---

## 四、文档间交叉引用检查

- 各文档末尾“相关文档”链接经核对，除上述层数/命名外，链接目标均存在且正确。  
- 建议在 **overview.md** 的“文档结构”中明确写一句：“以下文档均以**5 层架构**为基准，层号与本文一致。”

---

**报告结束。** 按上表 P0/P1 逐项修改后，再跑一次全文搜索“7 层”“Layer 7”“CanonicalMessage”“MessageBase（决策层）”“perception”“rendering（配置节）”即可做收尾验证。
