# ADR-010：工具可用性手动操作（手动重连）

- 状态：已采纳（2026-09-08 定案 / 同日实现落库，随 task/tool-reconnect 分支并入主线）
- 日期：2026-09-08
- 实现提交：
  - `e6c7376c8a032f2c0451c7e5af24ada3fa8d01bd`（feat(tools): 工具可用性重连契约与恢复联动）
  - `784f7fbad25261abbb2adad14284140d4c921877`（feat(avatar): VTS/Warudo 手动重连能力）
  - `060c50df0a0504a08ab63a1aef2337950bb581a9`（feat(studio): OBS 手动重连接入）
  - `06889d541405ec79f64384ed07ccb185aa0b3e48`（feat(mcp): MCP provider 手动重连能力）
  - `152d1a20d731f0ef372a2cf388034317a405f760`（feat(dashboard): 手动重连 REST 端点与工具重连字段）
  - `cfff31d70084dea4d81be1c949e0484586441e1d`（feat(dashboard): 工具页手动重连按钮）

## 背景（Context）

工具管控三维模型定案后（存在性/可见性/可用性），可用性维度（通道通不通，词对
已连接/已断开）在运行时只有被动观察：熔断器连续失败摘除工具面，恢复完全依赖
ToolHealthMonitor 的周期探活（`probe_interval_ms` 默认 30s + 熔断后最小驻留时长）。

现实场景的痛点：VTS 服务重启、Warudo 断线、MCP server 掉线、OBS 未启动——
这些通道故障时若不做任何动作，熔断工具要等下一拍探活通过才能恢复，运营者只能
干等，且无法确认"重试是否有效"。

部分 Provider 已有自动重连循环（VTS `_reconnect_loop`、Warudo `_connection_loop`），
但它们是后台自愈，面向"断线后自动拉回"，不面向"运营者确认通道已好、立即恢复"的
即时反馈诉求；OBS 甚至没有任何重连路径（连接只在 setup 时建立一次）。

## 决策（Decision）

**给可用性维度补"手动动作面"：运营者可一键重连指定 Provider，重连成功立即探活
恢复其熔断工具，UI 即时反馈。**

### 1. 连接动作契约（BaseToolProvider）

```python
async def connect(self) -> bool: ...      # 建立连接；无连接语义默认 False
async def disconnect(self) -> bool: ...   # 断开连接；无连接语义默认 False
async def reconnect(self) -> bool: ...    # 默认组合：断开→建立
@property
def supports_reconnect(self) -> bool:     # 单点判定：本类是否覆写 connect
```

- `supports_reconnect` 按"是否覆写 `connect`"内省判定（`type(self).connect is not
  BaseToolProvider.connect`）——覆写 connect 即同时获得重连支持，单一动作点
- 无连接语义／无状态 Provider（如 `make_provider_from_specs` 的工厂产物）不覆写
  connect → 自动判定不支持，不暴露重连按钮（边界 S5）
- `ToolProvider` Protocol 同步补签名（docstring 级，不强制 duck-typed 实现）

### 2. 四家连接类 Provider 接入

| Provider | connect | disconnect | 说明 |
|----------|---------|-----------|------|
| MCP | 已连接短路，否则 `client.connect()` | `client.close()` | reconnect 用默认组合（关闭+连接） |
| VTS | 触发已有 `_connect()`（幂等） | 复用已有 `_disconnect()` | 后台 `_reconnect_loop` 语义不变 |
| Warudo | 触发已有 `_connect()` | 最小断开（不破坏 `_connection_loop`） | 内部重连逻辑已存在，只做入口 |
| OBS | `_connect_obs()` 重建 | 断开但不破坏 `setup`/`_has_started` | 补齐无重连路径的缺口 |

VRChat OSC 无连接语义，明确不接入。

### 3. 手动重连与探活恢复联动（ToolRegistry）

`reconnect_provider(provider_id)`：
1. 按 provider_id（= provider.name，全局唯一）定位 Provider
2. 未找到 → 错误报告；不支持重连 → 错误报告
3. `await provider.reconnect()` 失败 → 错误报告
4. 成功 → 反查归属该 provider 的全部已注册工具名，逐个 `probe_tool` 探活，
   通过者 `recover_tool` 复位熔断（发 `tool.health.*` closed 事件）
5. 返回 `{ok, provider_id, recovered, still_tripped}` 报告

即：**手动重连是"即时探活"的多工具批量版**——重连本身恢复通道，probe+recover
恢复熔断状态，UI 一步到位。

### 4. 入口

- REST：`POST /api/v1/tools/providers/{provider_id}/reconnect`
  （404 未注册 / 409 不支持重连）
- `GET /api/v1/tools` 条目新增 `supports_reconnect` 字段（经
  `registry.provider_supports_reconnect(tool_name)` 判定，归属经 `_tool_owner`
  解析——与探活的所有权映射同一来源）
- Tools 页工具行按 `supports_reconnect` 条件渲染"重连"按钮，反馈按
  `recovered`/`still_tripped` 分级

### 5. 并发边界

手动重连与自动重连循环／探活潜在的并发 connect，**本期不加锁**：低频人工操作，
错误窗口极小；MCP client 的 connect 并发安全若成为现实问题再做（设计边界 S4）。

## 替代方案（Alternatives）

### 各 Provider 自建重连 REST，无统一契约

**拒绝**。Dashboard 需为每个 provider 特判调用方式，新增 provider 即改前端——
契约统一在 registry 一层解决问题，消费方只面对 `POST .../reconnect` 一个形态。

### 把 connect 设为抽象方法，强制所有 Provider 实现

**拒绝**。无连接语义／无状态 Provider（spec 工厂、纯计算工具）没有可建立的东西，
强制实现是伪方法负担；`supports_reconnect` 判定正好是"有连接语义才暴露能力"的
结构表达。

### 手动重连只置位状态，交由自动循环接管

**部分采纳**（VTS 现有 `vts_reconnect` 工具即此思路），但**不完整**：仅置位
`_is_connected=False` 让循环重拉，无法顺带复位熔断状态——熔断工具仍要再等
探活周期。本 ADR 的"重连成功 → probe → recover"联动是决策核心，回退此方案会
让手动重连的即时性收益归零。

### 自动重连开关（suspend/resume，四象限期望态×观察态）

**本期不做，未来化**。范围远大于手动动作面（涉及期望态建模、状态机、UI 开关），
手动重连先行满足当前运营诉求，自动开关另立任务。

## 后果（Consequences）

**收益**：

- 运营即时恢复：VTS/Warudo/MCP/OBS 通道故障后一键拉回，熔断工具不等探活节拍
- OBS 重连路径缺口被补齐（此前连接只建一次，断了只能重启应用）
- 统一入口：Dashboard 按钮 + 单条 REST，新增连接类 Provider 只需覆写 connect
  （单点判定自动暴露能力）
- 与自动重连循环共存：手动入口不改变任何后台自愈行为

**代价**：

- BaseToolProvider 契约新增三个方法 + Protocol 签名，ToolProvider（duck-typed）
  结构一致性靠文档契约维持
- 四家 Provider 各加 connect/disconnect（每家约 20-40 行），OBS 的 disconnect
  需小心不破坏 `setup`/`_has_started` 语义（与 cleanup 的完整收尾区分）
- 前端工具页新增操作列，列表元数据多一字段

**遗留（如实记录，不掩盖）**：

- 可用性自动重连开关（suspend/resume）未实现，未来化
- 手动重连与自动重连循环并发不加锁（边界 S4，低概率场景）
- OBS 无自动重连循环——手动重连是当前唯一补偿，未来可加自动
- `provider_id` 目前即 provider.name；若未来出现同名 provider 或嵌套命名
  空间，需要重新审视 id 语义
