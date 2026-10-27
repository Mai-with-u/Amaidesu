# ADR-048：组件身份单一源——装配层目录表

- 状态：已采纳
- 日期：2026-10-27
- 实现提交：`ea0aa3028be898e912ff35c2bf0f385e00ded249`（refactor: 组件身份收敛为目录表单一源）

## 背景（Context）

ADR-014/027 确立"显式注册表 + 双表 + 启动断言"形态后，组件身份实际要登记四处：`config.registry` 的 EXPECTED 手抄名单与填充函数、`collectors.factory` 的名单与实例化 if 分支（工具 provider 同构于 `tools.bootstrap` 成员表）。一致性靠启动断言与契约测试兜底——测试在补偿缺失的单一源。每新增一个组件付四处登记税，"加内容 = 加包 + 配置，框架零改动"的目标不成立。

同时存在一条依赖方向约束：config 包不得反向 import 组件实现包（`ecc324f7` 升级钩子声明化时斩断过组件依赖）。因此"名字 → 构造类"的表不能放进 config 侧注册表——注册类引用必然 import 组件包。

## 决策（Decision）

**装配层新建 `src/modules/component_catalog.py` 作为组件身份的唯一目录表；config.registry 降级为纯 Schema 容器；factory 与 bootstrap 降级为消费目录表的适配器。**

- 目录表两张：`COLLECTOR_BINDINGS`（名字 → 采集器加载器）与 `TOOL_PROVIDER_BINDINGS`（(分类段, 提供者键, 描述) → 提供者模块加载器）。条目的加载器在调用时才 import 组件实现——组件依赖链重，目录被 import 时不付出这些成本，config 侧经填充入口使用目录也不构成对组件包的反向依赖。
- `EXPECTED_*` 手抄名单删除：目录表本身就是期望清单，"条目可加载且带 ConfigSchema"由填充期逐条目强制（import 失败或缺 Schema 属性当场抛出）。
- 启动断言形态随之改变：原"对照手抄名单查缺失"改为"各注册表非空 + 采集器/Agent 分表不相交"。空表意味着组合根没走到填充入口，启动即硬错。
- 显式 import、禁装饰器自动注册的既定否决（ADR-014/027）不变；目录表条目全部显式书写。
- 新增一个采集器 / 工具 provider 的登记点从四处降到两处：组件包内写实现 + 目录表加一条目。

## 替代方案（Alternatives）

- **类引用进 config.registry**：打破"config 不依赖组件包"的依赖方向（ecc324f7 先例），拒绝。
- **装饰器自动注册**：ADR-014/027 已否决（注册副作用、装配确定性差），不翻案。
- **维持双表 + 契约测试**：登记税按组件数线性增长，测试持续补偿缺失的单一源，拒绝。

## 后果（Consequences）

- 新增组件登记点 4 → 2；"认得但装配不了"的表间脱节在结构上不再可能，契约测试从补偿一致性的兜底变为守护新不变式的常规测试。
- 漏登记的失败形态改变：从"启动断言报缺失清单"变为"组件不存在于系统"（不进目录表即不进任何名单）——排错入口从断言信息转向 collectors.toml 物化全集与 WebUI 组件页的缺席；条目写错（import 失败、缺 ConfigSchema）仍在启动期抛出。
- `component_catalog` 成为装配层新接缝：组件身份、Schema 聚合填充、tools 动态域清单（`TOOL_PROVIDER_DOMAINS`）都从这里出；WebUI 与加载器消费其派生量。

## 参考（References）

- [ADR-014：配置体系六文件重构](014-config-six-file-refactor.md)（登记形态的原始出处，本 ADR 修订其"双表 + 断言"一节）
- [ADR-027：动态配置段统一注册表机制](027-dynamic-config-section-registry.md)（Schema 注册表与装配成员表双写的出处，本 ADR 收敛其装配半边）
