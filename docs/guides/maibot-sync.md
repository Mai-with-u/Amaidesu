# 与 MaiBot 同步

MaiBot 7×24 常驻，负责 QQ 群里的麦麦；Amaidesu 只在直播时运行。接上以后，QQ 麦麦知道直播正在播什么、刚播完什么、以前每一场做了什么，也能和主播共用长期笔记：QQ 里答应过的事会进主播的笔记，主播的笔记也会发给 QQ 麦麦看（观众相关的部分除外）。

本文讲 Amaidesu 这一侧怎么接、怎么验证、怎么排错；适配器插件自身的安装、参数与注入规则见 [MaiBot-Amaidesu-Adapter 的 README](https://github.com/LittleSadSheep/MaiBot-Amaidesu-Adapter)。

不接不影响直播：`[maibot_sync].enabled = false`（默认）时完全不同步，`share_notes`、`send_recap` 等开关也不起作用。彩排场次一律不同步——模拟、回放、调试都不会推给 QQ 那边。

## 三条链路

| 链路 | 同步什么 | 什么时候 | 开关 |
|---|---|---|---|
| 直播近况 | 是否在播、标题、开播时刻、当前环节 | 开播、换环节、收播；直播中每 60 秒重发一次心跳 | `enabled` |
| 共享长期笔记 | 给 QQ 麦麦看的笔记发布稿；QQ 里记下的约定 | 正式场笔记每次写入、启动后第一次读；每次读之前先收约定 | `enabled` + `share_notes`（默认开） |
| 场次回顾 / 整场记录 | 本场回顾正文 / 这场的全部对话 | 回顾写好后 | `send_recap`（默认开）/ `send_transcript`（默认关） |

后两条也以 `enabled = true` 为前提；关掉主开关，三样都不发。

## 准备

1. **MaiBot 与适配器插件**：按[插件 README](https://github.com/LittleSadSheep/MaiBot-Amaidesu-Adapter) 安装，插件需包含提交 `038af3f`（共享笔记正本交回 Amaidesu）或更新。更早的插件把笔记正本存在自己那边、用 `get` / `put` 接口，与现在的发布协议对不上（旧接口会回 400 提示升级）。插件类型是 `adapter`，和 QQ 适配器跑在同一个插件进程里。
2. **插件的服务地址与令牌**：插件 `config.toml` 的 `[server]` 段，`auth_token` 为空时服务不启动：

   ```toml
   [server]
   host = "127.0.0.1"   # Amaidesu 不在同一台机器时改 0.0.0.0，只在可信网络用（明文 HTTP）
   port = 8767
   auth_token = "<随机长字符串>"

   [qq]
   group_ids = ["123456789"]   # 白名单群：只有这些群注入近况与笔记，也只有这里能记约定
   ```

   MCP 与两个 JSON 接口共用这个端口和令牌。白名单群之外的群与私聊不会注入直播近况，也看不到共享笔记。

## 配置 Amaidesu

### 一、让主播能读写 QQ 群（MCP）

`config/tools.toml` 里把适配器登记成一个 MCP 服务，服务名会做工具名前缀（配成 `qq` 时工具全名是 `qq_read_group`、`qq_ask_mai` 等）：

```toml
[tools.mcp.config.servers.qq]
transport = "http"
url = "http://127.0.0.1:8767/mcp"
headers = { Authorization = "Bearer <同一个令牌>" }
```

连不上不影响启动：Amaidesu 先登记这个 MCP 服务，后台按 5 秒起步、60 秒封顶的间隔重连；连上后工具自动出现，不用重启 Amaidesu。

### 二、直播近况与共享笔记（HTTP）

`config/infra.toml`：

```toml
[maibot_sync]
enabled = true
url = "http://127.0.0.1:8767/live-status"
token = "<同一个令牌>"
share_notes = true        # 共享长期笔记，默认开；关掉只影响笔记
send_recap = true         # 推送场次回顾，默认开
send_transcript = false   # 推送整场记录，默认关；要开还得打开插件的 live_memory.enabled
```

- `url` 填完整的近况接收地址（含 `/live-status`）；笔记接口由它推出——把末段换成 `notes`，即 `http://127.0.0.1:8767/notes`。
- 两个高级项一般不用动：`heartbeat_interval_ms`（默认 60000，插件据此判断直播是否还在进行）、`request_timeout_ms`（默认 5000）。
- 回顾与整场记录含观众昵称和醒目留言的付费金额。用 B 站官方开放平台采集弹幕时，向第三方展示这些信息与平台管理规范冲突，建议按需关掉；详见下文「隐私与合规」。

### 三、对齐名字

共享笔记连上后，Amaidesu 每次请求都会带上主播人设里的名字（`[agents.streamer.persona].bot_name`），插件拿它和 `bot.nickname`、`bot.alias_names` 比对。对不上时两边日志各告警一次，笔记照常发布，但 QQ 那边的称呼会对不上。人设本身不同步，名字需要手动改到一致。

## 各链路的行为

### 直播近况

每次都是完整快照，不是增量；插件错过任何一次都能被下一次补齐。只有正式场会推，Amaidesu 在事件里按场次类型过滤，彩排场连开播都不发。插件把快照缓存在数据目录的 `live_status.json`，怎么注入 QQ 麦麦的提示词、多久算"刚下播"由插件侧决定（见插件 README）。

### 共享长期笔记

正本始终是本地 `memory/MEMORY.md`，格式也只归 Amaidesu 管；插件只存一份渲染好的"给 QQ 麦麦看的笔记"，不认识笔记格式。

| 环节 | 行为 |
|---|---|
| 发布 | 正式场笔记每次写入后（以及启动后第一次读时），把开播注入的那份视图（四个注入节与人自己加的节、去掉标签、不含答应某位直播观众的条目）整份推给插件；内容没变不重发 |
| 收约定 | 每次读笔记前先取插件的待收清单，按正文去重并进"答应过的事"，标 `〔未兑现 · 来自 QQ群〕`；发布新版本成功后才回报"已收"，插件再从清单删掉 |
| 断线 | 读写照常走本地；发布、收约定与回报由后台按 30 秒 / 2 分钟 / 10 分钟补做，最后一档一直重试 |
| 彩排 | 不同步，只在本地 |

没开共享时这条链路完全不经过，行为与只用 Amaidesu 时一样。设计与取舍见[主播记忆](../architecture/streamer-memory.md)和 [ADR-063](../decisions/063-maibot-interop-published-views.md)。

### 场次回顾与整场记录

- 回顾写好后推一次；失败按 5 秒 / 30 秒 / 2 分钟重试三次。插件明确拒收（4xx，例如对应开关没开）时不再重试。
- 整场记录挂在回顾事件上而不是收播事件上：进程退出时收播可能来不及推，回顾会在下次启动时补写，整场记录随之补推。
- 两个开关各自独立：只关回顾时仍会订阅回顾事件，只为触发整场记录；只关整场记录不影响回顾。
- 整场记录要真正进 MaiBot 的长期记忆，插件侧还要打开 `live_memory.enabled`，并按插件 README 配好 `adapter_policy.toml` 与共享记忆组。
- 进程重启后补写的回顾同样按场次类型过滤，彩排场不会因为补写而漏过来。

### 开播提示

开播喊人由插件侧完成（QQ 麦麦去群里说），不占 Amaidesu 的配置。前提是近况同步已开、插件 `go_live.group_ids` 已填、群在 `adapter_policy.toml` 里放行；插件只认正式场，并且要稳定播过 `go_live.delay_ms`（默认 5 分钟）才喊，避免误触开播和测试喊人。细节见插件 README「开播提示」。

## 验证

1. **启动日志**。`[maibot_sync].enabled = true` 且地址、令牌齐全时，启动后会看到两行（内容随开关变化）：

   ```text
   直播近况同步已接线：开播 / 换环节 / 收播 / 场次回顾 → http://127.0.0.1:8767/live-status
   长期笔记与 MaiBot 共享：正本在本地，写完发布到 http://127.0.0.1:8767/notes，读前收下 QQ 里记下的约定
   ```

   开了整场记录会多一段" / 整场记录"。只看到 `[maibot_sync] 已启用但 url 或 token 未设置` 说明配置没填全，同步没有装配。

2. **工具页**。控制台「工具」页能看到 `qq_read_group`、`qq_ask_mai`、`qq_recall_memory` 等（provider 为 `qq`，即 MCP 服务名）；插件没起时这组工具在重连成功前不出现，属正常。

3. **近况**。开一场正式直播，插件数据目录的 `live_status.json` 应随心跳更新；在白名单群里问麦麦"现在在播什么"，它应能经 `query_live_status` 答上来。

4. **笔记**。正式场里让笔记发生一次写入（例如收播整理），插件的 `notes/published.md` 应更新；在 QQ 白名单群让麦麦记一件事，下次读笔记后本地 `MEMORY.md` 应多出标着"来自 QQ群"的条目，插件的 `notes/promises.json` 里对应条目被删掉。

5. **回顾**。回顾写好后，插件的 `live_recaps.json` 应多一场；开了整场记录时 `live_memory/` 下会出现该场的回放任务。

6. **断线演练**。停掉 MaiBot 再开播：直播照常，Amaidesu 日志会记推送失败。恢复后，直播中的下一次心跳会补上近况，笔记由后台继续补发；场次回顾只在失败后重试三次（约两分半），错过就补不回来，下播前尽量让插件在线。

## 排查

| 症状 | 先查 |
|---|---|
| 完全没有同步日志 | `[maibot_sync].enabled`、`url`、`token` 是否都填了；`url` 是否含 `/live-status` |
| 有同步日志但插件没反应 | 插件服务是否启动（`auth_token` 为空时不会启动）；`token` 是否与插件 `server.auth_token` 一致；端口是否被占 |
| 主播的工具清单里没有 `qq_*` | `[tools.mcp].enabled`、server 段是否写对；插件是否在线；重连有 5~60 秒退避，稍等再看 |
| QQ 看不到直播近况 | 群是否在插件 `qq.group_ids`；是否已下播超过 `recent_window_ms`（默认 12 小时），超过后插件不再注入 |
| 笔记没发到插件 | `share_notes` 是否开；是否只写了彩排场（彩排不同步）；发布地址（由 `url` 推出的 `/notes`）是否可达 |
| 笔记里没有 QQ 记的约定 | 约定是否在插件 `notes/promises.json`；名字是否对得上（看两边告警）；发布成功后才回报，插件连不上时会留着 |
| 回顾没推过去 | `send_recap` 是否开；插件是否明确拒收（对应开关没开时 4xx，不重试） |
| 整场记录没进记忆 | `send_transcript` 与插件 `live_memory.enabled` 是否都开；`adapter_policy.toml` 与共享记忆组是否配好 |
| 彩排场什么都没同步 | 设计如此；只有正式场对外同步 |

## 隐私与合规

- 回顾正文会写观众昵称（答应过谁什么）和付费者（谁在撑场）；整场记录还会带上弹幕原文与醒目留言金额。
- 走 B 站官方开放平台采集弹幕时，向第三方展示这些信息与平台管理规范冲突。不需要时把 `send_recap`、`send_transcript` 关掉；近况（是否在播、标题、环节）不含观众信息，可以照常开。
- 插件只对白名单群注入近况与笔记；QQ 里记约定的工具也只在这些群可用。

## 关掉

按需要逐项关，不必全关：

| 想停掉 | 改什么 |
|---|---|
| 全部同步（含笔记与回顾） | `[maibot_sync].enabled = false` |
| 只停共享笔记 | `share_notes = false` |
| 只停回顾正文 | `send_recap = false` |
| 只停整场记录 | `send_transcript = false` |
| 只停主播读写 QQ 群 | 删除或禁用 `[tools.mcp.config.servers.qq]` |

关掉后本地笔记、回顾、记忆都照常工作，不依赖 MaiBot。
