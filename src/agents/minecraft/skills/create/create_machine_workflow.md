---
name: create_machine_workflow
description: 机械动力机器从需求到产出：选工艺、勘测场地、写蓝图、施工、接动力、试运行验收；要造压机、搅拌、粉碎、鼓风机加工等装置时读
agents: [minecraft]
category: create
requires:
  mods: [create]
tags: [机械动力, 机器, 蓝图, 验收]
---

# 机械动力：机器从需求到产出

主播说“做一台机器出某某”时，真正的目标是**产物**；机器是手段。整条链路是：工艺 → 场地 → 蓝图 → 施工 → 动力 → 运行验收，每一段有自己的证据，前一段成功不能冒充后一段。

## 1. 先定工艺

- 从产物出发查配方：`maicraft_perceive(view="knowledge", query=产物名)` 找候选，读 `maicraft://knowledge/recipes/...` 看它由哪种加工得到（压制、搅拌、粉碎、鼓风机洗涤/烟熏/熔炼、机械手装配等）。
- 命中工艺对应的 Ponder 就读正文，了解部件关系；只补当前设计缺的那一点，不为确认名字遍历全部教程。
- 背包或 AE 里已有成品就先问自己：目标是不是“用机器做出来”？是的话现成品不算交付。

## 2. 常见加工与核心部件

| 加工 | 核心部件 | 要点 |
| --- | --- | --- |
| 压制（板材） | 动力压机 + 下方工作台面（置物台或传送带） | 压机在上、物品在其正下方 |
| 搅拌/合金 | 动力搅拌器 + 工作盆，部分配方需要下方加热 | 加热配方需要火焰人燃烧室 |
| 粉碎 | 一对粉碎轮，转向相对 | 两轮转向相反才能吃料 |
| 鼓风机批量加工 | 鼓风机 + 介质（水=洗涤、火=烟熏、岩浆=熔炼） | 物品要经过气流里的介质 |
| 装配 | 机械手 + 传送带 | 序列装配要多步，按配方顺序 |

部件精确用法以 Ponder 与 `maicraft://knowledge/machine_assembly` 为准，本表只帮你选方向。

## 3. 场地与蓝图

- `maicraft_perceive(view="construction_site")` 勘测，拿到 `snapshot_id` 与场地名。
- 写显式 `blueprint`（`schema_version:1` 的 blocks + assembly），产品目标写 `expected_output`，主播禁用的模组写 `constraints.forbidden_mods`。格式按需读 `maicraft://knowledge/machine_assembly`。
- 需要外部动力或材料输入时在蓝图里声明接收口（`external_inputs`），声明不等于已接通。

## 4. 施工与动力

- `maicraft_plan`（`goal.ability = maicraft:build_machine`，`allow_modify: true` 仅在已授权施工时设置）→ `maicraft_execute(plan_id)`。施工期间身体在忙，你去准备动力方案。
- 施工回执先看 `blueprint_diff` 与未完成部分，不符就用 `maicraft:modify_machine` 改，不重新从零建。
- 动力按《机械动力：动力网》技能接入，确认 `power_ready`。

## 5. 运行与验收

- 用 `maicraft:operate_machine` 投料或观察生产；交付以**真实产物进入背包或容器**为准，静态结构与转速都不是产出证明。
- 产出偏离预期时读回执里的现场差异，改一处再试，不整机推倒。

## 何时上报

工艺依赖的物品或部件拿不到、或连续两种不同布局都无法产出时，带上已试布局与回执要点上报主播。
