# 直播运行手册

从开机到开播的操作顺序、每步怎么确认成功、失败时代码会怎么表现。安装与首次配置见[快速开始](../getting-started.md)，GPT-SoVITS 服务的部署与调音见 [GPT-SoVITS 语音服务部署](gptsovits.md)——本文只覆盖"每次开播都要做的事"。

## 开播前清单

### 1. 先起外部服务

Amaidesu 启动时就会去连它们；连接型组件错过也不会坏（各有自动重连），但先起好可以省掉开播后的冷启动和重连告警。

| 服务 | 何时需要 | 就绪标志 |
|------|---------|---------|
| GPT-SoVITS（api_v2） | `[tts].provider = "gptsovits"` | 服务端日志出现 `Uvicorn running on http://127.0.0.1:9880`（命令见 [部署指南](gptsovits.md)） |
| VTube Studio | `[avatar.platform].enabled` 含 `vts` | 模型已加载，插件 API 端口 8001 开启 |
| Warudo | enabled 含 `warudo` | WebSocket 端口 19190 可达，场景里已导入按当前角色生成的 Amaidesu 蓝图（见[虚拟形象接入](avatar.md#warudo)） |
| VRChat | enabled 含 `vrchat` | VRChat 里 OSC 已启用（见[虚拟形象接入](avatar.md#vrchat)） |
| OBS | 启用 `live_frame` 采集器或 `[tools.studio.obs]` | WebSocket 服务开启（默认端口 4455），密码与配置一致 |
| MaiBot + 适配器插件 | `[maibot_sync].enabled = true` | MaiBot 在跑、插件已加载；在白名单群里能查到直播近况（见[与 MaiBot 同步](maibot-sync.md)） |

### 2. 启动 Amaidesu

```bash
uv run python main.py
```

首次运行会生成 config/ 七文件后主动退出，编辑完配置再跑一次（见[快速开始](../getting-started.md)）。

### 3. 开播前自检（启动后一分钟内）

按顺序确认，全部就位即开播就绪：

1. **配置加载**：日志出现「配置装配清单」——七文件装配成功。
2. **语音链路**（gptsovits）：出现「GPT-SoVITS 预热完成，首句合成已是热态」。若是「预热未完成（服务未启动或异常…）」，说明第 1 步的 GSV 服务没就绪，语音试说验证命令见[部署指南](gptsovits.md)。
3. **WebUI**：打开 `http://127.0.0.1:60214`。端口被占用时自动顺延（+1 至 +9），以日志为准；`dashboard/dist/` 未构建时该地址只有 API，页面会提示构建方法。
4. **虚拟形象**：WebUI 工具页能看到启用的 `vts_*` / `warudo_*` 工具即已连接。VTS 未连上时工具页看不到 `vts_*`——它在后台每 5 秒重试，连上后自动补注册，不用重启。
5. **消息链路**：WebUI 组件页里采集器与 Agent 均为运行中；发一句控制台输入（或等一条真实弹幕）出现在事件流页，链路即通。
6. **开播时选对场次类型**：直播控制台「开播」弹窗里，面向真实观众选「正式直播」，调试、模拟、回放选「彩排」。类型决定这一场会不会被整理成承诺与场次档案（只有正式场整理）、近况与整场记录推不推给 MaiBot；选错了下播后可在场次侧栏改。

## 开播后观察面

- **运行总览**：EventBus 吞吐与三组组件健康。
- **事件流页**：实时事件（WebSocket 推送，可按类型过滤）；历史查询覆盖内存环形缓冲与 `logs/events/` 落盘文件。
- **工具页**：工具健康快照、支持重连的工具可手动重连、可调试直调单个工具。
- **LLM 用量页**：token、费用、命中率与请求级明细。

## 代码会自己处理的事（不用管）

| 情形 | 自动行为 |
|------|---------|
| VTS 断线 / 晚于 Amaidesu 启动 | 每 5 秒自动重连，连上自动补注册工具 |
| MCP 连接失败 | 自动重连；同因失败告警去重（不刷屏），恢复后再失败才重新告警 |
| B 站官方弹幕心跳失效 | `app_heartbeat_reopen = true`（默认开）时自动重开会话；关闭时节流告警 |
| 工具连续失败 | 达阈值（默认 3 次）熔断并广播 `tool.health.*`；按周期（默认 30 秒）探活，通过即自动恢复 |
| Agent 失去心跳 | 超过阈值（默认 60 秒）判死自动重建 |
| 单句语音合成失败 | 放弃当句、发 `tts.utterance.failed`（字幕显示失败行），不阻塞后续发言与决策 |
| 下播整理失败（模型不可用、输出读不懂、输入超窗口） | 整场不提交，重试次数加一、原因写进整理报告；下次启动或下次下播自动再试，第 3 次失败后才停下等人工 |

## 要人工处理的事

- **GSV 服务本身挂了**：引擎侧无自动重试（当句放弃），重启 GSV 服务即可，Amaidesu 不用动。
- **VTS 首次授权**：VTS 界面弹窗授权一次，token 落在 `data/vts_token.txt`；提示未授权或 token 失效时重新授权一次。
- **Agent 进入 ERRORED 态**：5 分钟窗口内重建失败 3 次会停止自动重试——看日志定位原因，修复后在 WebUI 组件页重启该 Agent。
- **主循环卡顿告警**：「存在同步阻塞调用冻结循环」表示事件循环单次挂起超过 2.5 秒，按日志里的模块名定位同步调用。
- **某一场一直整理不出来**：WebUI 记忆页的整理状态里能看到失败原因与未登记清单；修好模型或提示词后点"重新整理"（把重试次数清零，下一次整理调度会重新挑到它）。模型看懂了但代码没让登记的原话会列在未登记清单里，认为是真的就手建一条承诺。

## 关停

Ctrl+C 走优雅关停：采集器 → Agent → Dashboard → 场次结账（保证下播落账）→ 诊断落盘冲刷 → 存储关闭。异常退出有两层兜底：关停链里的兜底会补做场次结账；没跑到或被打断的下播整理（承诺与场次档案的账本整理）由下次启动补完待整理的场次。

## 隐私删除与不记录名单

- 「忘了我」命中后：立即停止记录那位观众，下播后的清理里执行删除（最迟发令后 24 小时）。执行前 ta 本人发撤回短语（默认「算了」「开玩笑的」「别忘了我」等，见 `storage.toml` `[privacy].retract_phrases`）即取消；WebUI 观众页可看"待删除（预计执行时刻）"，运营者也能代办撤回或解除名单。
- 不记录名单只存外部 ID 的摘要，密钥是库外文件（`[privacy].key_path`，默认 `secrets/forget.key`，不入库）。**这个文件和数据库一样要单独备份**：它不在整库备份里，恢复数据库后若名单非空而密钥缺失，程序会启动报错，只给两条出路——找回密钥文件，或按报错里给出的语句确认放弃名单（清空后名单里的人会重新被记录）。
- 恢复时把密钥放回配置里的路径（数据库在项目内时相对项目根解析，数据库在项目外时相对库文件所在目录），再启动。
- 保留期在 `storage.toml` 的 `[retention]`：`raw_days` 管弹幕、付费身份列、话题摘要、请求正文、迁移前整库备份与迁移时移入的旧记忆目录；`viewer_absence_days` 管长期不来的观众行。两键默认 0 = 永不自动删除；清理在启动时和每次下播整理后运行。设了 `raw_days` 后，到期的迁移前整库备份与旧记忆目录也会被删掉，之后就不能再靠它们回滚——要保留回滚能力，升级前按下文自己另存整个 `data/`。

## 升级与回滚

**升级前（一次性）**：

1. 停掉程序，整目录备份 `data/`（库、`backups/`、若还在的 `memory/`）、`config/` 与 `secrets/`；
2. 记录当前库版本（库版本记在 `schema_migrations` 表里）：`uv run python -c "import sqlite3;print(sqlite3.connect('data/amaidesu.db').execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])"`；
3. 想先演练就按[发版前门槛清单](memory-release-gate.md#3-在真实直播库副本上演练升级)在隔离副本里跑一遍。

**升级**：启动程序，等日志出现 `存储已就绪`；确认 `data/backups/` 下有 `amaidesu-pre-v25-<时刻>.db`，日志有 `旧记忆目录已移入迁移备份（未删除，可手工取回）: <源> -> <目标>`（升级前有过 `data/memory/` 时）；确认 `config/old/` 下有改前配置、版本号已推进；再跑一次确认二次启动幂等。迁移是**不可逆**的：升级成功后旧回顾、旧画像、本场备忘录与 `memory/` 目录都不在程序的数据路径里了，回退旧二进制不能直接启动现有库（旧代码会把两张表按建库语句重建出来，与账本口径分叉）。

**回滚**（只在必须退回升级前版本时）：

1. 停程序，另存一份升级后产生的新数据（升级后已经播过的场次账本、承诺等，回滚会丢）；
2. 用升级前的整库备份还原：把 `data/backups/amaidesu-pre-v25-<时刻>.db` 复制回 `data/amaidesu.db`（同时移走 `-wal`/`-shm`）；
3. 把 `data/backups/memory-pre-v25-<时刻>/` 整个目录移回 `data/memory/`；
4. 用 `config/old/` 下的备份把 `config/` 还原成升级前版本，放回 `secrets/forget.key`；
5. 换回升级前的程序版本启动。

## 已知残留边界

以下位置仍可能有观众身份，删除与保留期都按使用者的配置处理，写公告与对外说明时一并交代：

- 宽限期（发令到之后第一次下播后的清理，最迟 24 小时）内，ta 的数据还在库里；
- 诊断日志（LLM 请求正文、事件流）与 WebUI 落盘日志（`logs/webui/*.jsonl`，含外部 ID 与昵称）、LLM 请求记录表正文、迁移前备份与迁入备份目录的旧记忆；
- 复盘数据包导出（`GET /api/v1/review/export` 打出的整库副本与日志）导出后不受保留期与删除管辖，包里含观众身份，仅限开发组使用、用完删除；
- 单字昵称、ta 没有出现过的场次里她提到 ta 的名字、本场内存时间线、运营者手写老梗栏里的昵称不替换；
- 换开放平台应用后 open_id 会变，不记录名单认不出 ta。
- 提示词里给会话能点名的只有承诺（`约#N`）与老梗（`梗#N`）；场次档案行编号 `档#N` 目前只能由运营者在 WebUI 看，会话点名它会被拒绝并记一条 warning 到日志。

## 生产计数（只读 SQL）

对账与评测用；只读查询，不改库。先 `uv run python -c "import sqlite3;..."` 或任意只读 SQLite 工具连 `data/amaidesu.db`。

```sql
-- 承诺账本：状态分布与总数
SELECT status, COUNT(*) FROM commitments GROUP BY status;

-- 每场新登记的承诺与判误记条数（人工判误记占比的分子分母）
SELECT created_session_id AS session, COUNT(*) AS total,
       SUM(status = 'void') AS voided
FROM commitments WHERE created_session_id IS NOT NULL
GROUP BY created_session_id ORDER BY session DESC LIMIT 10;

-- 最近场次的档案行数
SELECT live_session_id, COUNT(*) AS lines FROM archive_lines
GROUP BY live_session_id ORDER BY live_session_id DESC LIMIT 10;

-- 整理进度：正式场/彩排场、已整理/待整理、尝试次数
SELECT kind, settled_at_ms IS NULL AS pending, settle_attempts, COUNT(*)
FROM live_sessions GROUP BY 1, 2, 3;

-- 不记录名单与待删除
SELECT COUNT(*) FROM forget_list;
SELECT COUNT(*) FROM viewers WHERE forget_requested_at_ms IS NOT NULL;

-- 送达口径：某场她的台词里送达与未送达（把 ? 换成场次主键）
SELECT SUM(dropped_at_ms IS NULL) AS delivered,
       SUM(dropped_at_ms IS NOT NULL) AS dropped
FROM live_chat WHERE sender_role = 'assistant' AND live_session_id = ?;
```

## 只验证不运行

```bash
uv run python main.py --dry
```

完整走一遍装配但组件只构造不启动——不连外部服务（连接型 Provider 不建连）、不落盘、不发 LLM 调用。改完配置后先用它快速验证组合根能构造成功。
