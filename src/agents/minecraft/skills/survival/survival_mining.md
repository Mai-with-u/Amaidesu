---
name: survival_mining
description: 采矿：为铁、煤、钻石等矿物规划下矿路线、照明与安全；需要成批矿物或第一次下深层时读
agents: [minecraft]
category: survival
tags: [采矿, 铁, 钻石, 照明]
---

# 采矿：铁与钻石

采矿要的是“稳定拿到足量矿物并活着回来”，不是挖出最深的洞。

## 先定目标数量与深度

- 先说清要什么、要多少：铁通常 1～2 组起步，钻石按用途算（镐 3、全套装备 24）。
- 深度参考（1.18 以后的分布）：铁在 Y≈16 附近较多，高山 Y≈232 也有；钻石在 Y≈-58 一带最多；深板岩层更慢挖，需要更好的镐。
- 先用 `maicraft:find_block` 看附近已暴露的矿；附近有洞穴或已知矿点就先去那里，比盲挖划算。

## 取矿方式

- 一般用 `maicraft:acquire_items` 指定矿物产物，交给 Mod 选来源。只找即时可见或见过的矿源；需要往下掘进探矿时必须有授权字段（`allow_prospecting`），授权语义读 `maicraft://knowledge/game_mechanics/mine-source-scope`。
- 掘进形状用斜向阶梯或水平隧道，不垂直直挖（`maicraft://knowledge/game_mechanics/tunneling`）。
- 深挖作业面默认没有照明，长时间作业前开启 `maicraft:auto_light` 或对工作区用 `maicraft:light_area`（`maicraft://knowledge/game_mechanics/lighting`）。

## 安全线

- 带足食物、备用镐和一桶水再下深层；生命低于一半先退回亮处恢复。
- 岩浆层附近不挖脚下与头顶的未知格，挖穿隔挡前看流向（`maicraft://knowledge/game_mechanics/fluid-flow`）。
- 砾石、沙子会塌，挖下方方块前留意（`maicraft://knowledge/game_mechanics/gravity-blocks`）。

## 收尾

- 原矿要熔炼：用 `maicraft:cook` 批量烧成锭，有高炉时偏好 blasting。
- 回据点存放或交付前核对背包实际数量；矿物掉率与时运会影响产量，几次不掉不是故障（`maicraft://knowledge/game_mechanics/drop-rates`）。

## 何时上报

找了两个以上方向仍没有目标矿、或镐等级不够且无法就地补足时，把已探区域、已有材料与卡点上报主播。
