# 019：ToolRegistry Provider 常驻登记与工具集刷新（工具页注册表驱动）

状态：已采纳
日期：2026-09-18
实现提交：3a314ff37542615c3ee08043956c5c23079a972e

## 背景

三条真实痛点指向同一结构性缺陷：

- 工具页提供者卡片由静态硬编码成员表（`_PROVIDER_MEMBERS` / `_AGENT_CATEGORIES`）+ `[tools]` 配置段驱动，注册表只负责"往已存在的卡片里填计数"。运行时实际注册的 provider 不在表里即不可见——minecraft 的 4 个工具已注册而侧边栏全 0 即是事故形态。
- Agent 私有 MCP（[agents.minecraft.mcp]）的装配失败路径是终态：连接失败 → 关客户端、丢引用、不注册。server 后来起来了也不会恢复；手动重连 API 因 provider 未注册而 404；唯一恢复手段是重启整个应用。
- MCP 的工具清单是连接时预拉缓存：即便 provider 已注册，server 侧清单变化也不会反映到 registry。

## 决策

1. **Provider 常驻登记**：`register_provider` 对 0 工具 Provider 照常登记（分类落账、`list_providers` 在列）。连接失败从"未注册的静默"变为"已登记 0 工具"的可见降级态，不再 close-and-drop。
2. **工具集刷新**：registry 新增 `refresh_provider_tools`（换血语义：摘旧条目 → 按 provider 当前 `list_tools()` 重登记；存续工具保留"熔断待探活"，消失工具连带清健康状态）。`reconnect_provider` 成功后先刷新再探活复位，报告携带 `refreshed`。
3. **可见名单来源化**：`visible_to` 接受静态 dict 或 `(specs) -> dict` 策略 callable，registry 保存来源；刷新时策略对新工具集重跑。fail-closed 名单必须走策略形态——否则刷新补注册的新工具落"未列出 = 全员"默认，对所有 Agent 泄露可见性。
4. **装配失败常驻重试**：MinecraftAgent 私有 MCP 装配失败 → 降级登记 + 后台退避重试（5s 起步 60s 封顶，对齐 MaicraftAttentionCollector 的"Mod 没开是常态"失败语义）。任务查询 / attention 适配器绑定经 `on_tools_refreshed` 回调统一在三条路径（初次装配 / 恢复重试 / 手动重连）触发，幂等。
5. **工具页注册表驱动**：categories 端点卡片 = 注册表 `list_providers`（事实源）∪ 配置声明态（enabled=false 未装配也可见）∪ 已知成员发现表（首次可开启）；分类 = provider 自声明 category 动态发现 + 固定词表排序。提供者开关按 `switch_config` 声明路由写回目标文件（Agent 私有 MCP → agents.toml），不再依赖成员表静态映射。
6. **MCP reconnect 语义升级**：断开 → 重连 → 重拉清单换血；连上但清单为空视为重连未成。

## 替代方案

- **保留静态成员表并补 minecraft / maicraft 条目**：继续两套事实源漂移，每接一个新游戏 / 新 MCP 都要改 dashboard 代码，违背"注册表即事实源"。弃。
- **连接失败仅起重试循环、不降级登记**：server 长期不在时工具页仍看不到该 provider（不知道有个 maicraft 在重试），也无法从面板手动重连。弃。
- **刷新时沿用注册期名单快照**：新出现的 MCP 工具落"未列出 = 全员"默认，fail-closed 泄露。弃。
- **采集器复用 Agent 的 MCP 连接以消除双连接**：注意流是持续流数据源，生命周期必须独立于游戏 Agent 的重建 / 停机（组件判据与 ADR-009 既定）。维持双连接，以卡片 `notice` 明示停用语义。弃。

## 后果

- 正面：连接失败从静默终态变为"可见、可重连、自愈"；工具页与注册表单一事实源，新游戏 / 新 MCP 零 dashboard 改动；fail-closed 名单在动态工具集下保持成立。
- 代价：registry 增加 per-provider 登记状态（`_visible_to_source`）与刷新路径及其测试面；工具页卡片为三源并集，"待重启 / 连接失败"两种零工具态的区分依赖 `registered` / `degraded` 字段；game / framework 分类在无注册表记录时显示为空分类（诚实反映运行态）。
- 中性：Agent 私有 MCP 停用只卸工具面，身体事件采集器仍独立连接（卡片 notice 明示）——双连接是采集器判据的既定设计，本 ADR 不改变。
