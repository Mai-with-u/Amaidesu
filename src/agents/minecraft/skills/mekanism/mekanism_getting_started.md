---
name: mekanism_getting_started
description: 通用机械起步：锇矿、钢与合金、钢制机壳、第一批电力与基础机器，机器的侧面配置怎么看怎么改，先手动投料跑通再接管道；第一次接触 Mekanism 或要造任何 Mek 机器前读
agents: [minecraft]
category: mekanism
requires:
  abilities: [maicraft:obtain, maicraft:machine_build, maicraft:machine_inspect, maicraft:machine_configure, maicraft:machine_connect, maicraft:machine_run]
  mods: [mekanism]
tags: [通用机械, 起步, 电力]
---

# 通用机械（Mekanism）起步

Mekanism 的机器几乎都要 **钢制机壳** 和 **电力**。起步就是把这两样打通，之后的机器只是换配方。

## 起步路线

1. **锇**：锇矿是主世界常见的蓝灰色矿石。`obtain(item=mekanism:raw_osmium)` 只挖看得见的矿：模组矿不在长程挖矿的高度表里，附近没有露出来的就先 `travel` 去洞穴、峡谷找。要锇锭直接要 `mekanism:osmium_ingot`，角色会自己去烧。
2. **冶金灌注机**：最先造的机器，用来做钢（铁 + 碳）与灌注合金（铜 + 红石）。它本身靠电，电力要和它一起准备。
3. **钢制机壳**：由钢、锇与玻璃合成，是基础机器的通用外壳，批量做几个备用。
4. **基础机器**：富集仓（矿物 2 倍）、电力冶炼炉、粉碎机，按当前需求挑，不一次全造。

`obtain` 只会合成、烧炼、切石，不会替你开机器：钢、合金这类要灌注机做的，用 `machine_run` 做出来再合成下一步。看配方先 `lookup(topic=recipe, id=物品 ID)`，这台机器能做什么用 `lookup(topic=recipe, id=机器 ID, uses=true)`；整合包常改配方，以查到的为准，不凭记忆硬做。

## 电力从哪来

- **Mekanism 本体没有常规发电机**，发电机在附属模组 Mekanism Generators（命名空间 `mekanismgenerators`）。装了它就从热力发电机（烧燃料，挨着岩浆也会慢慢发电）、太阳能、风力起步。发电机的界面不在认得的机器界面里，往里加燃料用漏斗或管道从箱子送，太阳能、风力不用加燃料最省事。
- 没装发电机附属时，找整合包里其他模组的 FE 发电方式，或接入现场已有、主播说过能用的电网。
- 能量立方缓存电力，通用线缆连接：`machine_connect(target=机器, network=energy)` 会用基础线缆接到附近的能量网。机器要有能进电的一面（侧面配置不是 `none`）。
- 线缆里流着多少电读不到，回执只数得出几段线、连着几台机器。机器不干活时 `use` 打开它的界面看电量是不是空的。

## 机器的面

- 每台机器每一面、每种介质有一个模式：`none` 不通、`input` 进、`output` 出、`input_output` 进出都行，还有 `input_1`、`output_2`、`energy`、`extra` 这些；这台机器能选哪几种看它报的可选值。
- 先 `machine_inspect(target=机器的一格)` 看它现在每一面是什么，再决定改不改。
- 改用 `machine_configure(target=机器, settings={"side.north.item": "output"})`：键是 `side.方向.介质`（方向 north / south / east / west / up / down，介质 item / fluid / energy），值写模式名。角色手持配置器潜行点那一面，一下换一种，读回对上为止；身上没有配置器会先去拿。化学品与热的面只能看不能改。
- `side.north` 是世界的北面，不是机器的正面：改之前对着 `machine_inspect` 的读数确认朝向。
- 机器资料：`lookup(topic=knowledge, id=maicraft://knowledge/machine/mekanism/index)`，列着认得的机器、侧面配置、物流分拣器、投料和管网的写法。

## 先手动跑通，再接管道

- 机器放好（机器、能量立方、线缆写进机器蓝图用 `machine_build` 一起建，或手头已有就直接用）、接上电后，`machine_run(target=机器, item=产物, count=几件)` 手动投一批：角色走过去点开界面，把料放进收它的格。格子在界面里，站在外面看不到出口，所以投完就收手；做好没有用 `use` 打开看，或 `obtain` 从里面拿。
- 灌注物（碳、红石）在灌注机里是化学品：`machine_run` 只投配方里的物品主料，`notes` 会写明化学品带不进去、看机器里还有没有。先把煤或红石放进灌注机的附加格（`deposit` 点名这台机器；认得的机器界面才会放，认不出会以 `UNSUPPORTED` 说明），再投主料。
- 跑通了再做自动化：物流传输管道送物品、机械管道送流体、加压管道送化学品，贴着机器能收发的那一面就连上。`machine_connect` 能接 energy、fluid、item 三种；化学品的加压管道它不接，写进机器蓝图用 `machine_build` 铺。

## 常见坑

- 只造了机器忘了电：机器不工作先看电量是不是空的。
- 灌注机里的灌注物和主料是两个槽，缺任何一种都不产出。
- 机器那一面配成 `none`，管道贴着也接不上。
- 富集仓、粉碎机、冶金灌注机、锇压缩机、能量立方、线缆管道、物流分拣器之外的 Mek 机器按“其他 Mekanism 机器”处理：侧面照样能看能改，说不出它是什么机器。

## 何时上报

整合包里找不到任何可用的发电方式、或关键原料（锇等）在可达范围内找不到时，上报主播。
