# 发布指南

本文档定义 Amaidesu 的发布操作流程：版本号规则、CHANGELOG 写法与发布步骤。决策依据（为什么这样设计）见 [ADR-016](../decisions/016-versioning-and-release-model.md)。

## 角色分配

```
task/*  ──收口──▶  v2.0.0（开发主线）──发布──▶  tag vX.Y.Z + main 快进
```

- **v2.0.0 = 开发主线**：task/* 的收口目标，日常版本号不因收口而变。
- **tag `vX.Y.Z` = 发布事实源**：annotated tag，指向 release commit，统一带 `v` 前缀。
- **main = 发布线**：只在发布时快进，tip 永远指向最新发布，不产生自己的提交。
- 未来 3.0 代际时从主线长出 `v3.0.0` 分支接班，流程不变。

## 版本号规则（SemVer）

| 升什么位 | 判定 |
|---|---|
| PATCH `2.0.x` | bug 修复、小调整，无新功能 |
| MINOR `2.x.0` | 新功能：新增采集器/Agent/工具、行为增强，配置向后兼容 |
| MAJOR `x.0.0` | 架构代际更替、配置/存储需用户手动干预的不兼容升级 |

纪律：

- **版本号只在发布那一刻 bump**（`pyproject.toml` 的 `version`），平时停在上一发布版本；项目不经 PyPI 分发，不用 `-dev` 后缀。
- **内部版本机制不参与发布**：配置 `[meta].version` 与存储 `SCHEMA_VERSION` 是数据迁移机制，各有独立版本流，发布清单不触碰。
- dashboard 显示的版本经 `importlib.metadata` 从 pyproject 读取，前端不自持版本号。

## CHANGELOG

根目录 `CHANGELOG.md`，首次发布时创建，发布时手写。素材从 `git log v上一版..HEAD --oneline` 的 conventional commits 提炼归类，按架构子模块分组（整体架构 / 各 Agent / 工具系统 / 事件系统等），条目用短句，一条只说一件事：

```markdown
# Changelog

## [2.1.0] - 2026-XX-XX

### <子模块>
- （该模块的变化，按需分组，无变化的模块不出现在本版）

### 升级注意
- （有配置迁移 hook / SCHEMA_VERSION 升位时必写；无则省略此节）
```

## 发布步骤

1. **收口检查**（在 v2.0.0 上）：`uv run pytest tests/` 全量绿 + `uv run ruff check` + `uv run ruff format .`——发版属于全量测试场景。
2. **写 CHANGELOG**：`CHANGELOG.md` 顶部新增本版本条目。
3. **定版提交**：bump `pyproject.toml` 的 `version`，与 CHANGELOG 合为一个 release commit：`chore: 发布 2.1.0`。
4. **打 tag**：`git tag -a v2.1.0 -m "<一句话发布摘要>"`。
5. **main 快进**：`git switch main && git merge --ff-only v2.0.0 && git switch v2.0.0`。
6. **推送**：分支 + `git push --tags`（推送按提交纪律需用户授权）。
7. **验证**：启动后 dashboard 系统状态显示新版本号。

## 节奏

里程碑驱动，不定期发版：一批任务收口、需要给用户一个稳定点时发布。**task 收口并入 v2.0.0 ≠ 发布**，发布永远是显式动作。
