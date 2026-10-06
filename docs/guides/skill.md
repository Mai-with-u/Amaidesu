# Skill 编写与接入

Skill 是 Agent 的玩法/操作经验文档：回答"一类目标该怎么做成"。它与能力契约（某个能力怎么调）、外部知识库（世界里的事实是什么）分工，定位与取舍见 [ADR-036](../decisions/036-skill-system.md)。

## 什么内容该写成 Skill

| 写进 Skill | 不写进 Skill |
|------|------|
| 阶段顺序与每段的完成标准 | 能力参数逐项说明（读能力契约） |
| 决策点：什么情况选哪条路 | 配方、掉率、方块属性等数据（读知识库） |
| 常见坑与排查顺序 | 身份、行为准则、上报规则（系统提示词） |
| 何时查哪份资料、要先拿哪些授权 | 某个整合包的一次性现场信息（notebook） |
| 何时上报主播 | |

判据：换一个世界、换一局直播仍然成立的打法写成 Skill；随 Mod 版本变化的事实留在 Mod，Skill 只引用它的 URI。

## 文件位置与格式

技能文档放在消费方包内的 `skills/` 目录（如 `src/agents/minecraft/skills/<分类>/<名字>.md`），由 [`SkillLibrary`](../../src/modules/skills/library.py) 按 `src/**/skills/**/*.md` 约定自动发现。该目录下每个 `.md` 都会被当作一项技能加载，不要放 README 等其他 Markdown。

```markdown
---
name: create_power_network
description: 机械动力的动力网：选动力源、估应力与转速、接入或自建；任何 Create 机器需要转起来时读
agents: [minecraft]
category: create
requires:
  mods: [create]
tags: [机械动力, 应力]
---

# 正文……
```

字段的权威定义见 [`SkillMetadata`](../../src/modules/skills/models.py)，要点：

- `name`：全局唯一，小写字母/数字/下划线；模型读取时就填这个名字。
- `description`：一句话，**先说是什么，再说什么时候读**——它是目录里唯一的信息，决定模型会不会去读。
- `agents`：受众 Agent 注册名，或单独的 `"*"`。
- `category`：目录分组（Minecraft 现有 `survival` / `create` / `mekanism` / `progression`）。
- `requires`：环境前提，类别由消费 Agent 定义。Minecraft 用 `mods`（模组编号，如 `create`、`mekanism`、`ftbquests`）；确认没装时技能不进目录，未知时标注待确认。

未知字段、缺必填字段、空正文、名字重复都会让加载失败，问题在启动时暴露。

## 正文写法

- 面向模型写操作建议，用第二人称、短句、表格；控制在一屏到两屏。
- 推荐结构：目标与完成标准 → 阶段/步骤 → 决策点 → 常见坑 → 该查的资料 → 何时上报。
- 引用能力用 ID（`maicraft:acquire_items`），引用工具用全名（`maicraft_perceive(view=...)`），引用事实用知识 URI（`maicraft://knowledge/...`）。写之前对照当前 Mod 的能力契约核对字段名。
- 数值类事实（配方比例、掉率、应力数值）不写死，指向知识库或配方查询。
- 遵守游戏 Agent 的玩法守则（不用管理员命令、不用创造模式物品），需要授权的动作写明"先确认主播意图"。

## 让新 Agent 接入

1. 构造器接收 `skill_library: SkillLibrary | None`，由 Agent 工厂注入 `get_skill_library()`。
2. 在自己的工具提供者里声明 `build_skill_spec(<provider>)`，可见名单只填自己；调用转发到 `library.read(name, <自身注册名>, <环境事实>)`。
3. 系统提示词末尾附 `render_catalog(library.catalog(<自身注册名>, <环境事实>))`，目录为空时整段省略。
4. 有环境前提时提供环境事实：只填**已确认**的类别，未知类别不要填空集合。

Minecraft 的接入实现见 [`MinecraftAgent`](../../src/agents/minecraft/agent.py)（技能段）与 [`environment.py`](../../src/agents/minecraft/environment.py)。

## 相关文档

- [ADR-036](../decisions/036-skill-system.md) - Skill 系统决策
- [提示词管理](prompt.md) - 同构的提示词模板机制
- [Minecraft Agent](../architecture/minecraft-agent.md) - 游戏 Agent 工具契约
