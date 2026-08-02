# 文档维护规范

本文档定义 Amaidesu 项目的文档编写与维护规范，是 [AGENTS.md 文档维护规则](../../AGENTS.md#文档维护规则) 的详细展开。核心原则只有两条：

1. **单一事实源**：每个事实只在一处定义，其他文件用链接引用，绝不复制。
2. **渐进式披露**：核心规则内联，操作手册外置——读者只看他需要的部分。

## 1. 单一事实源

以下事实有**唯一权威处**。修改时只改权威处，其他文件中对应内容的"精简版 + 链接"（若有）也应检查是否需同步更新日期标注。

| 事实 | 唯一权威处 |
|------|-----------|
| 事件表（含发布者/订阅者/数据类型） | `docs/architecture/event-system.md` |
| 数据流图 / 组件清单 / 目录结构 | `docs/architecture/overview.md` |
| 生命周期表 | `docs/development-guide.md` §10.2 |
| 架构决策记录（ADR） | `docs/architecture/adr/` |
| 数据流规则约束 | `docs/architecture/data-flow.md` |

### 允许的"精简版 + 链接"模式

某些入口文件（如 `README.md`、`AGENTS.md`）为了可读性会保留**精简版事实 + 链接指向权威处**。这是允许的，但必须满足：

- 精简版只保留"方向/结论"级别信息，不含完整细节
- 必须附链接指向权威处
- 权威处更新后，检查精简版是否需要同步（通常只需更新日期标注）

示例（AGENTS.md 核心事件表）：

```markdown
| 事件名 | 方向 |
|--------|------|
| `input.message.received` | Input → Decision |

> **单一事实源**：完整事件表见 [事件系统](../architecture/event-system.md#事件载荷类型)。
```

## 2. 目录结构约定

```
docs/
├── README.md              # 文档导航（所有文档的入口索引）
├── getting-started.md     # 新手快速开始
├── development-guide.md   # 开发规范总纲（代码风格、命名、数据类型）
├── architecture/          # 架构理解
│   ├── overview.md        # 3阶段架构总览（权威图 + 组件清单）
│   ├── data-flow.md       # 数据流约束规则
│   ├── event-system.md    # 事件系统（权威事件表）
│   ├── event-naming-convention.md  # 事件命名规范
│   └── adr/               # 架构决策记录
├── development/           # 开发实操
│   ├── component-guide.md # 阶段参与者开发
│   ├── pipeline-guide.md  # 管道开发
│   ├── prompt-management.md
│   ├── dependency-injection.md
│   ├── testing-guide.md
│   ├── simulator-guide.md
│   └── documentation-guide.md  # 本文档
├── images/                # 图片资源
└── videos/                # 视频资源（按需创建）
```

**位置原则**：

| 内容类型 | 放哪里 |
|---------|--------|
| 全局架构事实（事件表、组件清单、数据流图） | `docs/architecture/` |
| 开发实操（怎么写 Collector/Pipeline/测试） | `docs/development/` |
| 组件级使用说明 | 跟随代码（组件目录下的 `README.md`） |
| 图片 | `docs/images/` |
| 视频 | `docs/videos/` |
| 根目录媒体文件 | ❌ 禁止（已被 `.gitignore` 忽略） |

## 3. 渐进式披露分层

| 层级 | 文件 | 定位 | 读者 |
|------|------|------|------|
| 入口 | `docs/README.md` | 所有文档导航 | 任何人 |
| 规则 | `AGENTS.md` | AI 代理核心规则（硬规则内联） | AI |
| 入门 | `getting-started.md` | 环境搭建、首次运行 | 新用户 |
| 架构 | `architecture/` | 系统设计、数据流、事件 | 架构理解者 |
| 开发 | `development/` | 具体组件开发 | 开发者 |

**写作原则**：
- 新事实先判断属于哪一层，放权威处，其他文件引用
- 不要在多个文档复制同一段话；需要重复时提取为"精简版 + 链接"
- AGENTS.md 只放硬规则与高频 API，操作细节一律链接 docs/

## 4. 变更流程

修改文档后：

1. **改权威处**：找到单一事实源表中对应的事实，只改那里。
2. **检查精简版**：若 `README.md`/`AGENTS.md` 等有该事实的精简版，确认其结论是否仍成立；不成立则同步（极少数情况）。
3. **更新日期**：在文件末尾"最后更新"行追加 `YYYY-MM-DD（变更摘要）`。若一次变更涉及多个文件，每个修改过的文件都更新。

```markdown
*最后更新：2026-08-02（事件表收敛至 event-system.md 单一事实源）*
```

## 5. ADR 编写规范

### 目录与编号

- 位置：`docs/architecture/adr/`
- 文件名：`NNN-短横线描述.md`（如 `004-output-direct-dispatch.md`）
- 编号：**按创建时间递增**，全局连续，不得跳号或重复
- 失效的 ADR：删除（git 历史保留），不保留"已废弃"文件

### 元数据

每篇 ADR 标题下必须有：

```markdown
# ADR-004：OutputHandlerManager 直接调度 Handler

- 状态：已采纳
- 日期：2026-07-31
- 实现提交：`f9078e65dff65d61efe0daa6e83589ba95a8e409`（refactor(output): ...）
```

- `实现提交`：完整 40 位 hash + 提交信息，可用 `git show <hash>` 追溯
- 新增 ADR 后同步更新 `adr/README.md` 的"现有 ADR"清单

### 内容结构（Nygard 四段式）

1. **背景（Context）**：问题、约束、已知事实
2. **决策（Decision）**：选择了什么、为什么
3. **替代方案（Alternatives）**：考虑过的方案及否决理由
4. **后果（Consequences）**：收益、代价、风险

## 6. 链接规范

- 使用**相对路径**，注意目录层级：`docs/architecture/` 下的文件引用 `docs/development/` 需加 `../`
- 锚点：GitHub 自动生成的锚点会保留标题编号，如 `## 3. 数据类型选用规范` → `#3-数据类型选用规范`
- 图片引用：`![描述](../images/xxx.png)`（相对 docs/ 根）

### 链接检查清单

- 内部链接：`Test-Path` 验证目标文件存在
- 锚点链接：确认目标文件的标题与锚点匹配（注意编号前缀）
- 外部链接（http/https）：确认 URL 有效

## 7. 相关文档

- [AGENTS.md 文档维护规则](../../AGENTS.md#文档维护规则) - 核心规则（单一事实源表）
- [开发规范](../development-guide.md) - 代码风格和数据类型规范
- [3阶段架构总览](../architecture/overview.md) - 权威组件清单与数据流图

---

*最后更新：2026-08-02（创建，文档维护规范成文化）*
