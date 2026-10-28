"""直播场次类型：正式场与彩排场（代码常量，非数据库表）。

场次被四类下游使用：明细分组、主播记忆（场次回顾 / 观众画像）、对外同步
（MaiBot）、统计复盘。后三类真正要问的只有一句"这场的观众是不是真人"，
场次类型就是这句话的答案，开场时声明、进行中不改：

- ``live``（正式）：面向真实观众的直播；
- ``rehearsal``（彩排）：模拟器生成、录制回放、控制台调试——观众是假的或
  只有开发者自己。

主播记忆按类型分两条线：正式场只读正式场的回顾，彩排场只读彩排场的回顾；
对外同步与统计只认正式场。它与场次来源（``source``：谁开的场）正交。
"""

from __future__ import annotations

from typing import Literal, Tuple

SessionKind = Literal["live", "rehearsal"]

SESSION_KIND_LIVE: SessionKind = "live"
SESSION_KIND_REHEARSAL: SessionKind = "rehearsal"

# 合法取值全集（API 校验、迁移回填、WebUI 下拉共用）
SESSION_KINDS: Tuple[SessionKind, ...] = (SESSION_KIND_LIVE, SESSION_KIND_REHEARSAL)

__all__ = ["SESSION_KINDS", "SESSION_KIND_LIVE", "SESSION_KIND_REHEARSAL", "SessionKind"]
