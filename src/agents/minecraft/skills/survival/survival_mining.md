---
name: survival_mining
description: 采矿：为铁、煤、钻石等矿物找暴露的矿点、取矿与烧炼，以及下到深处的安全线；需要成批矿物或第一次下深层时读
agents: [minecraft]
category: survival
requires:
  abilities: [maicraft:obtain, maicraft:travel]
tags: [采矿, 铁, 钻石]
---

# 采矿：铁与钻石

采矿要的是“稳定拿到足量矿物并活着回来”，不是挖出最深的洞。

## 先定目标数量与深度

- 先说清要什么、要多少：铁通常 1～2 组起步，钻石按用途算（镐 3、全套装备 24）。
- 深度参考（1.18 以后的分布）：铁在 Y≈16 附近较多，高山 Y≈232 也有；钻石在 Y≈-58 一带最多；深板岩层更慢挖，需要更好的镐。

## 取矿方式

- 用 `obtain` 要矿物或它的产物（`minecraft:raw_iron`、`minecraft:diamond`）。它只挖**看得见或见过**的矿，不会凭空往地下打洞找矿；附近没有时会如实说给不了。
- 所以先找暴露的矿：开局资料里的周围先看一眼；洞穴、峡谷、山体裸露面最容易看到矿，用 `travel`（方向加距离）过去，再看周围。
- 要铁锭就直接要铁锭：角色会先拿粗铁再去烧，缺镐、缺燃料、缺熔炉都会先自己补。
- 手上的镐不够格时，`obtain` 会先备工具；要一次挖一格指定的方块用 `gather`，它不会自己备工具，缺什么会写清楚。

## 安全线

- 带足食物、备用镐再下深处；生命低于一半先退回亮处恢复。
- 岩浆附近不挖脚下与头顶的未知格，挖穿隔挡前看流向（`maicraft://knowledge/game_mechanics/fluid-flow`）。
- 砾石、沙子会塌，挖下方方块前留意（`maicraft://knowledge/game_mechanics/gravity-blocks`）。
- 以上资料用 `maicraft_lookup`（`topic=knowledge`）按 `id` 读。

## 收尾

- 回据点用 `deposit` 存放，或交付前核对背包实际数量；矿物掉率与时运会影响产量，几次不掉不是故障（`maicraft://knowledge/game_mechanics/drop-rates`）。

## 何时上报

换了两个以上方向仍看不到目标矿、或镐等级不够且无法就地补足时，把已去过的地方、已有材料与卡点上报主播。
