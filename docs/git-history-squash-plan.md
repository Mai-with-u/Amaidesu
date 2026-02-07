# Git 历史重组计划文档

> **注意**: 这是历史记录文档，描述了从旧架构到新架构的演进过程。
> **当前架构**: 项目已采用3域架构（Input → Decision → Output），旧的7层/5层架构已废弃。
> 详见：[refactor/design/overview.md](../refactor/design/overview.md)

---

## 📋 概述

**目标**: 将 refactor 分支的 163 个 commit 合并成 6-8 个语义化阶段，创建一个干净的 `refactor-clean` 分支用于合并到 dev。

**策略**:
- `refactor` 分支 → 继续按细粒度提交（保留详细历史）
- `refactor-clean` 分支 → 定期合并为几个大 commit（用于合并到 dev）
- `dev` 分支 → 干净的历史记录

---

## 🎯 合并后的目标结构（6个阶段）

```
[阶段1] Phase 1-6: 5层架构重构与Extension系统
[阶段2] 插件系统到Plugin架构迁移与清理
[阶段3] Provider提取与测试重组
[阶段4] Provider统一与插件系统移除
[阶段5] 架构改进与遗留问题修复
[阶段6] 3域架构实施与文档更新
```

---

## 📦 详细分组方案

### 阶段 1: Phase 1-6 - 5层架构重构与Extension系统

**Commit 范围**: feaa4d4^..fd3a687（最早的20个commit）
**合并数量**: 20 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
fd3a687 refactor: implement Phase 1 infrastructure layer with comprehensive testing
b9d3fe9 refactor: complete Phase 2 implementation for input layer
6502378 refactor: 完成 Phase 3 决策层重构
7bbf749 refactor: 完成 Phase 4 Output Layer 重构
487b1a0 refactor: Phase 4 输出层集成完成
545a9e9 refactor: Phase 5 第一阶段 - Extension系统基础实现
5c8adb7 refactor: Phase 5-6重构完成 - Extension系统基础 + 清理和测试
3e540d0 refactor: Phase 4 输出层集成完成
f360008 docs: 更新技术债文档 - Phase 4 输出层集成完成
196ec53 refactor: 静态代码评审和技术债更新
e8c9aa0 docs: add Phase 5 second stage progress and migration plan
c7793f8 refactor: migrate sticker plugin to extension system
1002701 refactor: migrate bili_danmaku plugin to extension system
67776b7 docs: update Phase 5 progress - 12/23 plugins migrated (52.2%)
d345f78 docs: record plugins without plugin.py files
9e1e522 refactor: migrate complex plugins and bili_danmaku series to extension system
287e9f0 docs: Phase 5 最终完成 - 21/21 plugins migrated (100%)
7e85394 refactor: simplify AmaidesuCore from 599 to 464 lines (-22.5%)
8312386 refactor: Phase 6 代码清理和静态评审完成
2d775c3 docs: add Git历史修复方案和自动化脚本
0432957 docs: 更新Git历史修复方案，添加Plugin命名统一
824fc21 docs: 添加修复相关文档索引
```

**合并后的 commit message**:
```
refactor(architecture): Phase 1-6 完成5层架构重构与Extension系统

## 核心变更
- 实现6层架构基础设施（Phase 1）
- 完成输入层、决策层、输出层重构（Phase 2-4）
- 建立Extension系统（Phase 5-6）
- 迁移21个插件到新架构（100%完成）

## 技术改进
- AmaidesuCore简化 599→464行 (-22.5%)
- 建立清晰的层级数据流
- 完成静态代码评审和技术债清理

## 文档
- 更新技术债文档
- 添加Git历史修复方案
- 完成Phase 5-6最终验证
```

---

### 阶段 2: 插件系统到Plugin架构迁移与清理

**Commit 范围**: c1f8c04..df3f13c（约30个commit）
**合并数量**: 30 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
df3f13c refactor: 完成插件系统重构 Phase 6 - 清理和最终验证
f3b6cc6 refactor: 移除Extension系统，统一为Plugin架构
3267fd6 refactor: 删除残留的extensions/__init__.py文件
c3914f9 refactor: simplify AmaidesuCore from 464 to 364 lines (-277 lines, -43.1%)
2c592a0 refactor: 修复4个高优先级代码质量问题
8ae4cf1 refactor: 移除未使用的DataCache相关代码
0ed6509 refactor: 迁移gptsovits_tts插件到新Plugin架构
2d9cb7f refactor: 完成gptsovits_tts插件迁移
bbe74c6 refactor: 完成gptsovits_tts插件迁移和验证
46c8db4 refactor(phase6): complete Phase 6 - cleanup and testing
f827d62 fix: 删除已废弃的测试并修复 API 变更
ea28d6c docs: 清理 refactor/ 目录下的过时文档
ea4adb5 feat: 添加新协作者入门指南和更新.gitignore
a5ac921 refactor: complete Layer 2-3 bridge, enable input data flow
da162a1 refactor: implement TextPipeline and process_text for Layer 2-3 preprocessing
bf9b188 refactor: implement FastAPI HttpServer with AmaidesuCore integration
77f38a7 refactor: migrate MaiCoreDecisionProvider to use HttpServer.register_route
fe29bdf refactor: add TextPipeline examples (RateLimitTextPipeline, SimilarTextFilterPipeline)
ffe92c8 refactor: optimize service calls with cached references in TTS providers
b458d96 refactor: enhance EventBus with type-safe event data contracts
ae99bb8 refactor: 项目统一为uv管理依赖
0a7d6cf chore: update dependencies and clean up imports
3349599 refactor: 重构 AvatarManager 使用新的 LLMService
3a2b97f refactor: Avatar 系统重构到 6 层架构
6301f07 refactor: 补充 Avatar 系统重构文件
4d76d07 refactor: 清理 .vscode/settings.json 符号链接
27325b2 refactor: 完成 LLM 系统迁移并清理旧系统
a1f4959 docs: 更新 REFACTOR_OPTIMIZATION_ANALYSIS.md - 标记 LLM 系统迁移已完成
e98dcae refactor: complete optimization items - EmotionAnalyzer, Provider, docs
30a6d44 docs: 更新文档中 LLMClientManager 旧命名为 LLMService
```

**合并后的 commit message**:
```
refactor(plugins): 完成插件系统到Plugin架构迁移与清理

## 核心变更
- 移除Extension系统，统一为Plugin架构
- 迁移所有插件到新Plugin架构
- 完成gptsovits_tts等复杂插件迁移

## 架构优化
- AmaidesuCore简化 464→364行 (-43.1%)
- 统一为uv管理依赖
- 移除DataCache等未使用代码

## 功能增强
- 实现FastAPI HttpServer集成
- 添加TextPipeline示例（限流、相似文本过滤）
- 优化TTS providers服务调用缓存
- 增强EventBus类型安全

## 其他
- Avatar系统重构到6层架构
- LLM系统迁移完成
- 清理过时文档和测试
```

---

### 阶段 3: Provider提取与测试重组

**Commit 范围**: a0e33a8..f977d89（约15个commit）
**合并数量**: 15 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
f977d89 refactor(tests): 重新组织测试目录结构
dc6b984 chore(tests): 清理所有过时的测试文件
fb5b787 docs(tests): 更新测试README标记清理完成
7739f61 feat(providers): 提取 BiliDanmakuInputProvider 并添加测试
2c31aae feat(providers): 提取 EmotionJudgeDecisionProvider 并添加测试
779a5d4 feat(providers): 提取 StickerOutputProvider 并添加测试
a5f6d0c refactor(providers): 修正Provider目录组织，移动到各层providers目录
2b89f47 feat(providers): 提取 BiliDanmakuOfficialInputProvider 并添加测试
a0e33a8 feat(plugins): 创建MockProviders模拟插件
```

**合并后的 commit message**:
```
refactor(tests): Provider提取与测试重组

## 核心变更
- 重新组织测试目录结构
- 清理所有过时的测试文件
- 修正Provider目录组织

## Provider提取
- 提取 BiliDanmakuInputProvider 并添加测试
- 提取 EmotionJudgeDecisionProvider 并添加测试
- 提取 StickerOutputProvider 并添加测试
- 提取 BiliDanmakuOfficialInputProvider 并添加测试

## 测试完善
- 创建MockProviders模拟插件
- 更新测试README标记清理完成
```

---

### 阶段 4: Provider统一与插件系统移除

**Commit 范围**: dd21194..ec56910（约10个commit）
**合并数量**: 10 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
dd21194 refactor: 移除插件系统并迁移Provider到新架构
ec56910 feat(providers): 迁移6个核心Provider到新架构
1c11159 feat(layers): 迁移2个输入Provider并删除intent_analysis层
e8fe9cb refactor(providers): 统一Provider目录结构
8e2de5e feat(providers): 迁移BiliDanmakuOfficialMaiCraftInputProvider
3da26c2 refactor(providers): 重命名bili_official为bili_danmaku_official
e97acfe refactor(providers): 重命名bili_official_maicraft为bili_danmaku_official_maicraft
363b8da feat(providers): 提取MainosabaInputProvider
d9d4a69 docs(design): 修复设计文档中的架构一致性问题
89ad57f docs(design): 更新一致性报告，标记已修复问题
```

**合并后的 commit message**:
```
refactor(providers): Provider统一与插件系统移除

## 核心变更
- 移除插件系统（plugin.py, plugin_manager.py, plugins/目录）
- 迁移核心Provider到新架构
- 统一Provider目录结构

## Provider迁移
- 迁移6个核心Provider到新架构
- 迁移2个输入Provider并删除intent_analysis层
- 提取MainosabaInputProvider
- 迁移BiliDanmakuOfficialMaiCraftInputProvider

## 命名统一
- 重命名bili_official → bili_danmaku_official
- 重命名bili_official_maicraft → bili_danmaku_official_maicraft

## 文档更新
- 修复设计文档中的架构一致性问题
- 更新一致性报告，标记已修复问题
- 更新AGENTS文档
```

---

### 阶段 5: 架构改进与遗留问题修复

**Commit 范围**: d40bd98..db03766（约20个commit）
**合并数量**: 20 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
db03766 fix(decision): 修复Provider切换死锁并完善测试套件
6760e0f refactor(agents): 更新AGENTS文档和测试脚本以反映Provider架构变更
57d33e1 chore: 撤销对之前已有插件的修改以便测试参考
7289374 refactor(cleanup): 添加旧架构清理清单并删除过时设计文档
a438943 feat(config): 新增配置系统设计文档，详细描述Pydantic Schema及三级配置合并流程
137d8e8 refactor(docs): 更新文档以反映架构重组和Provider路径变更
0639fa1 refactor(config): 实施Schema-as-Template配置系统架构
c1bc784 refactor(architecture): 实施3域架构设计，全面重构项目结构
efe2d19 fix(tests): 修复测试文件中的导入路径问题
5c9481d docs: 清理过时文档
8781d9c docs: 统一架构术语为3域架构，移除5层/7层遗留
d40bd98 docs: 清理文档
968f83d refactor(cleanup): 完成遗留问题清理，提升代码一致性
7839b88 refactor(architecture): 实施架构改进建议并修复测试bug
22e07b4 refactor(core): 完成架构问题修复，移除插件系统残余
d979222 refactor(core): 完成P1遗留问题修复，清理插件系统残余
33a7c08 refactor(providers): 完成 Provider 自动注册迁移
4d128e6 refactor(providers): 统一 Provider 工厂模式，更新文档
5a622a8 test(e2e): 添加端到端测试用例
```

**合并后的 commit message**:
```
refactor(architecture): 架构改进与遗留问题修复

## 核心变更
- 实施3域架构设计（Input/Decision/Output）
- 实施Schema-as-Template配置系统架构
- 完成架构改进建议和测试bug修复

## Provider优化
- 完成Provider自动注册迁移
- 统一Provider工厂模式
- 修复Provider切换死锁

## 遗留问题修复
- 完成P1遗留问题修复，清理插件系统残余
- 完成架构问题修复，移除插件系统残余
- 完成遗留问题清理，提升代码一致性

## 测试与文档
- 添加端到端测试用例
- 修复测试文件中的导入路径问题
- 新增配置系统设计文档
- 清理过时文档
- 统一架构术语为3域架构
```

---

### 阶段 6: 3域架构实施与最终文档更新

**Commit 范围**: a5eefad（最新的4个commit）
**合并数量**: 4 个 commit → 1 个 commit

**包含的 commit**（按时间倒序）:
```
a5eefad chore: 测试目录的结构调整
8781d9c docs: 统一架构术语为3域架构，移除5层/7层遗留
5c9481d docs: 清理过时文档
efe2d19 fix(tests): 修复测试文件中的导入路径问题
```

**合并后的 commit message**:
```
refactor(final): 3域架构实施与最终文档更新

## 核心变更
- 统一架构术语为3域架构
- 移除5层/7层遗留架构术语
- 清理过时文档

## 测试调整
- 测试目录的结构调整
- 修复测试文件中的导入路径问题

## 架构完成
- 完成3域架构设计（Input Domain, Decision Domain, Output Domain）
- 所有核心功能迁移到新架构
- 文档完全更新反映新架构
```

---

## 🔧 执行步骤

### 步骤1: 创建备份分支
```bash
git checkout refactor
git branch backup/refactor-before-squash
```

### 步骤2: 创建 refactor-clean 分支
```bash
git checkout -b refactor-clean refactor
```

### 步骤3: 生成交互式rebase脚本
创建脚本文件 `/tmp/squash-script.sh`:
```bash
#!/bin/bash
# 自动生成 rebase todo 文件
```

### 步骤4: 执行rebase合并
```bash
git rebase -i dev
```

### 步骤5: 验证结果
```bash
git log --oneline --graph -10
```

### 步骤6: 对比验证
```bash
git diff dev..refactor --stat
git diff dev..refactor-clean --stat
# 两者应该相同
```

### 步骤7: 推送新分支
```bash
git push origin refactor-clean
```

---

## ✅ 验证清单

- [ ] 备份分支已创建
- [ ] refactor-clean 分支基于 refactor 创建
- [ ] 合并后 commit 数量为 6 个
- [ ] `git diff dev..refactor --stat` 与 `git diff dev..refactor-clean --stat` 相同
- [ ] 所有测试通过
- [ ] 代码风格检查通过
- [ ] commit message 清晰完整

---

## 🔄 未来工作流程

### 日常开发
```bash
# 1. 在 refactor 分支按细粒度提交
git checkout refactor
git commit -m "feat: 小改动A"
git commit -m "fix: 修复bug B"

# 2. 需要合并到 dev 时，更新 refactor-clean
git checkout refactor-clean
git merge refactor  # 或 git rebase refactor

# 3. 手动整理 refactor-clean 的历史（可选）
# 如果需要，可以运行 squash 脚本

# 4. 合并到 dev
git checkout dev
git merge refactor-clean
```

### 定期同步
```bash
# 每周或每完成一个功能块
git checkout refactor-clean
git rebase refactor  # 同步最新的细粒度提交
# 如果需要，手动合并一些 commit
git push origin refactor-clean
```

---

## 📊 合并前后对比

| 项目 | 合并前 | 合并后 |
|------|--------|--------|
| Commit 数量 | 163 个 | 6 个 |
| 历史清晰度 | 细粒度，难以追踪 | 阶段清晰，易于理解 |
| 调试能力 | 高（每个小改动） | 中（按阶段） |
| 合并到 dev | 复杂，冲突多 | 简单，冲突少 |
| 保留细节 | 完整保留 | 按阶段保留 |

---

## 🚨 风险与注意事项

### 风险
1. **rebase 操作不可逆** - 必须先备份
2. **commit 顺序改变** - 可能影响时间轴
3. **merge commit 可能丢失** - 会变成线性历史

### 注意事项
1. 确保远程分支同步
2. 操作期间不要在 refactor 分支推送新 commit
3. 如果出错，切回备份分支重来
4. rebase 完成后需要 force push（如果已推送）

---

## 📝 附录：Commit 统计

- **总 commit 数**: 163 个
- **计划合并为**: 6 个阶段
- **平均每个阶段**: 约 27 个 commit
- **最多 commit 阶段**: 阶段 2（30 个）
- **最少 commit 阶段**: 阶段 6（4 个）

---

**文档版本**: 1.0
**创建日期**: 2026-02-07
**状态**: 待用户确认
