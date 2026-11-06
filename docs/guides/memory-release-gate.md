# 主播记忆重设计：发版前门槛清单

把记忆重设计（承诺账本、场次档案与旧记忆清理）发到真实直播间之前，由发布负责人**本人**执行本文全部步骤。任何一项没过：**不发版、直播机不升级**，把报告与差条样本留下，修提示词或代码后重跑。

门槛的由来与取舍见 [ADR-065](../decisions/065-memory-ledger-shadow-run.md) 的状态行与 [ADR-067](../decisions/067-irreversible-memory-cleanup.md)；评测器的口径与样本格式见 [评测器说明](../../scripts/memory_eval/README.md)。

## 1. 离线抽取评测（live 模式）

评测的是下播整理里"找新承诺"那一次模型调用；dry 模式只是样本/校验器/指标的自洽基线，**不能替代 live**。

PowerShell（bash 把 `$env:` 换成 `export`）：

```powershell
$env:LLM_MODEL   = "你的模型名"
$env:LLM_API_KEY = "你的 API Key"
$env:LLM_BASE_URL = "https://你的OpenAI兼容端点"   # 可选
uv run python scripts/memory_eval/run_eval.py --live --out reports/memory-live.json
```

阈值（脚本按此判 PASS/FAIL，PASS 退出码 0、FAIL 退出码 1）：

| 指标 | 阈值 |
| --- | --- |
| 总召回（合成集 + 留出集） | ≥ 0.8 |
| 难负例误登记率 | ≤ 0.1 |
| 红队误登记 | 0 条 |

几个词的意思：**召回**是样本里该登记的承诺有多少真被登记了；**难负例**是看起来像承诺、其实不该登记的句子（玩笑、反问、转述别人的话），误登记率是其中被错登记的比例；**红队**样本是故意诱导模型乱登记的句子，一条都不能登记；**留出集**是写提示词时没参考过的样本，用来看提示词是不是只对着已知样本调好了。

连接参数优先取命令行 `--model` / `--api-key` / `--base-url`，其次环境变量。报告写到 `reports/`（不入库），保留到本次发版结束；逐条 diff 里"漏登记"与"误登记"可以直接作为改提示词的依据。

档案快照另跑一次多场序列评测，量几场下来稳定的事（名字、住处、和观众定下的设定）会不会被忘掉，连接参数同上：

```powershell
uv run python scripts/memory_eval/archive_rolling.py --live --out reports/archive-rolling-live.json
```

| 指标 | 阈值 |
| --- | --- |
| 稳定的事遗忘率 | ≤ 0.1 |
| 留下的旧行字节变化 | 0 处 |
| 删掉的误记行回来 | 0 条 |
| 没有本场证据的旧行被丢 | 0 条 |
| 进展被标成稳定的事 | 0 条 |

**遗忘率**是用例里前面几场立下的稳定的事，到序列最后一场整理完不在档案快照里的比例；中间夹着又薄又乱的短场，正是线上"几乎全部失忆"那一类情形。**没有本场证据的旧行被丢**量的是一次性的小事（比如被谁偷袭了一下）：哪一场都没有台词说它了结，模型却以"一次性遭遇"之类的理由去掉了它。

观众候选、她说欠着的、游戏里的人各跑一次，连接参数同上：

```powershell
uv run python scripts/memory_eval/claimed_missing.py --live --out reports/claimed-missing-live.json
uv run python scripts/memory_eval/viewer_candidates.py --live --out reports/viewer-candidates-live.json
uv run python scripts/memory_eval/game_players.py --live --out reports/game-players-live.json
```

| 评测 | 指标 | 阈值 |
| --- | --- | --- |
| 她说欠着的 | 账上找不到的欠着被列成候选的比例 | ≥ 0.7 |
| 她说欠着的 | 不该列的用例被列的比例 | ≤ 0.1 |
| 她说欠着的 | 红队诱导被自动登记成承诺 | 0 条 |
| 观众候选 | 该列的列出率 | ≥ 0.7 |
| 观众候选 | 不该列的被列比例 | ≤ 0.1 |
| 观众候选 | 标了个人信息的行被列成候选 | 0 条 |
| 游戏里的人 | 该登记的承诺记到对的玩家头上的比例 | ≥ 0.8 |
| 游戏里的人 | 记错人 | 0 条 |
| 游戏里的人 | 对不上却登记 | 0 条 |
| 游戏里的人 | 红队登记到玩家头上 | 0 条 |

**她说欠着的**是她说"上次答应过……"、账上却找不到对得上的一条；只列成待审候选，从不自动登记。**观众候选**是观众定下、她当场接受了的名字与设定，人批准才进记忆；红队组被列出的比例只报告，给审核的人参考。**游戏里的人**量的是她答应游戏玩家的事有没有记到对的人头上，"对不上却登记"由代码核对保证。

被动想起的语义阈值（余弦 0.65）还没有离线检索评测集校准；检索评测集做好之前，这一项按测试直播的线索卡记录人工看，不设自动门槛。

## 2. 留出集必须由没看过提示词的人重写

现有留出集与合成集出自同一作者、同一套口径，按方案只能当上限参考。门槛要求：找一位**没有看过 `src/agents/streamer/prompts/memory_*.md` 与校验规则**的同事，按 [样本格式](../../scripts/memory_eval/README.md#样本格式) 重写 `scripts/memory_eval/datasets/holdout.jsonl`（至少 10 条，覆盖长句、口语、闲聊中夹承诺、提旧承诺又说一遍、转述），写完再跑一次第 1 步。

重写人记在仓库外的发布记录里（提交信息与仓库文件里不写名字），并在报告里保留重写前后两版指标。

## 3. 在真实直播库副本上演练升级

不要拿直播机的库直接试。数据目录由 `storage.toml` 的 `[sqlite].db_path` 决定（相对路径按项目根解析，也可以写绝对路径）；在隔离副本里保持默认相对路径，库、`backups/` 与 `memory/` 就都落在副本自己的 `data/` 下，不会碰到直播机。准备隔离副本（示例，路径按自己机器改）：

```powershell
# 1) 从准备发布的提交开一个隔离检出，避免动到直播目录
git worktree add D:\amaidesu-upgrade-drill <准备发布的提交>
# 2) 把真实数据与配置复制进去（库、backups、memory/ 若有、config、secrets）
Copy-Item .\config   D:\amaidesu-upgrade-drill\config   -Recurse
Copy-Item .\data     D:\amaidesu-upgrade-drill\data     -Recurse
Copy-Item .\secrets  D:\amaidesu-upgrade-drill\secrets  -Recurse -ErrorAction SilentlyContinue

# 3) 记录升级前状态
cd D:\amaidesu-upgrade-drill
uv sync
uv run python -c "import sqlite3;c=sqlite3.connect('data/amaidesu.db');print('schema_version=',c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])"
Get-ChildItem .\data | Format-Table Name, Length
```

启动升级（`--dry` 仍会跑数据库迁移与配置升级钩子，但不启动组件、不连外部服务、不生成密钥、不清理历史数据）：

```powershell
uv run python main.py --dry
```

逐项检查：

- 日志出现 `存储已初始化 path=...` 与迁移/升级日志，没有 `ConfigValidationError` 或迁移异常；
- `data/backups/` 下新增 `amaidesu-pre-v25-<时刻>.db`：迁移前整库备份；
- 若升级前存在 `data/memory/`：它已被整目录移进 `data/backups/memory-pre-v25-<时刻>/`，原路径不存在，日志有 `旧记忆目录已移入迁移备份（未删除，可手工取回）`；
- `config/` 下 agents.toml、infra.toml（以及带变更的文件）版本号已推进，`config/old/` 下有改前备份；日志有配置写回记录；
- 完整性：`uv run python -c "import sqlite3;print(sqlite3.connect('data/amaidesu.db').execute('PRAGMA integrity_check').fetchone())"` 输出 `ok`；
- 二次启动幂等：再跑一次 `uv run python main.py --dry`，没有重复迁移、没有再次搬目录、没有新的 pre-v25 备份；
- 补整理留的那一场：`uv run python -c "import sqlite3;print(sqlite3.connect('data/amaidesu.db').execute('SELECT id, (ended_at_ms-started_at_ms)/60000 FROM live_sessions WHERE settled_at_ms IS NULL').fetchall())"` 列出的应是升级前最后一场正式场（时长至少 5 分钟）；去掉 `--dry` 启动一次后它被整理掉，记忆账本页能看到这一场的档案；
- 想在隔离副本里额外看隐私密钥生成与保留期清理，去掉 `--dry` 再启动一次（会尝试连外部服务，连不上只会告警；默认保留期 0，不会删数据）。

## 4. 至少 3 个真实正式场由运营者人工判误记

每场正式场下播整理完成后（WebUI 的"下播整理"面板显示已整理）：

1. 打开 WebUI「记忆账本」页（`/memory`）→「承诺账本」→ 状态筛选选「全部」；
2. 按每行"登记于 … · 场次 #N"确认是本场新登记的承诺；
3. 逐条核对：她真的当众答应过、而且做得到吗？不是的点「判误记」（会留墓碑，之后整理不会重新登记）；
4. 统计本场：新登记条数、判误记条数，误记率 = 判误记 ÷ 新登记，要求 **每场 ≤ 10%**，连续至少 3 个真实正式场。

WebUI 上一眼看不到逐场统计时，用只读 SQL 对账（只读，不写库）：

```sql
-- 每场新登记与其中判误记的条数（按登记场次）
SELECT created_session_id AS session, COUNT(*) AS total,
       SUM(status = 'void') AS voided
FROM commitments
WHERE created_session_id IS NOT NULL
GROUP BY created_session_id
ORDER BY session DESC
LIMIT 10;
```

把三场的场次编号、新登记数、判误记数记进发布记录；任一场超过 10% 就停下来改提示词，重新从这一项开始。

## 5. 结论

四项全过才允许发版与升级直播机：

- [ ] live 评测 PASS（召回、难负例、红队三项同时达标；档案快照多场序列的遗忘率、字节变化、误记回来、没证据被丢、进展被标稳定五项同时达标；她说欠着的、观众候选、游戏里的人三组各项同时达标）
- [ ] 留出集已由没看过提示词的人重写并重跑
- [ ] 隔离副本升级演练全部检查项通过，回滚步骤按 [直播运行手册](runbook.md#升级与回滚) 走过一遍
- [ ] 至少 3 个真实正式场人工判误记，每场 ≤ 10%

报告、评测 JSON 与人工判定表放在仓库外（`reports/` 不入库），发布记录里只写结论与提交号。
