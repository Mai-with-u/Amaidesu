# Git历史修复和Plugin命名统一 - 相关文档索引

> **创建日期**: 2026-01-25
> **目的**: 快速查找与修复相关的文档

---

## 📋 核心修复文档

### 1. Git历史修复方案指南
**文件**: `refactor/git_history_fix_guide.md`
**用途**: 完整的修复方案和执行指南

**内容包含**:
- ✅ Git历史丢失问题分析
- ✅ Extension → Plugin 命名不一致问题分析
- ✅ 3种修复方案对比（推荐方案A）
- ✅ 详细的7步执行步骤
- ✅ 重要变更总结表
- ✅ 注意事项和最佳实践

**何时使用**:
- 执行修复前必读
- 了解修复的整体策略
- 遇到问题时的参考

---

### 2. 自动化修复脚本
**文件**: `refactor/tools/fix_git_history.py`
**用途**: 一键执行Git历史保留和命名统一

**功能**:
- ✅ 自动迁移21个插件（使用git mv）
- ✅ 自动重命名 extension.py → plugin.py
- ✅ 自动重命名核心文件（extension → plugin）
- ✅ 自动更新所有导入语句（Extension → Plugin）
- ✅ 创建备份分支
- ✅ 详细的进度报告和错误处理

**7步修复流程**:
1. 使用git mv迁移插件到临时位置 `src/plugins_new/`
2. 重命名 `extension.py` → `plugin.py`（所有插件）
3. 重命名核心文件（extension → plugin）
4. 删除旧的 `src/plugins/` 中已迁移的插件
5. 重命名 `src/extensions/` → `src/extensions_old/`
6. 重命名 `src/plugins_new/` → `src/plugins/`
7. 更新所有导入语句（Extension → Plugin）

**执行命令**:
```bash
python refactor/tools/fix_git_history.py
```

---

## 📊 Phase 5 相关文档

### 3. Phase 5 实施计划
**文件**: `refactor/plan/phase5_extensions.md`
**状态**: ⚠️ 文件名使用extensions（应改为plugins）
**内容**: Extension系统设计（需要更新为Plugin）

**需要更新**: 整个文档中的Extension → Plugin

---

### 4. Phase 5 实施笔记
**文件**: `refactor/phase5_implementation_notes.md`
**内容**: Extension系统实现记录

**需要更新**:
- Extension类名 → Plugin类名
- ExtensionManager → PluginManager
- 所有Extension相关术语

---

### 5. Phase 5 插件迁移计划
**文件**: `refactor/phase5_plugin_migration_plan.md`
**内容**: 详细的插件迁移策略和步骤

**需要更新**: 如果文档中有Extension，改为Plugin

---

### 6. Phase 5 进度报告
**文件**: `refactor/phase5_progress_report.md`
**内容**: 12/23插件迁移进度（52.2%）

**状态**: 历史文档，不需要更新

---

### 7. Phase 5 最终完成报告
**文件**: `refactor/phase5_final_progress_report.md`
**内容**: 21/21插件迁移完成（100%）

**状态**: 历史文档，不需要更新

**重要信息**:
- 迁移的插件列表（21个）
- 使用的方法（git add而非git mv）
- 未迁移的插件列表（8个）

---

### 8. 无plugin.py的插件记录
**文件**: `refactor/phase5_plugins_without_plugin_py.md`
**内容**: 8个无plugin.py的插件

**插件列表**:
- arkights
- bili_danmaku_selenium
- command_processor
- dg-lab-do
- funasr_stt
- llm_text_processor
- message_replayer
- minecraft

---

## 📊 Phase 6 相关文档

### 9. Phase 6 清理计划
**文件**: `refactor/plan/phase6_cleanup.md`
**内容**: Phase 6的清理和测试计划

**相关内容**:
- AmaidesuCore简化
- 旧代码清理
- 静态代码评审
- 配置迁移工具

---

### 10. Phase 6 实施笔记
**文件**: `refactor/phase6_implementation_notes.md`
**内容**: Phase 6实施记录

**已完成**:
- AmaidesuCore从599行简化到464行
- 删除HTTP服务器代码
- 静态代码评审

---

### 11. Phase 6 代码质量报告
**文件**: `refactor/phase6_code_quality_report.md`
**内容**: 代码质量检查结果

**检查内容**:
- ruff check结果
- 代码格式化
- 未使用导入和变量
- 命名不一致问题

---

## 📊 技术债文档

### 12. 技术债总结
**文件**: `refactor/tech_debt_summary.md`
**内容**: 所有小问题和未完成工作的总结

**相关内容**:
- Git历史丢失问题
- Extension → Plugin 命名不一致
- 未使用的导入和变量
- AmaidesuCore代码量未达到目标

**已记录**:
- Phase 6 静态评审结果（2026-01-25更新）
- 命名统一待处理事项

---

### 13. 技术债实施笔记
**文件**: `refactor/tech_debt_implementation_notes.md`
**内容**: 技术债处理的详细记录

---

### 14. Phase 技术债笔记
**文件**: `refactor/phase_tech_debt_notes.md`
**内容**: 各个Phase发现的技术问题

---

## 🎯 修复前后文档对比

### 需要更新的文档

| 文档 | 状态 | 更新内容 |
|------|------|---------|
| `refactor/plan/phase5_extensions.md` | ⚠️ | Extension → Plugin |
| `refactor/phase5_implementation_notes.md` | ⚠️ | Extension → Plugin |
| `refactor/tech_debt_summary.md` | ✅ | 已更新 |

### 不需要更新的文档（历史记录）

| 文件 | 原因 |
|------|------|
| `refactor/phase5_progress_report.md` | 历史文档 |
| `refactor/phase5_final_progress_report.md` | 历史文档 |
| `refactor/phase5_plugins_without_plugin_py.md` | 历史记录 |
| `refactor/phase6_implementation_notes.md` | 历史记录 |
| `refactor/phase6_code_quality_report.md` | 历史记录 |
| `refactor/tech_debt_implementation_notes.md` | 历史记录 |
| `refactor/phase_tech_debt_notes.md` | 历史记录 |

---

## 🚀 修复执行流程

### 步骤1: 阅读核心文档

```bash
# 阅读修复方案指南
cat refactor/git_history_fix_guide.md

# 重点章节：
# 1. 问题分析（问题1 + 问题2）
# 2. 方案A详细步骤
# 3. 重要变更总结
# 4. 注意事项
```

### 步骤2: 执行自动化脚本

```bash
# 运行修复脚本
python refactor/tools/fix_git_history.py
```

**脚本会自动**:
1. 创建备份分支
2. 迁移21个插件（git mv）
3. 重命名extension.py为plugin.py
4. 更新所有类名
5. 更新核心文件名
6. 清理旧文件
7. 更新导入语句

### 步骤3: 验证修复结果

```bash
# 1. 检查Git状态
git status

# 2. 验证Git历史
git log --follow src/plugins/maicraft/ | head -10

# 3. 验证命名统一
grep -r "Extension" src/plugins/ --include="*.py" | grep -v "^#"
grep -r "BaseExtension" src/ --include="*.py" | grep -v "^#"
grep -r "ExtensionManager" src/ --include="*.py" | grep -v "^#"
```

**预期结果**:
- ✅ Git历史完整显示（从原始maicraft开始）
- ✅ 没有Extension类名（除非注释）
- ✅ 没有BaseExtension类名
- ✅ 没有ExtensionManager类名

### 步骤4: 提交修复

```bash
# 在修复分支提交
git add -A
git commit -m "fix: preserve git history and unify Plugin terminology"

# 合并回主分支
git checkout refactor
git merge fix/unified-history-naming --no-ff

# 推送
git push origin refactor
```

---

## 📝 修复后需要更新的文档

### 执行修复脚本后

修复脚本会自动更新以下内容：
- ✅ 所有插件的 `extension.py` → `plugin.py`
- ✅ 所有导入语句 `Extension` → `Plugin`
- ✅ 核心文件名 `extension` → `plugin`
- ✅ 类名 `Extension` → `Plugin`

### 手动更新（可选）

如果需要，可以手动更新：
- `refactor/plan/phase5_extensions.md` - 全文搜索替换
- `refactor/phase5_implementation_notes.md` - 全文搜索替换

---

## 🔗 文档依赖关系图

```
git_history_fix_guide.md (核心指南)
    ├── 引用：fix_git_history.py (脚本)
    ├── 引用：phase5_final_progress_report.md (已迁移插件列表)
    └── 更新：tech_debt_summary.md (问题记录)

phase5_extensions.md (计划文档)
    ├── 需要更新：Extension → Plugin
    └── 引用：phase5_plugin_migration_plan.md

phase5_implementation_notes.md (实施记录)
    ├── 记录：Extension系统实现
    └── 需要更新：Extension → Plugin

phase6_implementation_notes.md (实施记录)
    ├── 记录：AmaidesuCore简化
    └── 引用：phase6_cleanup.md

tech_debt_summary.md (问题记录)
    ├── 记录：Git历史丢失
    ├── 记录：Extension → Plugin 不一致
    ├── 记录：Phase 6 静态评审结果
    └── 引用：phase6_code_quality_report.md
```

---

## 📊 快速查找指南

### 我想了解...

**为什么要修复Git历史？**
→ 阅读 `refactor/git_history_fix_guide.md` 的"问题分析"章节

**Extension和Plugin有什么区别？**
→ 阅读 `refactor/git_history_fix_guide.md` 的"命名不一致"章节

**如何执行修复？**
→ 阅读 `refactor/git_history_fix_guide.md` 的"方案A"章节
→ 执行 `python refactor/tools/fix_git_history.py`

**修复包含哪些操作？**
→ 查看 `refactor/git_history_fix_guide.md` 的"重要变更总结"

**哪些插件已迁移？**
→ 查看 `refactor/phase5_final_progress_report.md`

**Phase 5完成了什么？**
→ 查看 `refactor/phase5_implementation_notes.md`

**Phase 6完成了什么？**
→ 查看 `refactor/phase6_implementation_notes.md`

**有哪些技术债？**
→ 查看 `refactor/tech_debt_summary.md`

---

**文档创建时间**: 2026-01-25
**创建人**: AI Assistant (Sisyphus)
**状态**: 已完成
**最后更新**: 2026-01-25
