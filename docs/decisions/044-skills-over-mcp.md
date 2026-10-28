# ADR-044：玩法技能随 Mod 发布，直播策略留宿主

- 状态：已采纳（2026-10-18）
- 日期：2026-10-18
- 实现提交：与本 ADR 同笔交付（`git log -- docs/decisions/044-skills-over-mcp.md`）

## 背景（Context）

ADR-036 把技能放在消费 Agent 一侧：玩法经验由 Amaidesu 的 `src/agents/minecraft/skills/` 沉淀，按能力清单筛进目录，正文由 `minecraft_skill` 按需读。当时的前提是 MaiCraft 只作为 Amaidesu 的游戏后端，经验沉淀在宿主一侧。

现在这个前提变了：MaiCraft v1 是一个独立的 MCP 服务器，任何支持 MCP 的客户端都可以连上来操作角色。同时，MCP 官方已经把 Skills extension（`io.modelcontextprotocol/skills`，SEP-2640）定稿：服务器可以把玩法技能作为资源发布，地址用 `skill://<路径>/SKILL.md`，客户端用 `resources/read` 读。玩法技能回答的正是"用这个服务器的能力把一类事做成"，与"能力怎么调"（能力说明）、"世界里的事实是什么"（知识库）并列。

原先的存放方式有三个问题：

- 技能与能力分在两个仓库：能力名、参数、知识地址改了，技能不会跟着改，只能靠人记得核对。ADR-036 自己也把"正文引用的能力名与知识 URI 随 Mod 演进可能过期"列进了代价。
- 宿主得先读回能力清单和模组清单，再自己决定哪些技能进目录；判断散在宿主，两个仓库各有一半。
- 独立客户端连上来拿不到这套玩法经验；MaiCraft 作为独立服务器是不完整的。

## 决策（Decision）

1. **玩法技能随 MaiCraft 发布**：阶段顺序、决策点、常见坑、该查哪份资料、许可怎么设、能力怎么组合，写成 `SKILL.md`，放在 `assets/maicraft/skills/<分类>/<名字>/SKILL.md`，由 `resources/list` 列目录、`resources/read` 读正文，地址 `skill://<分类>/<名字>/SKILL.md`。
2. **内容必须宿主中立**：不出现"主播""直播""观众""上报主播"，不出现宿主的工具名（`maicraft_lookup`、`minecraft_report`、`minecraft_notebook`）。引用工具用服务器自己的短名（`lookup`、`execute`、`goal`、`events`、`observe`），引用能力用 ID，引用事实用知识地址。
3. **直播策略留宿主**：什么时候上报、直播节奏、观众体验、什么时候问主播、人格与口吻、宿主工具的用法，写成 Amaidesu 本地的 `streaming_policy` 技能，不随 Mod 发布。
4. **目录在宿主合并，正文各按来源读**：宿主连上 MaiCraft 后读 `resources/list`，与本地技能合并，目录里标明来源；读远端正文走 `resources/read`，断线时撤掉远端、回退本地。
5. **门控由 Mod 做**：技能在 frontmatter 的 `metadata.requires-mods` 里写需要的联动模组；这些模组在本局都接上了才发布。宿主不再按能力清单筛玩法技能。不按能力门控：能力在进世界时才登记，MCP 服务在启动时就起来了，按能力筛会让启动时的目录是空的。
6. **同一篇只留一个权威位置**：九篇玩法技能从 Amaidesu 删除，不保留副本；宿主只留 `streaming_policy`。
7. **名字与格式跟 Agent Skills 规范**：`name` 用小写字母、数字、连字符，必须等于目录名；frontmatter 只认 `name`、`description`、`metadata` 三个顶层字段，`requires-mods` 放进 `metadata`（一行 JSON，值是字符串）。这样 `skill://` 资源同时符合 Agent Skills 与 MCP Skills extension 的格式要求。

当前不声明 `io.modelcontextprotocol/skills` 扩展：它要求 MCP `2026-07-28` 修订版与 `skills/list`、`skills/get`，而 MaiCraft 支持到 `2025-11-25`。在此之前按普通资源发布，支持 `resources` 的客户端都能读；等服务器升级协议后再补 `skills/list`、`skills/get` 与内容摘要。

## 替代方案（Alternatives）

- **继续放 Agent 侧**：拒绝。两个仓库的版本漂移是结构性的，不是靠提醒能解决的；独立客户端也拿不到。
- **两边各放一份**：拒绝。同一篇技能两个权威位置，迟早不一致；迁移后只留 Mod 这一份。
- **把玩法技能塞进知识库（`lookup(topic=knowledge)`）**：拒绝。知识库是"世界里的事实是什么"，随游戏与模组版本走；技能是"这类事怎么做成"，有阶段、决策与坑，混进去会让资料目录变成攻略目录，也让 `lookup` 的检索语义变糊。
- **现在就实现完整 Skills extension**：拒绝。要先把 MCP 基础协议升到 `2026-07-28`，再实现 `skills/list`、`skills/get` 与 SHA-256 清单；收益是客户端能识别"这是技能"、能校验内容，但当前客户端支持还很早，先把资源发布与内容迁移做完，协议升级另开工作包。
- **宿主继续按能力清单筛远端技能**：拒绝。Mod 已经知道本局能力与模组；宿主再筛一遍只会把同一份判断写两处，还会因为启动时能力未登记而筛空。

## 后果（Consequences）

- **收益**：玩法技能与能力同仓库、同版本发布，能力改动可以在同一个提交里改技能；任何支持 `resources` 的 MCP 客户端连上就能读；宿主只剩直播策略，技能目录不再依赖宿主猜模组。
- **代价**：玩法文档的修改要走 MaiCraft 的提交与发版，Amaidesu 不能再单独改；Mod 的发布节奏会约束文案更新。接受：玩法技能本来就跟能力版本绑定。
- **边界**：不支持 `resources` 的客户端拿不到技能；在协议升级前，实现 Skills extension 的客户端只会把它们当普通资源，不会当"技能"识别。技能正文仍是写给模型的建议，不是执行逻辑，也不授予权限。
- **审阅**：技能内容是人工写的，代码只能校验格式与门控；能力名、参数名、知识地址是否过期仍需在改动能力时对照审阅。
- **宿主行为变化**：Amaidesu 的技能目录由本地 + MaiCraft 远端合并而成；`minecraft_skill` 读远端时经 `resources/read`，正文去掉 frontmatter 后交给模型；MaiCraft 没连上时远端技能消失，本地直播策略照常可用。
