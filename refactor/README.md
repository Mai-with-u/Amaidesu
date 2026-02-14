# Amaidesu 架构重构文档索引

> **版本**: v3.0
> **日期**: 2026-02-01
> **状态**: 插件系统已移除，采用纯Provider架构

---

## ⚠️ 架构说明

> **重要**: 当前架构已稳定运行，采用3域架构设计

| 架构版本 | 说明 |
|----------|------|
| v3.0 | 3域架构（Input → Decision → Output） |
| 移除内容 | 7层/5层架构已废弃 |

---

## 📋 快速导航

### 我想了解...

**整体架构是什么？**
→ [设计总览](./design/overview.md)

**3域架构如何工作？**
→ [设计总览](./design/overview.md)

**决策层如何可替换？**
→ [决策层设计](./design/decision_layer.md)

**多个Provider如何并发？**
→ [多Provider并发设计](./design/multi_provider.md)

**配置系统如何重构？**
→ [配置系统设计](./design/config_system.md)

**提示词管理如何实现？**
→ [提示词管理设计](./design/prompt_management.md)

**LLM客户端如何管理？**
→ [LLM服务设计](./design/llm_manager.md)

---

## 📁 文档结构

```
refactor/
├── README.md                            # 本文件 - 文档索引
│
├── design/                              # 设计文档
│   ├── overview.md                       # 架构总览（3域架构）
│   ├── decision_layer.md                 # 决策层设计
│   ├── multi_provider.md                 # 多Provider并发设计
│   ├── config_system.md                 # 配置系统设计
│   ├── prompt_management.md             # 提示词管理设计
│   ├── llm_manager.md                   # LLM服务设计
│   ├── event_data_contract.md            # 事件数据契约设计
│   └── pipeline_refactoring.md           # Pipeline重新设计
```

> **注意**: 旧的设计文档（core_refactoring.md, http_server.md, avatar_refactoring.md, plugin_system.md, 5_layer_refactoring_plan.md）已移至 plugins_backup/ 目录作为历史参考。

---

## 🎯 重构核心要点

### 1. 3域架构数据流（当前架构）

```
Input Domain（数据采集 + 标准化）
    ↓ NormalizedMessage
Decision Domain（决策，可替换）
    ↓ Intent
Output Domain（参数生成 + 渲染）
    ↓ 实际输出
```

### 2. 核心变化

| 变化 | 旧架构 | 新架构（3域） |
|------|-------------|-------------|
| **架构类型** | 7层/5层分层架构 | 3域架构 |
| **Input** | Layer 1-2 (Input + Normalization) | Input Domain（包含标准化） |
| **Decision** | Layer 3 (Decision) 或 Layer 4 | Decision Domain |
| **Output** | Layer 5-7 (Parameters + Rendering) | Output Domain（包含参数生成） |
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
| **InputProvider** | Input Domain | 接收外部数据，生成RawData | ConsoleInputProvider, BiliDanmakuProvider |
| **DecisionProvider** | Decision Domain | 处理NormalizedMessage，决策并返回Intent | MaiCoreDecisionProvider, LocalLLMDecisionProvider |
| **OutputProvider** | Output Domain | 接收渲染参数，执行实际输出 | TTSProvider, SubtitleProvider, VTSProvider |

### Manager（管理者）

- **InputProviderManager**：管理输入Provider的生命周期
- **DecisionManager**：管理决策Provider，支持运行时切换
- **OutputProviderManager**：管理输出Provider的生命周期

### 配置驱动

```toml
# 输入Provider配置
[providers.input]
enabled_inputs = ["console", "bili_danmaku"]

[providers.input.providers.console]
source = "stdin"

# 决策Provider配置
[providers.decision]
active_provider = "maicore"

# 输出Provider配置
[providers.output]
enabled_outputs = ["tts", "subtitle", "vts"]
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

### v3.0（2026年2月，当前分支 refactor）

- **移除插件系统**：所有功能迁移到 Provider 架构
- Provider由Manager统一管理，配置驱动启用/禁用
- 3域架构（Input → Decision → Output），职责清晰
- 新增多个核心模块：events, config, context, llm, logging, prompts, streaming, tts, types, registry
- Input Pipeline 系统重构，支持 MessagePipeline
- Output 引入 Pipeline 机制，Intent 统一经 OutputPipeline 分发

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
- ✅ 清晰的3域架构数据流
- ✅ 决策层可替换（支持多种DecisionProvider）
- ✅ 多Provider并发支持（输入域和输出域）
- ✅ 域间依赖关系清晰（单向依赖）
- ✅ EventBus为内部主要通信模式
- ✅ Provider模式替代重复插件
- ✅ 配置驱动，无需修改代码即可启用/禁用Provider
- ✅ 插件系统已完全移除

---

## 🔗 相关资源

### 设计文档
- [设计总览](./design/overview.md) - 3域架构总览
- [决策层设计](./design/decision_layer.md) - 可替换的决策Provider系统
- [多Provider并发设计](./design/multi_provider.md) - Provider管理架构
- [配置系统设计](./design/config_system.md) - 配置管理架构
- [提示词管理设计](./design/prompt_management.md) - PromptManager 设计

### 文档目录
- [docs/](../docs/) - 项目文档（包含模块文档、API参考、开发指南）
- [plugins_backup/](../plugins_backup/) - 旧插件系统备份和迁移文档

---

## ❓ 常见问题

### Q: 为什么要从7层/5层改为3域？

**A**: 简化架构，消除冗余：
- Normalization与Input强耦合，合并到Input Domain
- Parameters与Output强耦合，合并到Output Domain
- 减少数据转换开销，提高性能
- 按业务功能组织，而非按技术分层

### Q: 插件系统为什么要移除？

**A**: 插件系统与"消灭插件化"的重构目标不兼容：
- Plugin在创建Provider，违背了设计原则
- 增加了一层不必要的抽象
- 纯Provider架构更简单、更清晰

详见：[插件系统移除说明](./PLUGIN_SYSTEM_REMOVAL.md)

### Q: 社区开发者如何扩展功能？

**A**: 直接添加Provider：

1. 在对应域创建Provider文件：`src/domains/{domain}/providers/my_provider.py`
2. 在配置中启用：`[providers.input]enabled_inputs = ["console", "my_provider"]`
3. 无需创建Plugin

详见：[设计总览 - 社区扩展](./design/overview.md#社区扩展)

---

## 📝 当前分支变更（相对于 dev 分支）

当前 refactor 分支相对于 dev 分支的主要架构变更：

### 1. 目录结构重构
- **删除**：`src/core/` 目录（旧的核心模块）
- **新增**：`src/domains/` 目录（3域架构）
  - `src/domains/input/` - 输入域
  - `src/domains/decision/` - 决策域
  - `src/domains/output/` - 输出域

### 2. 插件系统移除
- **删除**：所有 `src/plugins/` 目录下的插件（30+ 个）
- **新增**：Provider 架构，所有功能迁移到 `src/domains/{domain}/providers/`

### 3. 新增核心模块（src/modules/）
| 模块 | 功能 |
|------|------|
| `events/` | EventBus 事件系统 |
| `config/` | 配置管理（ConfigService, Schema 验证） |
| `context/` | 对话上下文管理 |
| `llm/` | LLM 客户端管理 |
| `logging/` | 统一日志系统 |
| `prompts/` | 提示词管理（PromptManager） |
| `streaming/` | 音频流传输 |
| `tts/` | TTS 服务管理 |
| `types/` | 共享类型定义（Intent, EmotionType, ActionType） |
| `registry.py` | Provider 注册表 |

### 4. Provider 系统
- Input Provider: 10 个（console, bili_danmaku, bili_danmaku_official, bili_danmaku_official_maicraft, mainosaba, mock_danmaku, read_pingmu, remote_stream, stt）
- Decision Provider: 3 个（llm, maicore, maicraft）
- Output Provider: 11 个（edge_tts, gptsovits, omni_tts, vts, warudo, vrchat, subtitle, sticker, obs_control, remote_stream, mock）

### 5. Pipeline 系统
- Input Pipeline: rate_limit, similar_filter
- Output Pipeline: profanity_filter

### 6. 配置系统
- 从旧配置系统迁移到 Pydantic Schema 验证
- 支持配置文件自动生成
- 配置覆盖机制

---

**最后更新**：2026年2月14日
**维护者**：Amaidesu Team
