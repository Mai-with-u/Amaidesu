# 文字冒险 Agent（text_adv）使用指南

`text_adv` 是一个**游戏 Agent**（命令驱动），用于直播文字冒险 / 视觉小说类游戏：它观察游戏画面，把剧情推进和待决策的选项上报给主播 Agent，由主播定夺后驱动游戏。它**绝不替主播做选择**——感知与上报是它的全部主动行为。

它为什么是 Agent 而不是工具：`set_auto(true)` 后它自己拉起并持有观察循环（`asyncio.Task`），有自己的内部状态（剧情环缓冲、auto 标志）和循环内自主决策（何时降频省 VLM、护栏失败如何收场）；工具面只是主播遥控它的命令入口。归类判据见[组件开发指南](component.md#判据组件该做成哪一类)。

## 工作原理

### 装配链

启用后由 Agent 工厂构造（`src/modules/agents/factory.py`），工厂内注入四个依赖：

- **读屏**：经 ToolRegistry 调用公共工具 `vision_look_at_screen`（不是 text_adv 私有的，任何 Agent 都可调用）
- **帧采集**：mss 截屏（供画面稳定判定与帧级去重）
- **窗口后端**：pygetwindow（按标题关键词查找游戏窗口、夺焦、检查前台与几何）
- **键鼠注入**：pyautogui（工具动作出口：推进按键、选项点击）

### 生命周期：空闲零消耗

命令驱动的关键是**启动 ≠ 运行**。应用启动时 text_adv 只注册 4 个工具到 ToolRegistry（可见名单全部是 `["streamer"]`，只有主播能调），然后完全空闲——没有循环、没有截屏、没有 VLM 消耗。直到主播调用 `text_adv_set_auto(true)`，Agent 才拉起观察循环。

### 观察循环

`set_auto(true)` 后每轮执行：

1. **窗口护栏**：检查游戏窗口前台焦点与几何，未通过则停循环、发一条 `game.error`、auto 回落 false
2. **等画面稳定**：连续多次采样一致才放行（有超时兜底）
3. **帧级去重**：画面指纹与上轮相同则跳过，零 VLM 消耗
4. **VLM 读屏**：以固定格式问句调 `vision_look_at_screen`，解析出「正文 + 带中心坐标的选项列表」
5. **文本去重与上报**：新剧情屏发 `game.milestone`；出现选项发 `game.report`（escalation 语义，消息里直接写明"请调用 text_adv_choose(option=N)"）
6. **降频**：连续多轮无新文本时暂停 VLM 只做廉价帧检查，直到画面先静止再变化才恢复——常驻动画不构成恢复依据

感知失败（采集异常 / 空图 / VLM 解析失败）只记日志、循环继续；只有护栏失败或循环整体死亡才发事件并停机。

### 与主播的协作回路

```
text_adv 观察循环 ──game.milestone（新剧情屏）──┐
              └──game.report（选项屏，升级决策）──┤
                                                ▼
                                    主播 Agent 收集进 Planner 上下文
                                                │
                                    Planner 决策 → 调 text_adv_choose(option=N)
                                                │
                                                ▼
                              text_adv 鼠标点击选项 → 验证画面变化 → 快照返回
```

`game.*` 事件的订阅契约见[事件系统](../architecture/event-system.md)。

## 配置

### 1. 启用 Agent

`config/agents.toml`：

```toml
[agents]
enabled = ["streamer", "text_adv"]
```

### 2. 开启视觉工具（最常见遗漏）

text_adv 的读屏依赖公共视觉工具，`config/tools.toml` 必须：

```toml
[tools.vision]
enabled = true
```

关着的话 `vision_look_at_screen` 不会注册，读屏永远失败且不会有任何事件上报。启动日志有硬检查，出现下面这行就是在提醒你：

> 文字冒险 Agent 已启用但感知读屏未装配（vision_look_at_screen 未注册，[tools.vision].enabled=false？）

`[tools.vision.config]` 中的 `monitor_index`、`vlm_timeout_ms`、`default_max_width` 即读屏实际使用的参数。

### 3. Agent 段配置

`[agents.text_adv]` 完整字段与默认值见 `src/agents/text_adv/config.py`（单一权威，带注释），最需要注意的两个：

- `game_window_title_keyword`：游戏窗口标题关键词。**留空则窗口护栏形同虚设**（可能锚定到任意窗口），强烈建议填写
- `monitor_index`：游戏窗口所在显示器（0 起），与 `[tools.vision.config].monitor_index` 指向同一块屏

### 4. 委派目标

主播的委派目标（`[agents.streamer.command]`）留空时，框架解析为**当前唯一启用的游戏 Agent**。换游戏只需改 `enabled` 列表；但 minecraft 与 text_adv **同时启用**时必须在委派配置里显式指定目标。

## 工具面（仅主播可见）

| 全名 | 作用 | 备注 |
|------|------|------|
| `text_adv_set_auto` | 启停自动观察 | 幂等；True 拉起观察循环，False 停止 |
| `text_adv_advance` | 推进一屏 | 按键 → 等稳定 → 识别 → 返回快照 |
| `text_adv_choose` | 选择第 N 项 | 鼠标点击选项中心坐标；点击后验证画面变化，未变化返回失败且不重试 |
| `text_adv_get_state` | 只读查询当前状态 | 当前屏文本、选项列表、auto 与循环状态 |

## 一次直播的典型流程

1. 启动 Amaidesu，确认日志无"感知读屏未装配"警告
2. 游戏窗口打开并置于 `monitor_index` 所指显示器，前台可见
3. 直播中对主播说"开始玩文字冒险游戏"（或由流程单/Planner 自主决定），主播调 `text_adv_set_auto(true)`
4. 观察循环开始上报：剧情推进时主播会在叙事里看到新屏内容，自然地向观众讲述
5. 出现选项时主播收到 `game.report`，按其人设做出选择，调 `text_adv_choose(option=N)`
6. 想接管手动推进就 `set_auto(false)`，之后用 `advance` 逐屏推进、`get_state` 随时查看
7. 游戏窗口被切走 / 关闭 → 护栏停循环并发一条 `game.error`，主播会知道观察中断了

本地调试可以配合[模拟直播间](simulator.md)触发主播的决策。

## 排障速查

| 现象 | 原因与处理 |
|------|-----------|
| 启动后完全没动静 | 正常——命令驱动，等主播调 `text_adv_set_auto(true)` |
| 启动日志有"感知读屏未装配"警告 | `[tools.vision].enabled = false`，读屏永远不会成功 |
| `game.error`：未找到游戏窗口 | `game_window_title_keyword` 配置不当或窗口未打开 |
| `game.error`：失去前台焦点 / 几何不可得 | 游戏窗口被切走或关闭，护栏按设计停机 |
| 日志持续"读屏解析失败（本轮跳过）" | VLM 回复不合规约格式，检查视觉模型能力与 `vlm_timeout_ms` 是否够用 |
| `text_adv_choose` 返回失败 | 点击后画面未变化（选项可能已失效），先 `get_state` 看当前屏再决定 |

## 开发者视角

代码位置：`src/agents/text_adv/`（自包含包，目录名即 Agent 注册名）。

- `agent.py` — Agent 主体：auto 标志、观察循环、`game.*` 上报
- `vlm.py` — 识别契约：问句构造、VLM 回复结构化解析、`RegistryVisionReader`
- `tools.py` — 工具面四件套与可见名单
- `screen.py` / `window.py` / `input.py` — 稳定判定、窗口护栏、键鼠注入后端
- `state.py` / `config.py` — 内部状态、配置 Schema

新增同类游戏 Agent 时照此包范式扩展（加包 + 配置，框架零改动），范式见 [游戏 Agent 范式（Minecraft）](../architecture/minecraft-agent.md) 与[组件开发指南](component.md)。
