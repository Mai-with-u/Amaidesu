# ADR-038：Agent 附属采集器——只对某个 Agent 有意义的流随它一同开关

- 状态：已采纳（2026-10-11）
- 日期：2026-10-11
- 实现提交：`3287565dfcdb150a143331f3339d9e0ca94c8d3a`（附属采集器随所属 Agent 开关）

## 背景（Context）

采集器原先只有一种装配方式：`collectors.toml` 的 `enabled` 名单驱动，生命周期挂装配期、不随 Agent 起停（ADR-018 后果一节把它记为"待机时该流是否还在"的有意分工）。Minecraft 的身体事件流 `maicraft_attention` 与聊天流 `maicraft_chat` 也走这条路，只是代码放在 Minecraft Agent 包内。

场次 21 直播实锤了这种分工的代价：主播整场几乎感知不到角色挨打、自卫和死亡。原因是主持人的 `collectors.toml` 没启用 `maicraft_attention`——Minecraft Agent 开着，给主播送身体事件的那条流却没开，系统不报错，只是主播侧安静。反过来，只开主播、不开 Minecraft 时，默认启用的这两条流又会对着连不上的 Mod 一直退避重试。两个名单各开各的，而这两条流离开 Minecraft Agent 并没有独立存在的意义。

另一个重复：两条流的连接参数在 `collectors.toml` 里另抄一份，注释写着"与 `[agents.minecraft.mcp].url` 保持一致"，请求头等字段则根本没有，Agent 改了地址或加了鉴权，流就静默连不上。

## 决策（Decision）

**采集器分两类：通用采集器照旧由 `collectors.toml` 单独开关；只对某个 Agent 有意义的流是该 Agent 的附属采集器，随它一同开关，配置由它的配置段换算。**

- **通用采集器**：直播间与操作者的输入（B 站弹幕、控制台、语音转写），装配走采集器工厂与 `collectors.toml`，不变。
- **附属采集器**：仍是 `BaseCollector`（持续流、自产自发事件、登记在 `CollectorManager` 里），只是"装配期"从采集器名单换成了所属 Agent 的启用：
  - Agent 包声明并构造（`src/agents/<名>/companions.py`），配置从 Agent 配置段换算：Minecraft 的两条流连接直接用 `[agents.minecraft.mcp]`（地址、传输方式、请求头、超时一份），等待与重试间隔取 `events_wait_ms` / `events_retry_interval_ms`；
  - 框架侧登记表 `src/modules/agents/companions.py`：Agent 注册名 → 附属采集器名单，以及按注册名分发到 Agent 包构造函数。它和 Agent 工厂是同一类装配登记表，属于 ADR-018 允许出现游戏名的位置；面板、开关拦截、启动装配都只查这张表，不出现具体游戏名；
  - 不进 `collectors.toml`、采集器工厂与配置注册表。
- **开关只看"所属 Agent 在不在 `agents.enabled`"**：
  - 启动装配时，给每个启用的 Agent 装上它的附属采集器，与通用采集器一起 `start_all`；
  - Dashboard 启用、停用 Agent 时一并启停它的附属采集器；单独开关附属采集器被拒绝，提示去开关 Agent；
  - Agent 的重启与心跳自愈走重建，**附属采集器不动**：这些流每次重新接入只对齐游标、不补发，跟着重建就会漏掉重建期间的事件——"主播的叙事不随游戏 Agent 的忙碌与重建断档"这条原有理由依然成立，只是边界从"永远独立"收窄到"独立于 Agent 实例、跟随 Agent 启用"。
- **迁移**：`collectors.toml` 升 2.0.43，单文件钩子从 `enabled` 与段里搬走 `maicraft_attention`、`maicraft_chat`；段里改过的地址、重试间隔（不等于旧默认）记警告，写明今后在 `agents.toml` 哪一项设置。只改本文件，不跨文件改 `agents.toml`（各文件版本流独立）。
- **展示**：组件清单的 `ComponentSummary` 多一个 `owner` 字段，附属采集器写所属 Agent，通用采集器与 Agent 为空；所属 Agent 在 `agents.toml` 里有段才列出。

## 替代方案（Alternatives）

- **保持独立名单，只把这两条流默认启用**（原状）。否决：默认值管不住已有配置，场次 21 的配置就是没开；开着 Minecraft 却没开它的流、或没开 Minecraft 却开着它的流，两种错配都是静默的。
- **让游戏 Agent 自己在 `start()` 里起这两条流**。否决：流的生命周期会绑在 Agent 实例上，Agent 重建、自愈时跟着断，正是原先把它做成采集器要避免的；而且采集器的运行状态就不再出现在组件管理里。
- **在 `collectors.toml` 里给采集器加一个"依赖某 Agent"的字段**。否决：依赖关系是代码事实（这条流读的就是这个 Agent 的游戏），不该交给用户配置去对；名单与段仍会存在，错配的可能没有消失，只是多了一层校验。
- **框架里按 Agent 名写分支启停**（散落在装配、面板各处）。否决：违反 ADR-018；收进一张登记表后，框架其余位置只查表。

## 后果（Consequences）

收益：开 Minecraft Agent 就有身体事件与聊天流，不开就不空转；连接只配一处，Agent 加鉴权头、改地址，流自动跟上；场次 21 这类"Agent 开着、流没开"的错配不再可能。

代价与需持续关注：

- **新 Agent 带附属采集器**要改两处：Agent 包里写构造函数，`src/modules/agents/companions.py` 加一行名单与一个分支（名单与构造出的实例名一致由测试守住）。
- **ADR-018 后果一节"采集器的启停是独立名单"**只对通用采集器成立；游戏相关的流改由本 ADR 约束。
- **Dashboard 前端**暂未使用 `owner` 字段：附属采集器的开关按钮点了会收到拒绝提示，前端可按 `owner` 把按钮换成"随 Agent 开关"的说明。
- **Agent 的 MCP 关着**（`mcp.enabled=false`）时不装它的附属采集器：Agent 本身不接游戏，流也没有意义。
