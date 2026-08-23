"""
W3 QA Scenario: SQLite 11 表创建（§1.50 / §1.53 9b）

本脚本直接执行 Acceptance Criteria 中描述的验证：
1. 初始化 storage（空路径）
2. 断言 11 表全部存在
3. 断言 schema_migrations 记录当前 SCHEMA_VERSION

输出写到 .omo/evidence/w3-storage-tables.txt 供后续查阅。
"""

import asyncio
import os
import tempfile
from pathlib import Path

from src.modules.storage import (
    SCHEMA_VERSION,
    SQLiteStore,
    list_expected_tables,
)


async def main() -> None:
    evidence_path = Path(os.environ.get("EVIDENCE_PATH", ".omo/evidence/w3-storage-tables.txt"))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("W3 QA Scenario: SQLite 11 表创建 + schema_migrations 验证")
    lines.append("=" * 70)
    lines.append("")

    with tempfile.TemporaryDirectory(prefix="w3-qa-") as td:
        db = Path(td) / "qa.db"
        store = SQLiteStore(db)
        try:
            await store.initialize()
            lines.append("[Step 1] 初始化成功")
            lines.append(f"  DB path: {db}")
            lines.append(f"  SCHEMA_VERSION (代码) = {SCHEMA_VERSION}")
            lines.append("")

            lines.append("[Step 2] 检查 11 张权威表 + schema_migrations 全部存在")
            lines.append("-" * 70)
            all_ok = True
            for t in list_expected_tables():
                exists = await store.table_exists(t)
                marker = "OK" if exists else "MISSING"
                if not exists:
                    all_ok = False
                lines.append(f"  [{marker}] {t}")
            lines.append("")

            lines.append("[Step 3] schema_migrations 记录当前 SCHEMA_VERSION")
            lines.append("-" * 70)
            version = await store.get_schema_version()
            lines.append(f"  已应用 schema version = {version}")
            lines.append(f"  代码 SCHEMA_VERSION     = {SCHEMA_VERSION}")
            version_match = version == SCHEMA_VERSION
            lines.append(f"  校验：{'PASS' if version_match else 'FAIL'}")
            lines.append("")

            lines.append("[Step 4] simulate 列存在且默认 0（§1.6 贯穿列）")
            lines.append("-" * 70)
            for table in ("live_chat", "gifts", "super_chats"):
                rows = await store.execute(f"PRAGMA table_info({table})")
                cols = {row["name"]: row for row in rows}
                if "simulated" in cols:
                    default_val = int(cols["simulated"]["dflt_value"])
                    lines.append(f"  [{table}] simulated 列存在，默认值 = {default_val}")
                else:
                    lines.append(f"  [{table}] simulated 列 **缺失**")
                    all_ok = False
            lines.append("")

            lines.append("[Step 5] assert_schema_ready 启动自检")
            lines.append("-" * 70)
            try:
                await store.assert_schema_ready()
                lines.append("  assert_schema_ready: PASS（无异常）")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  assert_schema_ready: FAIL ({exc})")
                all_ok = False
            lines.append("")

            lines.append("=" * 70)
            lines.append(
                f"综合结果：{'ALL PASS' if all_ok and version_match else 'FAIL'} （{SCHEMA_VERSION} / {len(list_expected_tables())} 表）"
            )
            lines.append("=" * 70)
        finally:
            await store.close()

    evidence = "\n".join(lines) + "\n"
    evidence_path.write_text(evidence, encoding="utf-8")
    print(evidence)
    print(f"[QA] wrote evidence to: {evidence_path}")


if __name__ == "__main__":
    asyncio.run(main())
