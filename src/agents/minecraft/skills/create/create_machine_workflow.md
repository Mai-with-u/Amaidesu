---
name: create_machine_workflow
description: 机械动力机器从需求到产出：选工艺、查机器资料、写机器蓝图、先审再建、接动力、试运行验收，照着现成的机器再建一台或改一台；要造压机、搅拌、粉碎、鼓风机加工等装置时读
agents: [minecraft]
category: create
requires:
  abilities: [maicraft:machine_inspect, maicraft:machine_review, maicraft:machine_build, maicraft:machine_connect, maicraft:machine_configure, maicraft:machine_run]
  mods: [create]
tags: [机械动力, 机器, 蓝图, 验收]
---

# 机械动力：机器从需求到产出

主播说“做一台机器出某某”时，真正的目标是**产物**，机器是手段。整条链路是：工艺 → 资料 → 蓝图 → 施工 → 动力 → 试运行，每一段有自己的证据，前一段成功不能冒充后一段：结构和蓝图一致不等于能转，能转不等于出货。

机器的事分给六个能力：`machine_inspect` 看（只读）、`machine_review` 审蓝图（只读）、`machine_build` 建、改、拆，`machine_connect` 接网络，`machine_configure` 改设置，`machine_run` 用机器做东西。没有在后台盯着产线的能力，也没有现成的布局模板：机器怎么排由你设计，要知道产了多少就过一会儿再看一次。

## 先定工艺

- 从产物出发查配方：`lookup(topic=recipe, id=产物 ID)` 看它由哪种加工得到（压制、搅拌、粉碎、切割、鼓风机洗涤/烟熏/熔炼、机械手、序列组装……）；不知道 ID 先 `lookup(topic=recipe, query=名字)`。
- 背包、箱子或 AE 里已有成品时先问自己：目标是不是“用机器做出来”？是的话现成品不算交付；只是要东西就直接 `obtain`。
- 只做几件（压几块板）用置物台加手摇曲柄就够，不为此搭一条长期产线。

## 查机器资料

- 先读总览 `lookup(topic=knowledge, id=maicraft://knowledge/machine/create/index)`：认得哪些机器、接动力的经验、放方块的讲究（带漏斗压在传送带上、横着的齿轮箱怎么放、动力辊下面要空着）。
- 每种机器一篇，地址列在总览里：加工处在哪、从哪面接转动、要多快才干活、每转吃多少应力、`machine_run` 时料放哪、产物从哪收、传送带和机械臂这类安装段怎么写。物品资料页（例如 `lookup(topic=knowledge, id=create:mechanical_press)`）也挂着这一篇和对应的思索。
- 思索（Ponder）只补当前设计缺的那一点，不为确认名字遍历全部教程；里面的创造马达、物品保险库是“这里接动力、这里接料”的示意，不照搬。

| 加工 | 核心部件 | 要点（以机器资料为准） |
| --- | --- | --- |
| 压制（板材） | 动力压机 + 下方台面 | 压机压正下方第二格：置物台、传送带或工作盆 |
| 搅拌、合金 | 动力搅拌器 + 工作盆 | 搅正下方第二格的工作盆；要加热的配方在盆下面放烈焰人燃烧室 |
| 粉碎 | 一对粉碎轮 | 两轮转向相反才吃料，做好的东西掉在地上 |
| 切割 | 动力锯 | 锯齿朝上才加工 |
| 鼓风机批量加工 | 鼓风机 + 介质 | 水洗涤、火烟熏、岩浆熔炼，物品要经过气流里的介质 |
| 机械手、序列组装 | 机械手 + 传送带或置物台 | 机械手作用在朝向前方第二格；序列组装按配方顺序多步 |

## 写蓝图，先审再建

- 选址：`observe(what=scene, grid=true)` 看地方够不够、平不平；离玩家的东西远一点，碰到会停下来问。选好用 `remember` 记个名字当锚点。
- 机器蓝图就是 `machine_build` 的 `blueprint`：`cells` 逐格写（`offset`、`block`、可选 `properties`；`minecraft:air` 是清空，水源、岩浆源在实心格之后倒桶），`installations` 写只能用模组自己的方式装的段（传送带两端写轴、链条输送机写两台、机械臂写取放点、机械合成器连入口），`parts` 写装在某一面上的部件，`settings` 写装好后的设置，`processes` 写声明的工序（只给审阅用）。安装段每种写法不同，照那种机器的资料写。
- 主播手搭过同款机器、想照着再建一台：`machine_inspect(operation=read_site, target=那台的一格)` 读成蓝图，`site.blueprint` 原样喂给 `machine_build(target=新地方, blueprint=…)`。箱子里的东西和要开界面才看得见的设置不抄，建完再配。
- 建之前 `machine_review(blueprint=…)` 审一遍：`issues` 看工序做不做得了、动力连不连得上，`materials` 看要多少料、身上缺多少，`power` 看应力账（只是估计）。审阅不拦施工，报了问题照样能建，所以问题要自己改掉再下达。
- 蓝图写错的地方一次报全（`INVALID_PARAMETER`）：按定位改那几处，不整份重写。

## 施工

- `machine_build(blueprint=…, target=锚点, name=档案名)`：缺料自己拿、挡着的挖掉、够不着垫临时方块，建成后记进机器档案。施工期间身体在忙，你去想动力方案。
- 读结果：动作做成没有（`changes`）和结构对不对（`check`）分开给；`installations[]`、`parts[]`、`settings[]` 每项各自做成没有，没做成的带原因。材料用完以 `NEED_ITEM` 停下，补齐后**再下达一次同样的** `machine_build`，会从没建完的地方接着做。
- 改这一台：`machine_build(target=档案名, blueprint=补丁)`，补丁和原蓝图同一个锚点，新声明的格覆盖旧的，范围外的保留；不要的零件在补丁里写成 `minecraft:air`。不从零重建。
- 换方案留下的旧东西顺手收拾：整台不用了 `machine_build(operation=remove, target=档案名)`；拆之前先 `machine_inspect` 看那几格还连没连着正在用的机器，拆下来还能用的料 `deposit` 存回去。

## 接动力、配设置

- 动力按《机械动力：动力网》接：`machine_connect(network=kinetic)` 的回执分开看 `joined`（接上没有）和 `running`（转不转）。
- 漏斗、智能溜槽的过滤格，转速控制器的数值，这类方块上画着设置框的用 `machine_configure` 改；能写哪些键先 `machine_inspect` 看这台机器。过滤器、属性过滤器的规则写在物品自己身上：先 `machine_inspect(item=create:filter)` 看，再 `machine_configure(item=…, settings=…)` 改。

## 试运行与验收

- `machine_run(target=机器的一格, item=产物, count=几件)`：备料、投料、开机、等产物、收产物，一台机器一次；一条线有几台就下几次，或用 `sequence` 排。
- 读 `fed`（投进去多少）、`produced`（这次新出几件）、`collected`（拿回背包几件）、`machine`（结束时的读数）。交付以**真实产物进背包或容器**为准。
- 等不到产物时读数会说原因：转速 0、过载就回去查动力网。结果说出口读不到（粉碎轮、锯做好的东西掉在地上，石磨的产物留在肚子里）时，到出口那边找，或 `use` 打开它看。
- 产出偏离预期：`machine_inspect(target=档案名)` 看结构和档案差在哪、哪台不转，改一处再试，不整机推倒。

## 常见坑

- 只看结构对得上就宣布机器能用：以 `running` 读数和真实产物为准。
- 一台机器里链条输送机的个数有上限（实例配置，默认 1），超了下达时就报错：近处用传动杆和齿轮箱。
- 带漏斗、隧道压在传送带上：照常写进 `cells`，施工会先装好传送带再放它们。
- 别人的机器能看；对方点了头才投料、开关、拿产物、拆改。

## 何时上报

工艺依赖的物品或部件拿不到，或连续两种不同布局都无法产出时，带上已试的布局、`machine_run` 的读数与卡点上报主播。
