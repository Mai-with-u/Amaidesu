# ADR-027：动态配置段统一注册表机制——工具提供者 config 纳入校验与默认值补全

- 状态：已采纳
- 日期：2026-09-21
- 实现提交：随本 ADR 同笔交付（单提交；`git log -- docs/decisions/027-dynamic-config-section-registry.md` 定位）

## 背景（Context）

配置系统对动态键子段（键名运行期才确定、每个键对应不同 Schema 的段）存在两套并行的待遇，且只建了一半：

- 采集器/Agent 段（`[collectors.<name>]`）有完整机制——`COMPONENT_SCHEMAS` 注册表（显式 import 填充）+ 加载管线阶段④的分支（`_validate_collectors_sections`）：子段按包内 ConfigSchema 校验、缺键补默认、未知键剥离，阶段⑤ 全量写回落盘；
- 工具提供者段（`[tools.avatar.<name>].config` / `[tools.studio.<name>].config`）的 `config` 字段是自由 `Dict[str, Any]`——加载管线对它无事可做，文件里的空段永远空着（有哪些键可配只能翻代码），配置错误要等到运行期 provider 构造时才暴露（被装配隔离吞成 ERROR 日志、工具静默消失），WebUI 编辑链路（`_walk_schema`）走到此处也只能返回"自由字典、放弃校验"。

根因是类型系统表达力缺口：`Dict[str, ???]` 的 value 类型只有一个声明位，而"每个键该用哪个 Schema"随键变（vts/warudo/vrchat 各一份）。仓库内已有的正确先例是 `VisionProviderConfig.config` 的 **typed 引用**（静态单实例段直接把包内 Schema 写成字段类型）——但 typed 引用只解静态命名段，动态键段必须有一张"键 → Schema"查找表。采集器当年建了，tools 域没建。

## 决策（Decision）

**动态键段的"键 → Schema"查找表补齐 tools 半边，两条机制的适用规则立为明文：**

- **静态命名段 → typed 引用**：聚合 Schema 直接把包内 ConfigSchema 写成字段类型（先例 `VisionProviderConfig.config`，编译期绑定，漂移写回天然自动补全）；
- **动态键段 → 注册表分支**：`registry.TOOL_PROVIDER_SCHEMAS`（键 `(分类段, 提供者键)` 二元组，与装配侧 `tools.bootstrap._DOMAIN_MEMBERS` 成员身份同构，显式 import 填充、无装饰器副作用）+ 加载管线 `_validate_tool_provider_sections` 分支（与采集器分支逐行同构：校验 / 补默认 / 剥未知键 / 回填供阶段⑤写回 / 未注册段 warning 容忍）。

配套机制：

- **一致性契约测试**：bootstrap 装配成员表的每个成员必须在注册表有 Schema——只登记装配、不登记 Schema 的 provider，其 config 段退化为无校验、无默认值补全的自由 dict，正是本 ADR 修复前的缺陷形态，契约测试把这条退化路焊死；`EXPECTED_TOOL_PROVIDERS` 启动断言同采集器。
- **注释化落盘**：序列化 tools 动态段时按注册表重建 `config` 子表，包内 Schema 的字段 description 成为行前注释（裸 dict 渲染路径无元数据、只能出裸键）；重建失败降级裸键 + 日志（注释是可读性增益，不构成硬错理由）。
- **None 剥离回填**：`Optional` 字段的 `None` 不落盘（TOML 无 null 字面量），回填 dict 前剥离。
- **WebUI 校验下钻**：`config_adapter._walk_schema` 对 tools 动态段的 `config.<字段>` 路径按注册表下钻，未知键拒绝（422）、整对象更新交加载器整体校验。
- 段本体未知键（`enabled` 拼写错误等，extra="allow" 保留、序列化丢弃）计入 redundant 报告，清理可见。
- `[tools.mcp].config.servers`（键本身动态）与 TTS/字幕引擎参数保持自由 dict，不在本机制范围。

## 替代方案（Alternatives）

- **把 provider config 声明成 typed 引用（vision 同款）**：只适用于静态命名段；`avatar.<name>` 每键不同 Schema，类型系统无"Map value 类型随 key 变"的表达，不可行。
- **装饰器自动注册**：被既有纪律否决（注册副作用、装配确定性差），注册表一律显式 import + 断言。
- **provider 侧继续自补默认、不动配置系统**：正是被拆除的现状——文件不可见、错误时点在运行期、WebUI 无护栏的三层不一致。
- **为 provider config 建独立版本流与迁移钩子**：无数据变换（只补缺失键、剥未知键），属漂移写回的正常工作，不升 `[meta].version`、不写钩子。

## 后果（Consequences）

- `[tools.avatar.<name>].config` 空段在下次启动自动展开全字段默认值（带注释）落盘；配置类型错误从运行期装配失败前移到加载期硬错（带完整 dotted path）；WebUI 对 provider config 键有了字段级校验。
- 运行时行为零变化：provider 本来就在构造时自补默认，补全后的数值与此前一致；装配侧消费同一份补全数据。
- 新增工具 provider 的登记处从一处（bootstrap 成员表）变两处（+ config 注册表 / EXPECTED 清单），契约测试守护两表一致。
- 采集器分支存在同型潜在缺陷：其子段 Schema 若未来引入 `Optional`-None 默认字段，`model_dump()` 回填会在序列化裸 dict 路径炸 `None`（本 ADR 实现已在 tools 分支剥 None；采集器分支留待其真正引入此类字段时同步修复）。
- 段缺失场景（文件里没有该 provider 段）不自动生成段——与采集器语义一致，段的存在性归人类/WebUI 决定。
