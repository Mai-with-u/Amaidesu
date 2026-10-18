# Skill 编写与接入

Skill 是写给模型的玩法/操作经验文档：回答"一类目标该怎么做成"。它与能力契约（某个能力怎么调）、外部知识库（世界里的事实是什么）分工，定位与取舍见 [ADR-036](../decisions/036-skill-system.md) 与 [ADR-044](../decisions/044-skills-over-mcp.md)。

从 ADR-044 起，技能分两处：

| 内容 | 放哪 | 怎么读 |
| --- | --- | --- |
| 阶段顺序、决策点、常见坑、该查哪份资料、许可怎么设、能力怎么组合 | MaiCraft 随包发布 | 服务器 `resources/list` 给目录，`resources/read` 读 `skill://<分类>/<名字>/SKILL.md` |
| 什么时候上报、直播节奏、观众体验、什么时候问主播、人格与口吻、宿主工具 | Amaidesu 本地 | `src/**/skills/**/*.md`，`minecraft_skill` 按名读 |

玩法技能不再在 Amaidesu 仓库里重复；宿主只在目录里合并两边，并标明来源。

## 什么内容该写成 Skill

| 写进 Skill | 不写进 Skill |
|------|------|
| 阶段顺序与每段的完成标准 | 能力参数逐项说明（读能力说明） |
| 决策点：什么情况选哪条路 | 配方、掉率、方块属性等数据（读知识库或配方查询） |
| 常见坑与排查顺序 | 身份、行为准则、上报规则（直播策略） |
| 何时查哪份资料、要先拿哪些授权 | 某个整合包的一次性现场信息（notebook） |
| 何时停下来、把现场交给调用方 | 直播节奏与观众体验（宿主策略） |

判据：换一个世界、换一局直播仍然成立的**玩法**写成 MaiCraft 技能；只对某个主播、某档直播成立的**策略**写成 Amaidesu 本地技能。

## 玩法技能（MaiCraft）

文件在 MaiCraft 仓库：

```text
common/src/main/resources/assets/maicraft/skills/index.txt
common/src/main/resources/assets/maicraft/skills/<分类>/<名字>/SKILL.md
```

`index.txt` 一行一个技能，写 `<分类>/<名字>`；没写进去的文件不发布。加一项玩法技能 = 加一个目录、在索引里加一行，MaiCraft 与 Amaidesu 的代码都不用改。

```markdown
---
name: create-machine-workflow
description: 机械动力机器从需求到产出：选工艺、查机器资料、写机器蓝图、先审再建、接动力、试运行验收；要造 Create 机器时读
metadata: {"requires-mods": "create"}
---

# 正文……
```

- `name`：小写字母、数字、连字符，1–64 字，必须等于目录名。
- `description`：1–1024 字，先说是什么、再说什么时候读；它是目录里唯一的信息。
- `metadata`：一行 JSON，值只能是字符串；`requires-mods` 是逗号分隔的模组 ID，本局这些联动都接上了才发布。没有前提时整段省略。
- 正文必须宿主中立：不出现"主播""直播""观众""上报主播"，不出现 `maicraft_lookup`、`minecraft_report` 这类宿主工具名；引用工具用服务器的短名（`lookup`、`execute`、`goal`、`events`、`observe`），引用能力用 ID（`maicraft:obtain`），引用事实用知识地址（`maicraft://knowledge/...`）。
- 数值类事实（配方比例、掉率、应力数值）不写死，指向知识库或配方查询。

格式不合规、正文为空、名字与目录不一致、地址重复，MaiCraft 启动时就失败，不会把坏技能发给客户端。

## 本地直播策略（Amaidesu）

文件在 `src/agents/minecraft/skills/<分类>/<名字>.md`，由 [`SkillLibrary`](../../src/modules/skills/library.py) 按 `src/**/skills/**/*.md` 自动发现。现有 `streaming/streaming_policy.md` 是唯一一篇，写上报、节奏、观众体验与宿主工具。

```markdown
---
name: streaming_policy
description: 直播与上报：什么时候向主播交付或上报、直播节奏与观众体验、什么时候问主播、宿主工具怎么用
agents: [minecraft]
category: streaming
tags: [直播, 上报]
---

# 正文……
```

- `agents`：受众 Agent 注册名，或单独的 `"*"`。
- `category`：目录分组。
- `requires`：本地策略一般不用；要按环境筛时，类别由消费 Agent 定义。
- 本地技能与远端技能同名时保留本地，记一条告警；服务器的技能不能静默顶掉宿主策略。

## 宿主怎么合并

1. 连上 MaiCraft 后读一次 `resources/list`，只取 `skill://` 资源；分类取地址第一段。
2. 用 `SkillLibrary.replace_remote("MaiCraft", entries)` 登记，目录里标"来自 MaiCraft"。
3. 模型按名读时，本地技能直接读；远端技能按 `uri` 走 `resources/read`，去掉 frontmatter 后交给模型，结果里带 `source: "MaiCraft"`。
4. 连接断开时 `clear_remote("MaiCraft")`，远端技能消失，本地策略照常可用；重连后重新登记。
5. 技能段里说明：正文里的 `lookup`、`execute`、`goal`、`events`、`observe` 对应本机的 `maicraft_lookup`、`maicraft_execute`、`maicraft_goal`、`maicraft_events`、`maicraft_observe`。

## 让新 Agent 接入

1. 构造器接收 `skill_library: SkillLibrary | None`，由 Agent 工厂注入 `get_skill_library()`。
2. 在自己的工具提供者里声明 `build_skill_spec(<provider>)`，可见名单只填自己；调用转发到本地库，远端技能由自己的 MCP 客户端按 `uri` 读。
3. 系统提示词末尾附 `render_catalog(library.catalog(<自身注册名>, <环境事实>))`，目录为空时整段省略。
4. 有远端技能时，连上对应的 MCP 服务器后登记目录；断开时撤掉。

Minecraft 的接入实现见 [`MinecraftAgent`](../../src/agents/minecraft/agent.py)（技能段与 `_ensure_remote_skills`）。

## 相关文档

- [ADR-036](../decisions/036-skill-system.md) - Skill 系统与目录/正文机制
- [ADR-044](../decisions/044-skills-over-mcp.md) - 玩法技能随 Mod 发布，直播策略留宿主
- [Minecraft Agent](../architecture/minecraft-agent.md) - 游戏 Agent 工具契约
