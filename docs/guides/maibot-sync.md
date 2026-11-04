# 与 MaiBot 同步

MaiBot 7×24 常驻，负责 QQ 群里的麦麦；Amaidesu 只在直播时运行。接上以后，QQ 麦麦知道直播正在播什么、刚播完什么，也能按配置把整场的对话回放进它的长期记忆。

本文讲 Amaidesu 这一侧怎么接、怎么验证、怎么排错；适配器插件自身的安装、参数与注入规则见 [MaiBot-Amaidesu-Adapter 的 README](https://github.com/LittleSadSheep/MaiBot-Amaidesu-Adapter)。

不接不影响直播：`[maibot_sync].enabled = false`（默认）时完全不同步。彩排场次一律不同步——模拟、回放、调试都不会推给 QQ 那边。

> 旧的场次回顾推送与共享长期笔记同步已随主播记忆改按账本记录而退役（配置键由升级钩子删除，回顾已不存在；见 [清掉旧记忆的存储面](../decisions/067-irreversible-memory-cleanup.md)）。现在只剩直播近况与可选的整场记录两条链路。

## 两条链路

| 链路 | 同步什么 | 什么时候 | 开关 |
|---|---|---|---|
| 直播近况 | 是否在播、标题、开播/收播时刻、当前环节 | 开播、换环节、收播；直播中按心跳间隔重发完整快照 | `enabled` |
| 整场记录 | 这一场的全部对话（观众弹幕、醒目留言、主播发言；跳过模拟消息） | 收播、道别那一轮跑完之后读库推送 | `enabled` + `send_transcript`（默认关） |

整场记录也以 `enabled = true` 为前提；关掉主开关，近况与记录都不发。

## 准备

1. **MaiBot 与适配器插件**：按[插件 README](https://github.com/LittleSadSheep/MaiBot-Amaidesu-Adapter) 安装。插件类型是 `adapter`，和 QQ 适配器跑在同一个插件进程里。
2. **插件的服务地址与令牌**：插件 `config.toml` 的 `[server]` 段，`auth_token` 为空时服务不启动：

   ```toml
   [server]
   host = "127.0.0.1"   # Amaidesu 不在同一台机器时改 0.0.0.0，只在可信网络用（明文 HTTP）
   port = 8767
   auth_token = "<随机长字符串>"

   [qq]
   group_ids = ["123456789"]   # 白名单群：只有这些群注入近况，也只有这里能记约定
   ```

   MCP 与近况接口共用这个端口和令牌。白名单群之外的群与私聊不会注入直播近况。

## 配置 Amaidesu

### 一、让主播能读写 QQ 群（MCP）

`config/tools.toml` 里把适配器登记成一个 MCP 服务，服务名会做工具名前缀（配成 `qq` 时工具全名是 `qq_read_group`、`qq_ask_mai` 等）：

```toml
[tools.mcp.config.servers.qq]
transport = "http"
url = "http://127.0.0.1:8767/mcp"
headers = { Authorization = "Bearer <同一个令牌>" }
```

连不上不影响启动：Amaidesu 先登记这个 MCP 服务，后台按退避间隔重连；连上后工具自动出现，不用重启 Amaidesu。

### 二、直播近况与整场记录（HTTP）

`config/infra.toml`：

```toml
[maibot_sync]
enabled = true
url = "http://127.0.0.1:8767/live-status"
token = "<同一个令牌>"
send_transcript = false   # 推送整场记录，默认关；要开还得打开插件的 live_memory.enabled
```

- `url` 填完整的近况接收地址（含 `/live-status`）。
- 两个高级项一般不用动：`heartbeat_interval_ms`（插件据此判断直播是否还在进行）、`request_timeout_ms`（单次上报超时）。
- 整场记录含观众昵称和醒目留言的付费金额。用 B 站官方开放平台采集弹幕时，向第三方展示这些信息与平台管理规范冲突，建议保持关闭或按需开启；详见下文「隐私与合规」。

### 三、对齐名字

人设本身不同步：微信/QQ 那边的称呼由插件侧决定，需要与 Amaidesu 人设里的主播名字手动改到一致，否则两边对同一个麦麦的叫法会对不上。

## 各链路的行为

### 直播近况

每次都是完整快照，不是增量；插件错过任何一次都能被下一次补齐。只有正式场会推，Amaidesu 在事件里按场次类型过滤，彩排场连开播都不发。插件把快照缓存在数据目录的 `live_status.json`，怎么注入 QQ 麦麦的提示词、多久算"刚下播"由插件侧决定（见插件 README）。

### 整场记录

- **推送时机**：收播事件后**不立即**读库——道别那一轮的台词要等模型好几秒才落库。上报器等这一轮的收场信号（`planner.decision` 的 `proactive:signoff`）到了再读，另留一小段余量让存储侧提交完；等不到信号时按兜底上限照样读库推送。被新场次顶掉的收播事件没有道别轮可等，直接按现状推。
- **不推的情形**：没配 `send_transcript`；空场次被丢弃、或运营者在 WebUI 删掉了这一场（内容已不存在或本意是清掉它，不该再外流）；彩排场。
- **失败重试**：失败按 5 秒 / 30 秒 / 2 分钟重试三次。插件明确拒收（4xx，例如对应开关没开）时不再重试。
- **进程退出与补推**：每场推成功（或被插件明确拒收）后，库文件旁的 `maibot_transcript_pushed.jsonl` 记一行“已处理”。收播后进程立刻退出、来不及推的那一场，下次启动会补推：只补 3 天内结束、真正播出过台词的正式场，最多看最近 20 场，按时间先后补；MaiBot 仍连不上时整批停下，留到再下次。删掉这个文件会让 3 天内的场次再推一遍（QQ 里多一份记录），不会丢数据。
- 整场记录要真正进 MaiBot 的长期记忆，插件侧还要打开 `live_memory.enabled`，并按插件 README 配好 `adapter_policy.toml` 与共享记忆组。

### 开播提示

开播喊人由插件侧完成（QQ 麦麦去群里说），不占 Amaidesu 的配置。前提是近况同步已开、插件 `go_live.group_ids` 已填、群在 `adapter_policy.toml` 里放行；插件只认正式场，并且要稳定播够延迟才喊，避免误触开播和测试喊人。细节见插件 README「开播提示」。

## 验证

1. **启动日志**。`[maibot_sync].enabled = true` 且地址、令牌齐全时，启动后会看到一行（内容随是否开整场记录变化）：

   ```text
   直播近况同步已接线：开播 / 换环节 / 收播 → http://127.0.0.1:8767/live-status
   ```

   开了整场记录会多一段" / 整场记录"。只看到 `[maibot_sync] 已启用但 url 或 token 未设置` 说明配置没填全，同步没有装配。

2. **工具页**。控制台「工具」页能看到 `qq_read_group`、`qq_ask_mai`、`qq_recall_memory` 等（provider 为 `qq`，即 MCP 服务名）；插件没起时这组工具在重连成功前不出现，属正常。

3. **近况**。开一场正式直播，插件数据目录的 `live_status.json` 应随心跳更新；在白名单群里问麦麦"现在在播什么"，它应能经 `query_live_status` 答上来。

4. **整场记录**。开着 `send_transcript` 与插件 `live_memory.enabled`，下一场正式直播结束后，插件的 `live_memory/` 下应出现该场的回放任务；用 `--dry` 不产生真实场次，验不了这一项。

5. **断线演练**。停掉 MaiBot 再开播：直播照常，Amaidesu 日志会记推送失败。恢复后，直播中的下一次心跳会补上近况；整场记录只在失败后重试三次，错过就补不回来，下播前后尽量让插件在线。

## 排查

| 症状 | 先查 |
|---|---|
| 完全没有同步日志 | `[maibot_sync].enabled`、`url`、`token` 是否都填了；`url` 是否含 `/live-status` |
| 有同步日志但插件没反应 | 插件服务是否启动（`auth_token` 为空时不会启动）；`token` 是否与插件 `server.auth_token` 一致；端口是否被占 |
| 主播的工具清单里没有 `qq_*` | `[tools.mcp].enabled`、server 段是否写对；插件是否在线；重连有退避，稍等再看 |
| QQ 看不到直播近况 | 群是否在插件 `qq.group_ids`；是否已下播超过插件配置的最近窗口，超过后插件不再注入 |
| 整场记录没进记忆 | `send_transcript` 与插件 `live_memory.enabled` 是否都开；`adapter_policy.toml` 与共享记忆组是否配好；这一场是不是被删/被丢弃的场次 |
| 彩排场什么都没同步 | 设计如此；只有正式场对外同步 |

## 隐私与合规

- 整场记录会带上弹幕原文、观众昵称与醒目留言金额。
- 走 B 站官方开放平台采集弹幕时，向第三方展示这些信息与平台管理规范冲突。不需要时把 `send_transcript` 关掉；近况（是否在播、标题、环节）不含观众信息，可以照常开。
- 插件只对白名单群注入近况；QQ 里记约定的工具也只在这些群可用。

## 关掉

按需要逐项关，不必全关：

| 想停掉 | 改什么 |
|---|---|
| 全部同步（近况与整场记录） | `[maibot_sync].enabled = false` |
| 只停整场记录 | `send_transcript = false` |
| 只停主播读写 QQ 群 | 删除或禁用 `[tools.mcp.config.servers.qq]` |

关掉后本地状态照常工作，不依赖 MaiBot。
