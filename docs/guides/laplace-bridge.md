# LAPLACE Event Bridge 弹幕接入

## 什么是 LAPLACE

[LAPLACE Chat](https://laplace.live/chat) 是一个运行在浏览器中的 B 站直播聊天面板：把弹幕、Super Chat、礼物分栏显示，并提供 OBS 覆盖层、TTS、实时翻译、OBS/VTS 控制等能力；免安装、免账号（接入本采集器时，要拿到完整用户名/UID 仍需按前置条件配置登录同步）。

[LAPLACE Event Bridge](https://laplace.live/chat/event-bridge) 是 LAPLACE Chat 的 **WebSocket 事件桥**：把 LAPLACE Chat 收到的实时 B 站直播事件（弹幕、礼物、SC、上舰、进房等）转发给本地客户端。Amaidesu 的 `bili_danmaku_laplace` 采集器以 **client 角色**连接桥接服务器，把事件转换为现有的 `room.message.*`，因此不需要直接调用 B 站接口。

## 数据链路

```
LAPLACE Chat 控制台 (https://chat.laplace.live/dashboard/<房间号>, server 角色)
        ↓ 推送 B 站事件
桥接服务器（leb-server，默认 ws://localhost:9696）
        ↓ 广播
Amaidesu bili_danmaku_laplace 采集器 (client 角色)
        ↓ room.message.danmaku / gift / super_chat / guard / enter
Planner / 存储 / 字幕 / 虚拟形象
```

LAPLACE 是**传输层**，事件仍属于 B 站：采集器统一写 `platform="bilibili"`；观众身份沿用 B 站 `uid`，与 legacy `bili_danmaku` 同命名空间，与 `bili_danmaku_official` 的 `open_id` 不互通。礼物金额只能按源提供的折扣后总价记录，口径差异见“已知限制”。

## 前置条件

**先阅读 [LAPLACE Event Bridge 文档](https://laplace.live/chat/event-bridge)，按其中说明准备一个可用的桥接服务器。** 本文只覆盖 Amaidesu 侧的配置与排障。

1. **桥接服务器（`leb-server`）**：从 [event-bridge Releases](https://github.com/laplace-live/event-bridge/releases) 下载对应平台的单文件二进制（Windows 为 `leb-server-windows-x64.exe`）。该文件不随仓库分发。
2. **LAPLACE Chat 控制台**：使用 `leb-server` 时，打开 `https://chat.laplace.live/dashboard/<房间号>`。Event Bridge 设置只存在于控制台页面，OBS 页面不转发事件。
3. **LAPLACE 登录同步（推荐）**：在控制台「进阶」→「同步密钥」配置 LAPLACE Login Sync。未配置时 LAPLACE 可能只提供打码事件（用户名 `***`，`uid` 可能为 0），采集器会因缺少 uid 丢弃事件；要稳定拿到真实 UID，请配置登录同步。

> 使用 LAPLACE Chat 控制台时，连接方式选「匿名直连」。「开放平台」来源的 `uid` 恒为 0，且公开事件不提供 `open_id`/`union_id`，当前采集器无法还原观众身份。

> LAPLACE Event Fetcher 是 LAPLACE 的另一个独立项目；它的 bridge mode 不属于本文的 `leb-server` 路径，部署与事件源要求以该项目文档为准。

## 配置步骤

### 1. 启动 leb-server

```bash
# 默认监听 localhost:9696，无认证
leb-server-windows-x64.exe

# 需要认证时（token 与 Amaidesu 配置保持一致）
leb-server-windows-x64.exe --auth "your-token"
```

可选参数：`--host`（默认 `localhost`）、`--port`（默认 `9696`）、`--debug`。

### 2. 连接 LAPLACE 控制台

1. 打开 `https://chat.laplace.live/dashboard/<房间号>`。
2. 点击工具栏齿轮「设置」，打开「控制台设置」抽屉。
3. 找到「Event Bridge 集成」：
   - 主机地址：`localhost`
   - 端口：`9696`
   - 密码：留空；若 leb-server 用 `--auth` 启动，填写同一个 token
4. 点击「连接」；成功后可保持「页面载入时自动连接」开启。

> 控制台页面必须保持开启——它是事件源。浏览器可能节流后台标签页，建议把 `chat.laplace.live` 加入浏览器性能白名单。

### 3. 配置 Amaidesu

在 `config/collectors.toml` 的 `enabled` 名单加入 `bili_danmaku_laplace`，并填写同名子段：

```toml
enabled = ["console_input", "bili_danmaku_laplace"]

[bili_danmaku_laplace]
url = "ws://localhost:9696"
token = ""            # 与 leb-server --auth 一致；无认证留空
room_id = 0           # 0=不过滤；填写时使用规范长房间号
```

其余字段（事件开关、去重缓存、重连区间等）的权威定义在
[`BiliDanmakuLaplaceCollector.ConfigSchema`](../../src/modules/collectors/bilibili/laplace/bili_danmaku_laplace_collector.py)。
未写的键由配置加载管线补默认值并写回，不需要手工抄一份字段表。

**同一房间只启用一种 B 站消息源**（`bili_danmaku` / `bili_danmaku_official` / `bili_danmaku_laplace` 三选一），也不要中途换源。同时启用多个真实源会让同一条弹幕被采集多次，导致落库与统计翻倍；中途换源会让同一观众在 viewers/画像里拆成两个身份。

采集器启动后会无限重连，因此 Amaidesu、leb-server、控制台的启动顺序不限；首次连接失败只记录 warning，不阻塞启动。

## 事件映射

Laplace 的 `message` / `gift` / `superchat` / `toast` / `interaction` 分别映射为
`room.message.*` 语义事件；字段语义见 [LAPLACE 事件类型文档](https://chat.laplace.live/event-types/)。
映射表与过滤/去重/脱敏逻辑的权威实现在
[`events.py` 的 `LaplaceEventMapper`](../../src/modules/collectors/bilibili/laplace/events.py)。
其他事件（在线人数、点赞、天选、红包、弹幕撤回等）当前忽略；SC 删除与弹幕撤回不会追溯修改已落库数据。

## 已知限制

- **实时流，无历史补播**：断线期间的事件不会补发；重连后依靠 `id` / `stableKey` / 内容指纹去重，降低重复落库概率。gift 的事件 `id` 可能由上游按“时间戳:房间:UID”生成，采集器不单独用 `id` 判断重复，而是比较 uid、礼物 ID、数量、价格、时间戳等内容字段，避免同一用户同一时间戳的不同礼物被误删。
- **观众身份与 official 不互通**：Laplace 公开事件只有 `uid`（legacy 链路是真实 B 站 UID；Open Platform 来源的 `uid` 已废弃、恒 0，且不提供 `open_id`）。本采集器与 legacy 同命名空间，`bili_danmaku_official` 用 `open_id`；切换消息源会让同一观众在 viewers/画像里变成两个身份，同一房间不要混用、不要中途换源。B 站字段定义见[开放平台文档](https://open-live.bilibili.com/document/f9ce25be-312e-1f4a-85fd-fef21f1637f8)。
- **礼物金额为源限制**：Laplace 只提供含舰队折扣的总价，采集器把它同时写入 `total_price` 与 `paid_price`；`unit_price` 是由总价反推的近似值。现有金额口径要求贡献按标价计算，该源无法提供标价，因此贡献统计会按折扣后金额计算；代码与本文均已显式注明。
- **连击信息缺失**：Laplace `gift` 事件只提供 `giftAmount`，没有 `combo_id` / `combo_count`；`GiftInfo` 的 `combo_*` 保持默认，连击归并只能按消息量处理。
- **SC 金额按人民币换算**：Laplace 的 `priceNormalized` 是实际货币单位（人民币元），采集器优先用它 ×1000 转为金瓜子；只有该字段缺失或非正数时才回退 `price`。金额大小与单位无关，不能按金额猜单位。
- **盲盒 ID 取配置 ID**：`blindGift.blind_gift_config_id` 是盲盒配置 ID，写入 `GiftInfo.blind_gift_id`；`original_gift_id` 是开出的实际礼物，不写入该字段。
- **上舰无粉丝牌快照**：Laplace `toast` 事件没有粉丝牌字段，`GuardInfo` 的该两项保持默认；原始事件仍在 `raw_data` 中。
- **Dashboard 实时事件流没有 guard 专用渲染**：guard 会进入实时事件流（WS 类型折叠为 `room.message`）并正常落库、进入 Planner 上下文，但前端当前按弹幕样式展示（内容为开通文案）。
- **使用 leb-server 时控制台页面必须开启**；只开 OBS 弹幕窗口不会向 bridge 推送事件。Event Fetcher bridge mode 不需要控制台。

## 排障

| 现象 | 处理 |
|---|---|
| Amaidesu 日志「LAPLACE bridge 不可达」 | 确认 exe 已启动且端口与 `url` 一致（客户端直连，不读取系统代理） |
| 日志「认证失败（401）」 | `token` 与 leb-server `--auth` 不一致，或控制台密码与之一致但 Amaidesu 未填 |
| 连接成功但没有事件 | 确认控制台在 `/dashboard/<房间号>` 页面、Event Bridge 显示已连接、直播间确实在开播；若日志提示缺少 uid，检查「进阶」登录同步，或确认连接方式不是「开放平台」 |
| 日志「Laplace 事件缺少 uid（已丢弃）」 | 在控制台「进阶」配置登录同步；「开放平台」来源 uid 恒 0 且不转发 open_id，当前不可用 |
| 同一观众变成两个身份 | 检查是否在 official 与 laplace/legacy 之间换过源；两者身份键不同，不会自动合并 |
| 事件被过滤 | 检查 `room_id` 是否与事件 `origin` 一致；填 0 可先排除过滤因素 |
| 同一弹幕出现两次 | 检查 `enabled` 是否同时启用了 legacy/official/laplace 中的多个 |
