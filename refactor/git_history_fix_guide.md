# Git历史补救方案

> **创建日期**: 2026-01-25
> **目的**: 恢复Phase 5插件迁移过程中丢失的Git历史

---

## 📋 问题分析

### 问题描述

在Phase 5插件迁移过程中，从`src/plugins/`到`src/extensions/`的迁移使用了`git add`而不是`git mv`，导致：

- ✅ **原始插件历史保留**：`src/plugins/xxx/`下的文件仍然有完整的Git历史
- ❌ **新扩展历史丢失**：`src/extensions/xxx/`下的新文件（extension.py等）没有历史记录

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

### 方案A: 使用git mv保留历史（推荐）⭐

**优点**:
- 完全保留Git历史
- 使用Git原生机制
- 透明的历史跟踪

**缺点**:
- 需要合并现有代码
- 可能产生冲突
- 需要手动处理extension.py与原始plugin.py的合并

**步骤**:

#### 步骤1: 创建备份分支

```bash
git branch backup/extensions-before-fix
git push origin backup/extensions-before-fix
```

#### 步骤2: 批量迁移脚本

创建 `refactor/tools/fix_git_history.py`:

```python
"""
Git历史修复脚本
使用git mv将原始插件移动到extensions目录，保留Git历史
"""

import os
import subprocess
import sys

# 需要迁移的插件列表（21个已迁移的插件）
PLUGINS_TO_MIGRATE = [
    # B站弹幕系列
    "bili_danmaku",
    "bili_danmaku_official",
    "bili_danmaku_official_maicraft",

    # 优先级1插件
    "console_input",
    "dg_lab_service",
    "emotion_judge",
    "gptsovits_tts",
    "keyword_action",
    "mock_danmaku",
    "remote_stream",
    "sticker",
    "stt",
    "subtitle",
    "tts",
    "vtube_studio",

    # 优先级2,3插件
    "maicraft",
    "mainosaba",
    "obs_control",
    "omni_tts",
    "read_pingmu",
    "screen_monitor",
    "vrchat",
    "warudo",
]

def run_command(cmd, check=True):
    """运行shell命令"""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result

def main():
    print("=" * 60)
    print("Git历史修复脚本")
    print("=" * 60)

    # 检查是否在Git仓库中
    result = run_command("git rev-parse --is-inside-work-tree")
    if result.stdout.strip() != "true":
        print("❌ 错误: 不在Git仓库中")
        sys.exit(1)

    # 检查当前分支
    result = run_command("git branch --show-current")
    current_branch = result.stdout.strip()
    print(f"当前分支: {current_branch}")

    # 创建临时分支用于修复
    temp_branch = "fix/git-history-preservation"
    run_command(f"git checkout -b {temp_branch} origin/refactor")

    print("\n开始迁移插件...")

    for plugin_name in PLUGINS_TO_MIGRATE:
        src_path = f"src/plugins/{plugin_name}"
        dst_path = f"src/extensions/{plugin_name}"

        # 检查源路径是否存在
        if not os.path.exists(src_path):
            print(f"⚠️  警告: {src_path} 不存在，跳过")
            continue

        # 检查目标路径是否存在
        if os.path.exists(dst_path):
            print(f"⚠️  警告: {dst_path} 已存在，需要手动合并")
            print(f"   插件: {plugin_name}")
            continue

        # 使用git mv移动目录
        print(f"\n✅ 迁移: {src_path} -> {dst_path}")
        run_command(f"git mv {src_path} {dst_path}")

    print("\n" + "=" * 60)
    print("迁移完成！")
    print("=" * 60)
    print("\n下一步:")
    print("1. 检查冲突文件（如果有）")
    print("2. 合并extension.py代码到原始plugin.py")
    print("3. 运行测试确保功能正常")
    print("4. 提交修复")
    print("\n命令:")
    print("  git add -A")
    print("  git commit -m 'fix: preserve git history for plugin migration'")
    print("  git checkout refactor")
    print("  git merge fix/git-history-preservation")

if __name__ == "__main__":
    main()
```

#### 步骤3: 执行迁移脚本

```bash
python refactor/tools/fix_git_history.py
```

#### 步骤4: 手动处理extension.py合并

对于每个插件，需要合并以下内容：

**原始plugin.py**（src/extensions/xxx/plugin.py）:
```python
class SomePlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 原始插件逻辑
```

**新extension.py**（src/extensions/xxx/extension.py）:
```python
class SomeExtension(BaseExtension):
    async def setup(self, event_bus: EventBus, config: Dict[str, Any]):
        # Extension包装器逻辑
        core_wrapper = CoreWrapper(event_bus)
        plugin = SomePlugin(core_wrapper, config)
        await plugin.setup()
        return []
```

**合并策略**:
1. 保留原始的plugin.py（有Git历史）
2. 将extension.py的逻辑合并到plugin.py
3. 或者在plugin.py中添加Extension支持

#### 步骤5: 提交修复

```bash
# 在修复分支
git add -A
git commit -m "fix: preserve git history for plugin migration using git mv

- 使用git mv移动所有21个插件到src/extensions/
- 保留完整的Git历史记录
- 合并extension.py逻辑到原始plugin.py
- 所有插件功能保持不变

修复 Phase 5 迁移过程中的历史丢失问题"

# 切换回主分支并合并
git checkout refactor
git merge fix/git-history-preservation --no-ff

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

---

### 方案C: 完全重做（不推荐）

**优点**:
- 完全从头开始，最干净

**缺点**:
- 丢失所有历史
- 违反Git最佳实践
- 不推荐

---

## 📊 推荐方案对比

| 方案 | Git历史保留 | 复杂度 | 时间成本 | 推荐度 |
|------|------------|--------|----------|--------|
| **方案A: git mv** | ✅ 100% | 中 | 1-2天 | ⭐⭐⭐⭐⭐ |
| **方案B: Git Notes** | ⚠️ 部分保留 | 低 | 1小时 | ⭐⭐ |
| **方案C: 完全重做** | ❌ 0% | 高 | 3-5天 | ❌ |

---

## 🎯 执行建议

### 立即执行（方案A）

**时间**: 1-2天
**风险**: 中
**收益**: 完全恢复Git历史

**执行顺序**:
1. ✅ 创建备份分支
2. ✅ 运行迁移脚本
3. ⏸️ 手动处理extension.py合并（21个插件）
4. ⏸️ 运行测试验证
5. ⏸️ 提交并推送

### 后续优化

**时间**: 可选
**风险**: 低

1. 更新重构文档，记录Git历史修复
2. 更新AGENTS.md，强调使用git mv的重要性
3. 创建Git hooks，防止未来类似问题

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

### 最佳实践

```bash
# ✅ 正确: 使用git mv
git mv src/plugins/maicraft src/extensions/maicraft
git commit -m "refactor: migrate maicraft to extension system"

# ❌ 错误: 直接移动+add
mv src/plugins/maicraft src/extensions/maicraft
git add src/extensions/maicraft
git commit -m "refactor: migrate maicraft to extension system"
```

---

## 🚀 下一步行动

### 如果选择方案A（推荐）

1. **立即执行**:
   ```bash
   python refactor/tools/fix_git_history.py
   ```

2. **手动合并**:
   - 逐个处理21个插件的extension.py合并
   - 或创建自动化合并脚本

3. **测试验证**:
   - 运行pytest测试
   - 手动测试插件功能
   - 验证Git历史保留

### 如果选择方案B（快速但不完美）

1. **快速记录**:
   ```bash
   # 运行Git notes记录脚本
   ```

2. **文档化**:
   - 在refactor目录创建映射文档
   - 记录每个extension对应的原始plugin路径

### 如果需要帮助

查看以下文档:
- Git官方文档: https://git-scm.com/docs/git-mv
- Git历史跟踪: https://git-scm.com/docs/git-log#_follow_logs
- 重构文档: refactor/plan/phase5_extensions.md

---

**文档创建时间**: 2026-01-25
**创建人**: AI Assistant (Sisyphus)
**状态**: 待用户选择方案
