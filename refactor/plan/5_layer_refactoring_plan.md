# 5层架构重构实施计划

> **创建时间**：2025-02-01
> **目标**：将7层架构重构为5层架构，提高结构化程度和智能化水平

---

## 📋 重构目标

### 核心变更
1. **合并Layer 2和Layer 3**：Normalization层输出NormalizedMessage（保留结构化数据）
2. **去掉UnderstandingLayer**：DecisionProvider直接返回Intent
3. **提高结构化程度**：StructuredContent类型化设计
4. **LLM意图解析**：用小LLM替代规则匹配
5. **层级命名简化**：单单词命名（Input、Normalization、Decision、Parameters、Rendering）

### 预期效果
- ✅ 消除信息丢失（保留原始结构化数据）
- ✅ 简化架构（7层 → 5层）
- ✅ 更智能的意图解析（LLM替代规则）
- ✅ 更清晰的层级职责

---

## 🚀 实施计划

### Phase 1: 数据结构重构

**目标**：创建新的数据结构

**任务列表**：
- [ ] 1.1 创建 `src/data_types/normalized_message.py`
  - 定义 `NormalizedMessage` 数据类
  - 包含 text, content, importance, metadata 字段
  - 实现 `to_message_base()` 方法

- [ ] 1.2 创建 `src/layers/normalization/content/` 目录
  - 创建 `base.py`：`StructuredContent` 抽象基类
  - 创建 `text_content.py`：`TextContent` 实现
  - 创建 `gift_content.py`：`GiftContent` 实现
  - 创建 `super_chat_content.py`：`SuperChatContent` 实现

- [ ] 1.3 创建 `src/layers/normalization/content/__init__.py`
  - 导出所有 StructuredContent 类型

**Git提交**：
```bash
git add src/data_types/normalized_message.py src/layers/normalization/content/
git commit -m "feat(data): 添加NormalizedMessage和StructuredContent数据结构

- 创建NormalizedMessage（合并NormalizedText + CanonicalMessage功能）
- 添加StructuredContent类型化设计（支持方法多态）
- 自动计算importance字段
- 保留原始结构化数据，不丢失信息"
```

---

### Phase 2: 层级目录重命名

**目标**：重命名层级目录，使用单单词命名

**任务列表**：
- [ ] 2.1 重命名 `src/layers/understanding/` → `src/layers/intent_analysis/`
  ```bash
  git mv src/layers/understanding src/layers/intent_analysis
  ```

- [ ] 2.2 重命名 `src/layers/expression/` → `src/layers/parameters/`
  ```bash
  git mv src/layers/expression src/layers/parameters
  ```

- [ ] 2.3 更新这些目录下的导入路径

**Git提交**：
```bash
git commit -m "refactor(layers): 重命名层级目录使用单单词命名

- understanding → intent_analysis
- expression → parameters
- 更新目录内所有导入路径"
```

---

### Phase 3: 创建Intent数据类

**目标**：定义Intent数据结构（DecisionProvider的输出）

**任务列表**：
- [ ] 3.1 创建 `src/layers/decision/intent.py`
  - 定义 `Intent` 数据类
  - 定义 `EmotionType` 枚举
  - 定义 `IntentAction` 数据类
  - 定义 `ActionType` 枚举

- [ ] 3.2 创建 `src/layers/decision/__init__.py`
  - 导出 Intent 相关类型

**Git提交**：
```bash
git add src/layers/decision/intent.py
git commit -m "feat(decision): 添加Intent数据结构

- 定义Intent作为DecisionProvider的输出格式
- 包含emotion、actions、response_text字段
- 定义EmotionType和ActionType枚举"
```

---

### Phase 4: 重构DecisionProvider接口

**目标**：更新DecisionProvider接口，返回Intent

**任务列表**：
- [ ] 4.1 更新 `src/core/base/decision_provider.py`
  - 输入从 `CanonicalMessage` 改为 `NormalizedMessage`
  - 输出从 `MessageBase` 改为 `Intent`

- [ ] 4.2 更新 `src/layers/decision/decision_manager.py`
  - 订阅 `normalization.message_ready` 事件（而不是 `canonical.message_ready`）
  - 更新类型注解

- [ ] 4.3 更新 `src/layers/decision/providers/` 下的所有Provider
  - 更新 `maicore_decision_provider.py`
  - 更新 `local_llm_decision_provider.py`
  - 更新 `rule_engine_decision_provider.py`

**Git提交**：
```bash
git commit -m "refactor(decision): 重构DecisionProvider接口返回Intent

- 输入：NormalizedMessage（代替CanonicalMessage）
- 输出：Intent（代替MessageBase）
- 更新所有DecisionProvider实现
- 去掉UnderstandingLayer的依赖"
```

---

### Phase 5: 实现LLM意图解析器

**目标**：创建IntentParser，使用小LLM解析MessageBase

**任务列表**：
- [ ] 5.1 创建 `src/layers/decision/intent_parser.py`
  - 实现 `IntentParser` 类
  - 定义系统prompt
  - 实现 `parse()` 方法：MessageBase → Intent
  - 添加降级逻辑（LLM失败时使用规则）

- [ ] 5.2 集成到 MaiCoreDecisionProvider
  - 在 `maicore_decision_provider.py` 中使用 `IntentParser`
  - 添加异步Future机制

**Git提交**：
```bash
git commit -m "feat(decision): 添加LLM意图解析器

- 创建IntentParser使用小LLM解析MessageBase → Intent
- 替代规则匹配，更智能、更灵活
- 成本可控：~$0.00025/次（Claude Haiku）
- 添加降级逻辑：LLM失败时使用规则"
```

---

### Phase 6: 更新main.py

**目标**：更新主程序，使用新的数据结构

**任务列表**：
- [ ] 6.1 更新导入
  - `NormalizedMessage` 路径
  - `Intent` 路径
  - 层级目录名称

- [ ] 6.2 更新组件初始化
  - DecisionManager 订阅正确的事件

**Git提交**：
```bash
git commit -m "refactor(main): 更新main.py使用新架构

- 更新所有导入路径
- DecisionManager订阅normalization.message_ready
- 适配5层架构"
```

---

### Phase 7: 清理旧文件

**目标**：删除不再需要的文件

**任务列表**：
- [ ] 7.1 删除 `src/layers/canonical/` 目录
  ```bash
  git rm -r src/layers/canonical
  ```

- [ ] 7.2 删除 `src/data_types/normalized_text.py`
  ```bash
  git rm src/data_types/normalized_text.py
  ```

- [ ] 7.3 删除 `src/layers/intent_analysis/response_parser.py`（旧的规则解析器）

- [ ] 7.4 更新所有其他文件的导入路径
  - 搜索并替换所有导入
  - 测试导入是否正常

**Git提交**：
```bash
git commit -m "refactor: 清理旧架构文件

- 删除canonical/目录（合并到normalization）
- 删除normalized_text.py（替换为normalized_message.py）
- 删除旧的response_parser.py（替换为intent_parser.py）
- 更新所有导入路径"
```

---

### Phase 8: 创建Pipeline系统框架

**目标**：创建3类Pipeline的基础框架

**任务列表**：
- [ ] 8.1 创建 `src/core/pipelines/base.py`
  - 定义 `PrePipeline` 协议
  - 定义 `PostPipeline` 协议
  - 定义 `RenderPipeline` 协议

- [ ] 8.2 创建 `src/core/pipelines/pre/` 目录
  - 创建示例：`rate_limit_pipeline.py`

- [ ] 8.3 更新 `PipelineManager`
  - 管理3类Pipeline
  - 按优先级处理

**Git提交**：
```bash
git commit -m "feat(pipeline): 创建3类Pipeline系统框架

- 定义PrePipeline、PostPipeline、RenderPipeline协议
- 创建示例PrePipeline
- 更新PipelineManager管理3类Pipeline"
```

---

### Phase 9: 更新文档

**目标**：更新README和其他文档

**任务列表**：
- [ ] 9.1 更新 `README.md`
  - 更新架构图
  - 更新快速开始指南

- [ ] 9.2 更新 `CLAUDE.md`
  - 更新架构描述
  - 更新目录结构

- [ ] 9.3 更新迁移指南（如果有）

**Git提交**：
```bash
git commit -m "docs: 更新文档反映5层架构

- 更新README.md架构图
- 更新CLAUDE.md架构描述
- 添加重构说明"
```

---

### Phase 10: 测试和验证

**目标**：确保所有功能正常工作

**任务列表**：
- [ ] 10.1 运行 `uv run python main.py --debug` 测试启动
- [ ] 10.2 测试输入流程
- [ ] 10.3 测试决策流程
- [ ] 10.4 测试输出流程
- [ ] 10.5 修复发现的问题

**Git提交**：
```bash
git commit -m "test: 修复重构后发现的问题

- 修复xxx
- 修复yyy"
```

---

## 📊 进度跟踪

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1 | ⏸️ 待开始 | 数据结构重构 |
| Phase 2 | ⏸️ 待开始 | 层级目录重命名 |
| Phase 3 | ⏸️ 待开始 | 创建Intent数据类 |
| Phase 4 | ⏸️ 待开始 | 重构DecisionProvider接口 |
| Phase 5 | ⏸️ 待开始 | 实现LLM意图解析器 |
| Phase 6 | ⏸️ 待开始 | 更新main.py |
| Phase 7 | ⏸️ 待开始 | 清理旧文件 |
| Phase 8 | ⏸️ 待开始 | 创建Pipeline系统框架 |
| Phase 9 | ⏸️ 待开始 | 更新文档 |
| Phase 10 | ⏸️ 待开始 | 测试和验证 |

---

## 🔑 关键原则

### Git历史保留
- ✅ 使用 `git mv` 移动文件（保留历史）
- ✅ 避免使用 `git rm --cached` 然后重新添加
- ✅ 尽量保持文件的可读性（便于git diff）

### 向后兼容
- ⚠️ 如果有外部依赖，需要提供适配器
- ⚠️ 保留旧的类型定义作为别名（过渡期）

### 测试驱动
- ✅ 每个Phase完成后测试启动
- ✅ 尽早发现问题，避免堆积

---

## 📝 备注

- 重构过程中如果发现问题，及时调整计划
- 每个Phase完成后更新进度表
- 记录重要的设计决策和遇到的问题
