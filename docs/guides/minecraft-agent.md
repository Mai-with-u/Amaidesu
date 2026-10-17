# Minecraft Agent（minecraft）使用指南

`minecraft` 是一个**委派型**游戏 Agent（AI 玩家）：主播用自然语言交代目标（"先建个庇护所"），它自己拆解步骤、调 Mod 工具执行、做完上报总结。它有自带的决策 LLM，空闲时零消耗。与遥控型游戏 Agent（文字冒险 / 2048，主播直接调工具操作）的分工见 [ADR-043](../decisions/043-game-agent-interaction-shapes.md)；任务生命周期与终止语义的设计细节见 [MinecraftAgent 设计](../architecture/minecraft-agent.md)。

## 前置清单

1. **Minecraft + MaiCraftMod 就绪**：安装 [MaiCraftMod](https://github.com/Mai-with-u/MaiCraftMod) 并进入世界。Mod 内置 MCP 服务端，本机默认端点 `http://127.0.0.1:8766/mcp`。
2. **填决策模型**：`config/model.toml` 的 `minecraft` 档位填入模型——这是 AI 玩家自己的决策档位（与主播的 `planner` / `replyer` 同理，用途名由代码固定）。不填的话委派下去无法推理。
3. **启用 Agent**：`config/agents.toml`：

   ```toml
   [agents]
   enabled = ["streamer", "minecraft"]
   ```

4. **MCP 端点（通常不用改）**：默认已指向 MaiCraft 本机默认端口；Mod 侧改过端口时才需要配：

   ```toml
   [agents.minecraft.mcp]
   enabled = true
   url = "http://127.0.0.1:8766/mcp"
   ```

两个附属采集器 `maicraft_attention`（AI 玩家的身体遭遇叙事流）与 `maicraft_chat`（游戏内他人聊天）随 `minecraft` 一同开关，不进 `collectors.toml`、无需单独配置。

## 工作原理（一分钟版）

空闲时只有一条挂着的事件长轮询，零 LLM 消耗。主播委派或递话 → 文本入队唤醒 → 一个任务批次：代读开局资料（身体/场景/目标清单）→ LLM 推理出工具调用 → 经 MCP 交给 Mod 执行 → 批次终止判定 → 回到空闲。交给 Mod 后台执行的目标（挖矿、建造）由宿主盯着事件流，结束后才带结果唤醒下一批——LLM 不轮询等待。

## 主播侧工具面

| 工具 | 作用 |
|------|------|
| `framework_delegate` | 委派目标（自然语言指令，不写步骤） |
| `framework_prompt` | 中途递话（补充指示，不派新任务） |
| `minecraft_get_work_log` | 读工作文档（待办、笔记、最近上报） |
| `minecraft_glance` | 看一眼游戏（叙事用的事实 + 手头工作） |

AI 玩家自己用的工具（`maicraft_*`、`minecraft_todo`、`minecraft_report` 等）不进主播工具列表——主播看结果，不替它玩。

## 观众命令

`[agents.streamer.command]` 的 `mappings` 表把弹幕命令映射为自然语言指令，经委派下发：

```toml
[agents.streamer.command.mappings]
mc = "看看周围，继续手头的活"
```

`target_agent` 留空时解析为当前唯一启用的游戏 Agent（换游戏只需改 `agents.enabled`）。表外命令一律静默丢弃（白名单机制）。

## 一次直播的典型流程

1. Minecraft 进世界，确认 Mod 的 MCP 端点就绪（Mod 侧日志）。
2. 启动 Amaidesu：WebUI 组件页 `minecraft` 运行中、工具页 `minecraft_*` 工具出现。MCP 一时连不上会有告警，恢复后自动重连。
3. 直播中主播按流程单推进："开个新坑，先建个庇护所" → 收到受理回执（任务号）。
4. 干活期间：主播用 `minecraft_glance` 看一眼进展、`game.report` 上报后向观众播报交付；观众用 `/mc` 递话。
5. 角色死亡、生存需求处理不了等大事会以 `game.attention_required` 进入主播叙事（死亡屏幕的恢复选项由 AI 玩家自己回答，回答不了会升级求助）。
6. 中途改指令用递话（主播 `framework_prompt` 或 WebUI 运营递话）；要它停手用 WebUI 的硬取消。

## 排障速查

| 现象 | 原因与处理 |
|------|-----------|
| 启用后完全没动静 | 正常——命令驱动，等委派或递话唤醒，空闲零消耗 |
| MCP 连接失败告警 | Mod 未启动或端口不对；同因告警已去重不刷屏，连接恢复后挂起的任务自动续跑 |
| 委派后长时间无上报 | 用 `minecraft_get_work_log` 看待办与在跑目标——后台目标要等 Mod 侧事件回来才唤醒下一批 |
| 账面出现 `waiting_for_decision` | AI 玩家上报升级等决策：递话给指令、或恢复/硬取消清账（语义见 ADR-035） |
| 角色停在死亡屏幕 | 死亡恢复决策在等回答，主播侧会看到 `game.attention_required`；按选项回答后自动恢复 |
| 重启 Mod / 换世界后目标无响应 | 事件流编号变化属预期，旧目标按"不在了"处理，重新委派即可 |

## 开发者视角

代码位置：`src/agents/minecraft/`（自包含包，目录名即 Agent 注册名）。

- `agent.py` — Agent 主体：任务批次循环、委派/递话入口、上报
- `maicraft.py` — MaiCraft v1 返回格式与事件流语义的唯一解读点
- `goals.py` — 后台目标跟踪（事件长轮询 → 唤醒）
- `tools.py` — 玩家决策工具与对外工具面
- `skills/` — 玩法经验技能库（按 Mod 能力清单筛选进目录），编写见 [Skill 编写与接入](skill.md)

架构与设计（为什么这样、终止语义怎么定的）：[MinecraftAgent 设计](../architecture/minecraft-agent.md)。
