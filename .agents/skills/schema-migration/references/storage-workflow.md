# 存储表结构迁移工作流

存储层事实源在 `src/modules/storage/`：权威表结构 DDL 在 `schema.py`（`SCHEMA_VERSION` 常量 + 各表 SQL 常量 + `build_schema_sql()`），版本迁移注册表在 `migrations/__init__.py`（`SCHEMA_MIGRATIONS`），迁移驱动在 `database.py`（`SQLiteDatabase._apply_schema_blocking`）。

## 红线（先读这个）

- **测试库绝不指向真实库 `data/amaidesu.db`。** 存储迁移测试一律用 `tests/conftest.py` 的 `db_path` fixture（`tmp_path/test.db`）或 `sqlite3.connect(":memory:")`；历史事故是测试污染了真实库。
- **`SCHEMA_VERSION` 不参与发布。** 它是存储表结构的数据迁移版本号，与 pyproject 的发布版本号完全独立，不随发布 bump、不进 CHANGELOG 的版本号。
- **迁移必须幂等。** 幂等指：对已迁移的库重复执行同一迁移，结果不变。实现手段：`IF NOT EXISTS` / `IF EXISTS` / 列表存在性检查（`migrations/_common.py` 的 `table_exists` / `column_exists`）。

## 机制（先知道迁移怎么被驱动）

`SQLiteDatabase.initialize()` → `_apply_schema_blocking`（`database.py`）依次做：

1. **迁移前备份**：探测旧库当前版本，需要升级时先把库文件快照为 `<库名>-pre-v<目标版本>-<时间戳>`（备份失败只告警不阻塞）。
2. **应用最新 DDL**：`build_schema_sql()` 全部 `CREATE TABLE IF NOT EXISTS`——新建库直接就是最新形态。
3. **推进版本**：读 `schema_migrations` 表的最高已记录版本，执行 `[current+1, SCHEMA_VERSION]` 区间内的每个注册回调，每个执行后 `INSERT OR IGNORE` 版本记录——**版本记录的写入即迁移幂等推进**，重开库时已记录的版本不再重跑。库版本高于代码期望时告警（可能是回滚到旧代码）。

注册表纪律（`migrations/__init__.py` 文档串）：**严格 +1**——`1..SCHEMA_VERSION` 每个版本都必须有注册条目，由守护测试 `tests/modules/storage/test_schema_version_upgrade.py` 断言完整性；**一律升版本**——任何 DDL 变更（包括 `build_schema_sql()` 能自动补齐的新表/新索引）都升 `SCHEMA_VERSION` 并补条目，无数据变换的条目写成显式 no-op 并注明改了什么；**一版本一文件**——文件名 `<版本>_<语义>.py`，每文件导出 `migrate(conn)`。

## 完整步骤

1. **改权威 DDL**（`schema.py`）：新表/新列/删列写进对应 SQL 常量，保证 `build_schema_sql()` 产出最新形态；`SCHEMA_VERSION` +1。新表 SQL 用模块级常量（如 `_RUNDOWN_SEGMENTS_SQL`），迁移回调从 `schema.py` import 同一份 DDL，不复制两份。
2. **写迁移文件**：新建 `src/modules/storage/migrations/v<N>_<语义>.py`，导出 `migrate(conn)`：
   - 原地修改、幂等：先存在性检查（`table_exists` / `column_exists`），新表 DDL 直接 `executescript`（`IF NOT EXISTS` 自带幂等）。
   - 数据搬移失败不能让迁移整体失败：脏行跳过并继续（范例：v17 的 `_unpack_segments_json` 对解析失败的 JSON 行跳过插行）。
   - 存量数据要保信息不丢（范例：v17 把 `key_points` 拼进 `task_description` 而非直接丢弃）。
   - 文件 docstring 写清改了什么、为什么、幂等如何保证（范例 `v17_rundown_rows_and_column_cleanup.py`）。
3. **登记注册表**：`migrations/__init__.py` import 新文件，`SCHEMA_MIGRATIONS[N] = vN_xxx.migrate`。漏登记会被 `test_fresh_db_records_full_version_chain_without_private_tables` 的严格 +1 断言拦下。
4. **消费方代码同步改**：repos / models 里引用旧列旧表的地方一并更新（跨模块改动会体现在同一提交；v17 的改动面可作参照：`git show 094067ea --stat`）。
5. **迁移测试**：放 `tests/modules/storage/test_v<N>_migration.py`，配套的注册表完整性断言在 `test_schema_version_upgrade.py`。两类写法：
   - **回调级**（`sqlite3.connect(":memory:")`）：手工造旧形状表 + 存量数据，循环跑两次 `migrate(conn)` 断言幂等与数据语义。范例 `test_v12_to_v13_adds_breakdown_column`。
   - **整链级**（`db_path` fixture + `SQLiteDatabase`）：先建当前库造存量数据，`_rollback_to` 把 `schema_migrations` 记录回退并造出旧形状，再 `initialize()` 触发真实升级链，断言版本到位、数据保留、旧列/表消失；再开第二次断言幂等。范例 `test_old_db_upgrades_monotonically_keeping_data`、`test_v11_drops_sim_window_column_keeping_personas`。
   - 永远不写死指向 `data/amaidesu.db` 的路径。
6. **验证迁移生效**：跑 `uv run pytest tests/modules/storage/test_v<N>_migration.py tests/modules/storage/test_schema_version_upgrade.py`；收尾对真实旧库验证时，**启动应用看加载日志**的 schema 推进行（`SQLiteDatabase schema 已应用: version=<N>（前版本=<N-1>）`）与迁移前备份日志，并确认真实库被原样保留、版本记录表推进到 `SCHEMA_VERSION`。

## 新表 vs 改旧表

- 新表：DDL 进 `build_schema_sql()` 后新库自动就有；旧库靠迁移回调补建。仍要升版本 + 注册（可为 no-op 式回调只建表），保持严格 +1 链完整。
- 加列：DDL 常量加列 + 回调 `column_exists` 检查后 `ALTER TABLE ADD COLUMN`；存量行的新列值想好默认语义（NULL / 回填）。
- 删列/删表：确认全库无消费方（读写两侧都查）；DROP 前若有数据要保，先在回调里搬移。
