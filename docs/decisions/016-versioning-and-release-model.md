# ADR-016：版本号与发布模型（pyproject 单一声明 + tag 事实源 + main 发布线）

- 状态：已采纳（2026-09-13 定案）
- 日期：2026-09-13
- 实现提交：`57175034abb91e2eda475d65cefa4db57f8b7556`（docs: 定案版本号与发布模型——ADR-016 + release-guide + AGENTS.md 版本节）

## 背景（Context）

v2.0.0 重构以来项目没有任何发布动作：仓库零 git tag，main 停在开发主线（v2.0.0 分支）之后一百多个 commit，pyproject 版本自始至终为 `2.0.0`。需要定案四件事：版本号由什么承载、发布动作发生在哪个分支、用户如何感知版本与变更、内部已有的版本计数与发布版本是什么关系。

已知事实与约束：

1. 运行时版本链路已存在：pyproject `version` → 安装元数据 → `importlib.metadata.version("amaidesu")` → dashboard `/api/v1/system/status` → 前端展示，前端不自持版本。
2. 内部有两套独立版本机制：配置 6 文件的 `[meta].version`（per-file Schema 迁移计数）与存储 `SCHEMA_VERSION`（表结构迁移）。语义是"数据迁移进度"，不是"发布"。
3. 相关项目 MaiBot 的实践教训：运行时硬编码版本常量与 pyproject 双事实源，三方数字漂移（tag `0.12.3` / 常量 `0.13.0-sakana.1` / pyproject `0.11.6` 各说各话）；其官方 WebUI 的"更新日志"页面是前端模板遗留的 mock 数据。

## 决策（Decision）

核心一句：**pyproject 的 `version` 是对外版本号的唯一声明处；git tag `vX.Y.Z` 是发布的唯一事实源；版本号只在发布时 bump；发布动作发生在开发主线 v2.0.0 上，main 仅作发布线快进追随。**

- **SemVer**：MAJOR = 架构代际更替或需用户手动干预的不兼容升级；MINOR = 向后兼容的功能发布；PATCH = 修复。tag 统一带 `v` 前缀。
- **发布 commit 在 v2.0.0 上**：CHANGELOG 条目 + bump pyproject 合为一个 release commit，tag 指向该 commit；main 以 `--ff-only` 快进到该 commit，永不产生自己的提交。tag 与 pyproject 同 commit、同名、同步。
- **CHANGELOG 是发布产物**：根目录 `CHANGELOG.md`，首次发布时创建，发布时手写。它面向用户，与"文档内不设变更历史"的纪律不冲突——该纪律约束的是代码与架构文档内的变更记录条目。
- **内部版本机制解耦**：`[meta].version` 与 `SCHEMA_VERSION` 保持各自独立的版本流，不参与发布流程，发布清单不触碰它们。配置版本流与发布版本同自 2.0.0 起步（该次重构配置系统同步大改）、此后各自独立递进，数字相近是独立演进的自然结果；迁移钩子的 `target_version` 按各配置文件版本流自身链上界递进取号，与发布版本号无关，两套序列互不换算、互不带动。
- **WebUI 不设独立版本号**：dashboard 构建产物由后端托管，同仓同发同装，一个交付物一个版本号；前端版本从 `/api/v1/system/status` 取，不自持。`dashboard/package.json` 的 version 无对外语义。

## 替代方案（Alternatives）

### 在 main 上定版（并入 main 后再 bump + tag）

**拒绝**。main 产生独立 commit 后与 v2.0.0 历史分叉，下次收口需把 release commit 反向并回开发线；tag 落在开发主线历史之外，`git log vX..vY` 区间查询失真。

### WebUI 独立版本号（前端自持 APP_VERSION）

**拒绝**。前端与后端永远一起发布、一起升级，第二个版本号只制造"到底哪个是新 UI"的疑问，且必然漂移（MaiBot 实证）。未来 WebUI 若独立分发（独立 npm 包、独立发布节奏）再引入，届时它自己走自己的 tag。

### 运行时版本用硬编码常量

**拒绝**。与 pyproject 形成双事实源，漂移已被 MaiBot 实证；importlib.metadata 链路在本项目已打通，无需第二个声明处。

### 开发期版本号带 `-dev` 后缀预占下一版本

**拒绝**。项目不经 PyPI 分发，没有预占与冲突需求；保持"pyproject = 最新发布版本"的语义最简单。

## 后果（Consequences）

- 版本号单一声明处，dashboard 显示自动跟随 pyproject，无第二处需要同步。
- main 的 tip 永远指向最新发布，历史一条直线，永不冲突；两次发布之间 main 落后于开发主线（接受：main 的语义是"最新发布在哪"，不是"最新代码在哪"）。
- 发布是一次显式手工动作（写 CHANGELOG + bump + tag + 快进 main），操作见发布指南；节奏为里程碑驱动，task 收口并入 v2.0.0 不等于发布。
- 后续若出现 PyPI 分发或自动化发布需求，再评估 pre-release 后缀与 CI 化。

## 参考

- [发布指南（操作流程）](../guides/release.md)
