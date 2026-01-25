# Git历史补救方案（包含命名统一修复）

> **创建日期**: 2026-01-25
> **目的**: 恢复Phase 5插件迁移过程中丢失的Git历史，同时统一Extension→Plugin命名

---

## 📋 问题分析

### 问题1: Git历史丢失

在Phase 5插件迁移过程中，从`src/plugins/`到`src/extensions/`的迁移使用了`git add`而不是`git mv`，导致：

- ✅ **原始插件历史保留**：`src/plugins/xxx/`下的文件仍然有完整的Git历史
- ❌ **新扩展历史丢失**：`src/extensions/xxx/`下的新文件（extension.py等）没有历史记录

### 问题2: 命名不一致

Phase 5使用了`Extension`命名，与项目原有的`Plugin`术语不一致：

| 类别 | 应该的命名 | 实际的命名 | 状态 |
|------|-----------|-----------|------|
| **术语** | Plugin（插件） | Extension（扩展） | ❌ 不一致 |
| **核心文件** | `src/core/plugin.py` | `src/core/extension.py` | ❌ 不一致 |
| **目录** | `src/plugins/` | `src/extensions/` | ⚠️ 都存在 |
| **类名** | `Plugin`, `PluginManager` | `Extension`, `ExtensionManager` | ❌ 不一致 |

**时间线**：
1. **2026-01-18 08:28** (e13b981): 试图将Extension改回Plugin（文档层面）
2. **2026-01-18 13:15** (feaa4d4): 又改回Extension（plan/phase5_extensions.md）
3. **2026-01-25 18:57** (545a9e9): 代码实现时使用Extension命名
4. **2026-01-25 19:51+**: Phase 5插件迁移继续使用Extension

### 当前状态

| 目录 | 状态 | Git历史 |
|------|------|----------|
| `src/plugins/` | 仍然存在 | ✅ 完整保留 |
| `src/extensions/` | 新创建 | ❌ 从新建开始 |
| 迁移提交 | 使用`git add` | ❌ 历史断开 |

### 受影响的插件

#### 已迁移到Extension系统（21个）：

**优先级1** (有plugin.py):
- bili_danmaku
- bili_danmaku_official
- bili_danmaku_official_maicraft
- console_input
- dg_lab_service
- emotion_judge
- gptsovits_tts
- keyword_action
- maicraft
- mainosaba
- mock_danmaku
- obs_control
- omni_tts
- read_pingmu
- remote_stream
- screen_monitor
- sticker
- stt
- subtitle
- tts
- vtube_studio
- vrchat
- warudo

**优先级2,3** (已完成迁移):
- 已全部包装为Extension

#### 无plugin.py的插件（8个，未迁移）：
- arkights
- bili_danmaku_selenium
- command_processor
- dg-lab-do
- funasr_stt
- llm_text_processor
- message_replayer
- minecraft

---

## 🎯 补救方案

### 方案A: 使用git mv保留历史 + 统一命名（推荐）⭐

**优点**:
- 完全保留Git历史
- 使用Git原生机制
- 透明的历史跟踪
- 统一使用Plugin术语
- 修复命名不一致问题

**缺点**:
- 需要合并现有代码
- 可能产生冲突
- 需要手动处理extension.py与原始plugin.py的合并
- 需要重命名大量类和导入

**步骤**:

#### 步骤1: 创建备份分支

```bash
git branch backup/before-unified-fix
git push origin backup/before-unified-fix
```

#### 步骤2: 批量迁移和重命名脚本

使用更新后的 `refactor/tools/fix_git_history.py`，它会自动：

1. **使用git mv迁移插件**（保留Git历史）
2. **重命名extension.py为plugin.py**（统一命名）
3. **重命名核心文件**（extension → plugin）
4. **更新所有导入语句**（Extension → Plugin）
5. **清理旧文件**（删除已迁移的插件）
6. **重命名目录**（extensions → plugins）

```bash
python refactor/tools/fix_git_history.py
```

**脚本会执行以下操作**：

```python
# 步骤1: 使用git mv迁移到临时位置
git mv src/plugins/maicraft src/plugins_new/maicraft

# 步骤2: 重命名extension.py为plugin.py
git mv src/plugins_new/maicraft/extension.py src/plugins_new/maicraft/plugin.py

# 步骤3: 更新plugin.py中的类名
# Extension → Plugin
# BaseExtension → BasePlugin
# ExtensionManager → PluginManager
# ExtensionInfo → PluginInfo

# 步骤4: 重命名核心文件
git mv src/core/extension.py src/core/plugin.py
git mv src/core/extension_manager.py src/core/plugin_manager.py
git mv src/core/extensions/ src/core/plugins/

# 步骤5: 删除旧插件
git rm -rf src/plugins/maicraft

# 步骤6: 重命名extensions目录
git mv src/extensions src/extensions_old

# 步骤7: 重命名plugins_new为plugins
git mv src/plugins_new src/plugins

# 步骤8: 更新所有导入
# from .extension import → from .plugin import
# from src.core.extension import → from src.core.plugin import
```

#### 步骤3: 手动处理冲突

如果某些插件有冲突，需要手动合并：

**原始plugin.py**（src/plugins/maicraft/plugin.py，有Git历史）:
```python
class MaicraftPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 原始插件逻辑
```

**新extension.py**（src/extensions/maicraft/extension.py，包装器）:
```python
class MaicraftExtension(BaseExtension):
    async def setup(self, event_bus: EventBus, config: Dict[str, Any]):
        # Extension包装器逻辑
        core_wrapper = CoreWrapper(event_bus)
        plugin = MaicraftPlugin(core_wrapper, config)
        await plugin.setup()
        return []
```

**合并策略**：
1. 保留原始的plugin.py（有Git历史）
2. 删除extension.py（使用plugin.py）
3. 或者在plugin.py中添加Plugin支持

#### 步骤4: 提交修复

```bash
# 在修复分支提交
git add -A
git commit -m "fix: preserve git history and unify Plugin terminology

- 使用git mv迁移所有21个插件，保留完整Git历史
- 统一命名：Extension → Plugin
- 重命名文件：extension.py → plugin.py
- 重命名核心文件：src/core/extension.py → src/core/plugin.py
- 重命名核心管理器：extension_manager.py → plugin_manager.py
- 重命名目录：src/extensions/ → src/plugins/
- 更新所有导入语句：Extension → Plugin
- 删除旧文件：src/extensions/（重命名为extensions_old）
- 所有插件功能保持不变

修复内容：
1. Git历史：所有插件现在有完整的Git历史记录
2. 命名统一：统一使用Plugin术语，与项目现有命名一致
3. 类名更新：Extension → Plugin, BaseExtension → BasePlugin
4. 导入更新：所有导入语句统一为Plugin"

# 切换回主分支并合并
git checkout refactor
git merge fix/unified-history-naming --no-ff

# 推送到远程
git push origin refactor
```

---

### 方案B: 创建Git Notes记录关联

**优点**:
- 不需要修改现有代码
- 简单快速

**缺点**:
- Git notes不会自动显示
- 需要额外的工具查看
- 不是Git原生历史
- 不能解决命名不一致问题

**步骤**:

```bash
# 为每个extension记录对应的plugin历史
for plugin in bili_danmaku maicraft mainosaba warudo; do
    # 找到迁移提交的hash
    migration_commit=$(git log --oneline --grep="migrate.*${plugin}" | head -1 | awk '{print $1}')

    # 记录原始插件路径
    git notes add ${migration_commit} -m "原始插件: src/plugins/${plugin}/"

    # 添加历史链接
    git notes add ${migration_commit} -m "历史: git log --follow src/plugins/${plugin}/"
done
```

**注意**：此方案不会解决命名不一致问题

---

### 方案C: 完全重做（不推荐）

**优点**:
- 完全从头开始，最干净

**缺点**:
- 丢失所有历史
- 违反Git最佳实践
- 不推荐
- 需要重命名所有内容为Plugin

---

## 📊 推荐方案对比

| 方案 | Git历史保留 | 命名统一 | 复杂度 | 时间成本 | 推荐度 |
|------|------------|----------|--------|----------|--------|
| **方案A: git mv + 重命名** | ✅ 100% | ✅ 是 | 中 | 1-2天 | ⭐⭐⭐⭐⭐ |
| **方案B: Git Notes** | ⚠️ 部分保留 | ❌ 否 | 低 | 1小时 | ⭐⭐ |
| **方案C: 完全重做** | ❌ 0% | ✅ 是（但全部重建） | 高 | 3-5天 | ❌ |

---

## 🎯 执行建议

### 立即执行（方案A）

**时间**: 1-2天
**风险**: 中
**收益**: 完全恢复Git历史 + 统一命名

**执行顺序**:
1. ✅ 创建备份分支
2. ✅ 运行迁移和重命名脚本
3. ⏸️ 手动处理需要合并的插件
4. ⏸️ 运行测试验证
5. ⏸️ 提交并推送

### 后续优化

**时间**: 可选
**风险**: 低

1. 更新重构文档，记录Git历史修复和命名统一
2. 更新AGENTS.md，强调使用git mv的重要性
3. 创建Git hooks，防止未来类似问题

---

## 📝 重要变更总结

### 命名统一

| 旧命名（Extension） | 新命名（Plugin） |
|------------------|----------------|
| `src/core/extension.py` | `src/core/plugin.py` |
| `src/core/extension_manager.py` | `src/core/plugin_manager.py` |
| `src/core/extensions/` | `src/core/plugins/` |
| `src/extensions/` | `src/plugins/` |
| `Extension` 类 | `Plugin` 类 |
| `BaseExtension` 类 | `BasePlugin` 类 |
| `ExtensionManager` 类 | `PluginManager` 类 |
| `ExtensionInfo` 类 | `PluginInfo` 类 |
| `extension.py` | `plugin.py` |

### 导入语句更新

```python
# 旧命名
from .extension import Extension, BaseExtension
from src.core.extension import Extension
from src.core.extension_manager import ExtensionManager
from src.core.extensions.example import ExampleExtension

# 新命名
from .plugin import Plugin, BasePlugin
from src.core.plugin import Plugin
from src.core.plugin_manager import PluginManager
from src.core.plugins.example import ExamplePlugin
```

---

## 📝 注意事项

### Git mv的优势

- ✅ **保留历史**: `git log --follow`可以查看完整历史
- ✅ **记录移动**: Git知道文件是移动的，不是新建的
- ✅ **追溯变更**: 可以追踪文件的所有历史变更
- ✅ **代码审计**: 方便进行代码审计和问题追溯

### Git add的问题

- ❌ **历史断开**: 新文件的历史从创建开始
- ❌ **丢失上下文**: 无法查看原始插件的演变过程
- ❌ **审计困难**: 无法追踪代码的来源和变更历史

### Plugin vs Extension 命名选择

**Plugin（插件）的优势**：
- ✅ 符合"插件"的中文表达
- ✅ 与项目现有术语一致（BasePlugin, PluginManager）
- ✅ 最初设计就是用Plugin
- ✅ 更直观和易懂

**Extension（扩展）的优势**：
- ✅ 如果您更喜欢"扩展"这个概念
- ❌ 需要重命名所有现有的BasePlugin等类
- ❌ 与现有代码不一致

### 最佳实践

```bash
# ✅ 正确: 使用git mv
git mv src/plugins/maicraft src/plugins/maicraft  # 重命名
git mv src/plugins/maicraft src/plugins_new/maicraft  # 迁移到新位置
git commit -m "refactor: migrate maicraft plugin"

# ❌ 错误: 直接移动+add
mv src/plugins/maicraft src/plugins_new/maicraft
git add src/plugins_new/maicraft
git commit -m "refactor: migrate maicraft plugin"
```

---

## 🚀 下一步行动

### 如果选择方案A（推荐）

1. **立即执行**:
   ```bash
   python refactor/tools/fix_git_history.py
   ```

2. **手动合并**:
   - 处理有冲突的插件（如果有）
   - 确认所有重命名正确

3. **测试验证**:
   - 运行pytest测试
   - 手动测试插件功能
   - 验证Git历史保留

4. **验证命名**:
   - 检查所有使用Extension的地方都改为Plugin
   - 检查所有导入语句正确

### 如果需要帮助

查看以下文档:
- Git官方文档: https://git-scm.com/docs/git-mv
- Git历史跟踪: https://git-scm.com/docs/git-log#_follow_logs
- 重构文档: refactor/plan/phase5_extensions.md
- 本修复脚本: refactor/tools/fix_git_history.py

---

**文档创建时间**: 2026-01-25
**最后更新**: 2026-01-25
**创建人**: AI Assistant (Sisyphus)
**状态**: 待用户执行
