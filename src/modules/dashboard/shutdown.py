"""停机守卫与停机现场日志：给重启时的清理序列加时限，并留下卡住时的现场。

重启的编排是"拉起新进程 → 清理旧进程 → 旧进程退出"。清理序列里有若干 await
（WebSocket 客户端排干、uvicorn 优雅关闭、Vite 子进程回收、SQLite 落盘），任一处
卡住都不会自己醒过来，旧进程就一直活着：它既占着弹幕采集的游戏会话（新进程连
不上，弹幕收不到），又继续往虚拟形象注入捕捉参数（形象高频闪烁）。

因此清理必须有时限：到点就把现场留在盘上（各线程的栈），然后强制退出。
停机期间运行期的日志广播已经被摘掉（清理序列第二步就摘 sink），此后进程打的
日志无处可去，所以现场另写独立文件。
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from src.modules.logging import get_logger

#: 项目根目录（src/modules/dashboard/ 往上三层）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 停机现场日志目录（与运行期日志目录分开：运行期日志靠广播，停机时已不可用）
SHUTDOWN_LOG_DIR = PROJECT_ROOT / "logs" / "shutdown"

#: 兜底定时器比事件循环时限多留的宽限（秒）：让正常路径先由事件循环收口
_TIMER_GRACE_S = 5.0


class ShutdownJournal:
    """停机现场日志：按天写 ``logs/shutdown/<日期>.log``。

    运行期日志靠 WebSocket 广播，而停机清理的第二步就摘掉了那个广播，此处之后
    进程打的日志无处可去。停机现场改写独立文件，事后能核对重启走没走完、卡在
    哪一步、卡住时各线程的栈。
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self._directory = Path(directory) if directory is not None else SHUTDOWN_LOG_DIR
        self._logger = get_logger("ShutdownJournal")

    def _path_for_today(self) -> Path:
        return self._directory / f"{datetime.now().strftime('%Y-%m-%d')}.log"

    def record(self, message: str, **fields: Any) -> None:
        """记一条停机节点：时间 + 文本 + 可选字段。

        写盘失败只告警不停机——取证失败不该阻断退出。
        """
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        extras = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{stamp} {message}" + (f" ({extras})" if extras else "")
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with self._path_for_today().open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            self._logger.warning(f"停机现场日志写入失败: {exc}", exc=True)
        self._logger.info(message)

    def dump_stacks(self, reason: str) -> Optional[Path]:
        """把当前所有线程的栈追加写进现场文件（卡住时的定位依据）。

        返回写到的文件路径；写不出时返回 None。
        """
        path = self._path_for_today()
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== 线程栈现场（{reason}）=====\n")
                handle.flush()
                faulthandler.dump_traceback(file=handle, all_threads=True)
                handle.write("\n===== 现场结束 =====\n")
        except OSError as exc:
            self._logger.warning(f"线程栈写入失败: {exc}", exc=True)
            return None
        return path


class ShutdownGuard:
    """给停机清理加时限：期限内没走完就把现场留在盘上并强制退出。

    两道时限互补：
    - 事件循环内的 ``wait_for``：正常路径由它收口，超时抛 TimeoutError；
    - 独立线程的定时器：连 ``wait_for`` 的超时都传不回来（事件循环本身失灵）时兜底。
    """

    def __init__(
        self,
        timeout_s: float,
        journal: Optional[ShutdownJournal] = None,
        exit_func: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._journal = journal if journal is not None else ShutdownJournal()
        # 退出动作可注入：默认直接终止进程，注入替身时便于验证退出时机
        self._exit_func = exit_func if exit_func is not None else os._exit
        self._logger = get_logger("ShutdownGuard")

    async def run(self, sequence: Callable[[], Awaitable[None]]) -> bool:
        """在时限内执行清理序列。

        返回 True 表示清理走完；返回 False 表示超时——现场已留在盘上，进程已被
        请求退出（真实运行时 ``os._exit`` 不会返回，走不到这个 return）。
        """
        self._journal.record("进入停机清理", timeout_s=self._timeout_s)
        timer = threading.Timer(
            self._timeout_s + _TIMER_GRACE_S,
            self._record_and_exit,
            args=("停机清理超时（事件循环未按时返回）",),
        )
        timer.daemon = True
        timer.start()
        try:
            await asyncio.wait_for(sequence(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            self._record_and_exit("停机清理超时")
            return False
        except asyncio.CancelledError:
            self._journal.record("停机清理被取消")
            raise
        except Exception:
            self._journal.record("停机清理异常", detail=traceback.format_exc(limit=5))
            raise
        finally:
            timer.cancel()
        self._journal.record("停机清理完成")
        return True

    def _record_and_exit(self, reason: str) -> None:
        """留证并强制退出（真实运行时进程立即消失，不会返回）。"""
        self._journal.record(f"{reason}，强制退出")
        self._journal.dump_stacks(reason)
        self._exit_func(0)
