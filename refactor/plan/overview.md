# Amaidesu 重构实施计划总览

## 📋 实施原则

### 核心目标
1. **全面重构**：1-2天内完成，不考虑向后兼容
2. **消灭插件化**：核心功能全部模块化
3. **EventBus优先**：用事件系统替代服务注册
4. **Provider模式**：统一接口，工厂动态选择
5. **支持并发**：每层支持多个Provider同时处理
6. **决策层可替换**：MaiCore只是决策Provider的一种实现
7. **保留Git历史**：使用`git mv`迁移文件，避免丢失提交历史

### ⚠️ 重要：Git历史保留

**强制要求**：所有文件迁移必须使用`git mv`命令，**禁止使用文件系统直接移动文件**

**原因**：
- `git mv`会记录文件移动，Git可以追溯完整历史
- 直接移动文件会导致Git丢失该文件的提交历史
- 重构后的代码应该可以追溯到原始实现

**正确做法**：
```bash
# ✅ 正确：使用git mv
git mv src/plugins/minecraft src/extensions/minecraft
git commit -m "refactor: migrate minecraft to extension"

# 查看完整历史（包括移动）
git log --follow src/extensions/minecraft/
```

### 实施顺序

按照数据流顺序，从输入到输出逐步重构：
```
Phase 1 (基础设施) → Phase 2 (输入) → Phase 3 (决策+中间) → Phase 4 (输出) → Phase 5 (扩展) → Phase 6 (清理)
```

---

## 🗓️ 各阶段详细计划

### Phase 1: 基础设施搭建

**目标**：搭建重构的基础设施

**详细内容**：[Phase 1: 基础设施](./phase1_infrastructure.md)

**任务清单**：
- [x] Provider接口定义（公共API）
- [x] 决策Provider接口定义（新增）
- [x] Extension接口定义
- [x] ExtensionLoader实现
- [x] EventBus增强

**预期产出**：
- `src/core/provider.py`
- `src/core/decision_provider.py`
- `src/core/extension.py`
- `src/core/extension_loader.py`

---

### Phase 2: 输入层实现 (Layer 1-2)

**目标**：实现输入数据流的前两层

**详细内容**：[Phase 2: 输入层](./phase2_input.md)

**任务清单**：
- [x] Layer 1: 输入感知层 - 统一所有输入源接口
- [x] Layer 2: 输入标准化层 - 统一转换为Text格式

**预期产出**：
- `src/perception/` 目录及所有Provider
- `src/normalization/` 目录及所有Normalizer

**迁移清单**：
- [x] console_input → perception/text/console_input.py
- [x] bili_danmaku → perception/text/danmaku/
- [x] mock_danmaku → perception/text/danmaku/
- [x] stt → perception/audio/stt/

---

### Phase 3: 决策层 + Layer 3-4

**目标**：实现决策层和中间表示、表现理解层

**详细内容**：[Phase 3: 决策层](./phase3_decision.md)

**任务清单**：
- [x] 决策层实现（DecisionManager + DecisionProviders）
- [x] Layer 3: 中间表示层 - 统一消息格式
- [x] Layer 4: 表现理解层 - 解析MaiCore返回

**预期产出**：
- `src/core/decision_manager.py`
- `src/core/providers/` (MaiCore + LocalLLM + RuleEngine)
- `src/canonical/` 目录
- `src/understanding/` 目录

**迁移清单**：
- [x] llm_text_processor → understanding/response_parser.py
- [x] emotion_judge → understanding/emotion_judge.py

---

### Phase 4: 输出层实现 (Layer 5-6)

**目标**：实现输出数据流的后两层

**详细内容**：[Phase 4: 输出层](./phase4_output.md)

**任务清单**：
- [x] Layer 5: 表现生成层 - 生成渲染参数
- [x] Layer 6: 渲染呈现层 - 多Provider并发渲染

**预期产出**：
- `src/expression/` 目录
- `src/rendering/` 目录及所有Renderer

**迁移清单**：
- [x] tts → expression/tts_module.py + rendering/audio_renderer.py
- [x] subtitle → rendering/subtitle_renderer.py
- [x] vtube_studio → rendering/virtual_renderer.py

---

### Phase 5: 扩展系统实现

**目标**：实现扩展系统（Layer 8）

**详细内容**：[Phase 5: 扩展系统](./phase5_extensions.md)

**任务清单**：
- [x] ExtensionLoader实现（支持内置和用户扩展）
- [x] 迁移内置扩展（minecraft, warudo, dg_lab）
- [x] 配置系统更新
- [x] .gitignore配置

**预期产出**：
- `src/extensions/` 目录（内置扩展）
- `extensions/` 目录（用户扩展，.gitignore）

**迁移清单**（必须使用git mv）：
- [x] minecraft → extensions/minecraft
- [x] warudo → extensions/warudo
- [x] dg_lab_service → extensions/dg_lab
- [x] mainosaba → extensions/mainosaba

---

### Phase 6: 清理、测试和迁移

**目标**：清理旧代码，测试所有功能，验证完整性

**详细内容**：[Phase 6: 清理和测试](./phase6_cleanup.md)

**任务清单**：
- [x] 删除旧插件系统（PluginManager、plugins/目录）
- [x] 更新main.py以使用新架构
- [x] 测试所有功能
- [x] 验证Git历史完整性

**预期产出**：
- 简化后的 `main.py`
- 删除的 `src/plugins/` 目录
- 完整的测试覆盖

---

## ✅ 总体验证标准

### 技术指标
- ✅ 所有现有功能正常运行
- ✅ 配置文件行数减少40%以上
- ✅ 核心功能响应时间无增加
- ✅ 代码重复率降低30%以上
- ✅ 服务注册调用减少80%以上
- ✅ EventBus事件调用覆盖率90%以上
- ✅ 扩展系统正常加载内置扩展和用户扩展

### 架构指标
- ✅ 清晰的6层核心数据流架构
- ✅ 决策层可替换（支持多种DecisionProvider）
- ✅ 多Provider并发支持（输入层和输出层）
- ✅ 层级间依赖关系清晰（单向依赖）
- ✅ EventBus为内部主要通信模式
- ✅ Provider模式替代重复插件
- ✅ 工厂模式支持动态切换
- ✅ 扩展系统支持社区开发

---

## 🔗 相关文档

### 设计文档
- [设计总览](../design/overview.md)
- [6层架构设计](../design/layer_refactoring.md)
- [决策层设计](../design/decision_layer.md)
- [多Provider并发设计](../design/multi_provider.md)
- [扩展系统设计](../design/extension_system.md)
- [核心重构设计](../design/core_refactoring.md)

### 实施文档
- [Phase 1: 基础设施](./phase1_infrastructure.md)
- [Phase 2: 输入层](./phase2_input.md)
- [Phase 3: 决策层](./phase3_decision.md)
- [Phase 4: 输出层](./phase4_output.md)
- [Phase 5: 扩展系统](./phase5_extensions.md)
- [Phase 6: 清理和测试](./phase6_cleanup.md)

---

## 📝 提交策略

每个Phase完成后，创建独立提交：

```bash
# Phase 1
git add src/core/provider.py src/core/decision_provider.py src/core/extension.py src/core/extension_loader.py
git commit -m "feat(phase1): add provider interfaces and extension system"

# Phase 2
git add src/perception/ src/normalization/
git commit -m "feat(phase2): implement Layer 1-2 input perception and normalization"

# Phase 3
git add src/canonical/ src/understanding/ src/core/decision_manager.py src/core/providers/
git commit -m "feat(phase3): implement decision layer and Layer 3-4"

# Phase 4
git add src/expression/ src/rendering/
git commit -m "feat(phase4): implement Layer 5-6 output rendering"

# Phase 5
git add src/extensions/ extensions/.gitkeep
git commit -m "feat(phase5): implement extension system and migrate built-in extensions"

# Phase 6
git add main.py
git rm -r src/plugins/
git commit -m "refactor: remove plugin system and update main.py"
```

---

## 🎉 重构完成

所有Phase完成，架构重构结束！

**主要成果**：
1. ✅ 6层核心数据流架构
2. ✅ 可替换的决策层
3. ✅ 多Provider并发支持
4. ✅ Provider模式统一接口
5. ✅ 扩展系统支持社区开发
6. ✅ EventBus内部通信
7. ✅ 配置简化40%以上
8. ✅ Git历史完整保留
