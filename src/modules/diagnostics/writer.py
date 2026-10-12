"""诊断记录落盘器：按小时 JSONL 追加流 + 覆写快照，后台线程 fire-and-forget。"""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

logger = get_logger("DiagnosticsWriter")

_STOP = None
"""队列哨兵：通知写入线程冲刷退出。"""


class DiagnosticsWriter:
    """诊断记录统一落盘器（多 kind 共用一个写入线程）。

    目录布局（相对项目根）::

        logs/llm_requests/2026-10-11/14.jsonl   # 逐小时追加流
        logs/llm_context/latest.json            # 覆写快照（不参与过期清理）

    写入语义：``append`` / ``write_snapshot`` 只入队即返回，磁盘 IO 与过期
    清理全部在写入线程内串行完成；队列满丢弃并计数告警（每 100 条汇总一次）。
    主调用链零阻塞，任何失败只降级不传播。

    过期清理按天目录整目录删除（快照文件不参与）；``retention_days=0`` 关闭
    清理。清理触发点：``start()`` 与每天首次 ``append``（跨天检测）。
    """

    def __init__(
        self,
        base_dir: Union[str, Path] = Path("logs"),
        retention_days: int = 14,
        *,
        max_queue: int = 4096,
    ) -> None:
        """
        Args:
            base_dir: 落盘根目录（相对项目根或绝对路径）
            retention_days: 正文流文件保留天数，超期天目录整体删除；0 = 永不清理
            max_queue: 写入队列容量上限（超出丢弃，best-effort 旁路定位）
        """
        self._base_dir = Path(base_dir)
        self._retention_days = max(0, retention_days)
        self._queue: "queue.Queue[tuple[str, str, Optional[Mapping[str, Any]]]]" = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._dropped = 0
        self._last_cleanup_date: Optional[str] = None

    @property
    def base_dir(self) -> Path:
        """落盘根目录（读侧按 ``<base_dir>/<kind>/`` 定位各诊断流）。"""
        return self._base_dir

    # -------------------- 对外接口（主调用链，零阻塞） --------------------

    def append(self, kind: str, record: Mapping[str, Any]) -> None:
        """追加一条记录到 ``<kind>/<YYYY-MM-DD>/<HH>.jsonl``（fire-and-forget）。

        record 须含 ``timestamp_ms``（毫秒）——写入定位与读侧按行回查都依赖
        它；缺失时以当前时刻兜底。
        """
        self._enqueue("append", kind, record)

    def write_snapshot(self, kind: str, file_name: str, payload: Mapping[str, Any]) -> None:
        """原子覆写 ``<kind>/<file_name>`` 快照（如每模型最新上下文水位）。

        快照文件不参与过期清理；写入经同一队列与追加流串行，避免半写状态
        被读侧读到。
        """
        self._enqueue("snapshot", kind, {"__file_name__": file_name, "__payload__": payload})

    def read_snapshot(self, kind: str, file_name: str) -> Dict[str, Any]:
        """同步读取快照文件（覆写前合并用；文件小，调用链低频）。"""
        path = self._base_dir / kind / file_name
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def start(self) -> None:
        """启动写入线程（幂等；懒启动由首次 append 触发，一般无需显式调用）。"""
        self._ensure_thread()

    def stop(self) -> None:
        """停机收工：冲刷队列后退出写入线程（阻塞至多 5 秒）。"""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            logger.warning("诊断落盘队列已满，停机冲刷放弃尾部记录")
        thread.join(timeout=5.0)
        if thread.is_alive():
            logger.warning("诊断落盘线程停机超时（尾部记录可能未落盘）")
        self._thread = None

    # -------------------- 内部实现 --------------------

    def _enqueue(self, action: str, kind: str, record: Mapping[str, Any]) -> None:
        if not self._ensure_thread():
            return
        try:
            self._queue.put_nowait((action, kind, record))
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                logger.warning(f"诊断落盘队列已满，累计丢弃 {self._dropped} 条（kind={kind}）")

    def _ensure_thread(self) -> bool:
        """懒启动写入线程；返回 False 表示 writer 已停机（进程收尾中）。"""
        if self._thread is not None and self._thread.is_alive():
            return True
        if self._thread is not None:
            # stop() 后不再接受新写入（进程退出路径）
            return False
        self._thread = threading.Thread(target=self._run, name="diagnostics-writer", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self._cleanup_expired()
        except Exception:  # noqa: BLE001 清理失败不影响写入主职责
            logger.exception("诊断记录启动清理失败")
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            action, kind, record = item
            try:
                if action == "append" and record is not None:
                    self._append_sync(kind, record)
                elif action == "snapshot" and record is not None:
                    self._write_snapshot_sync(kind, record)
            except Exception:  # noqa: BLE001 单条失败不终止写入循环
                logger.exception(f"诊断记录落盘失败（kind={kind}）")
        # 停机前冲刷队列中尚存的记录（stop 哨兵之后的排队项）
        while True:
            try:
                action, kind, record = self._queue.get_nowait()
            except queue.Empty:
                break
            if action == "append" and record is not None:
                try:
                    self._append_sync(kind, record)
                except Exception:  # noqa: BLE001 停机冲刷同不传播
                    logger.exception("诊断记录停机冲刷失败")
            # 停机冲刷只处理 append；快照允许丢（下一次成功调用会重新覆写）

    def _append_sync(self, kind: str, record: Mapping[str, Any]) -> None:
        ts = record.get("timestamp_ms")
        ts_ms = int(ts) if isinstance(ts, (int, float)) else now_ms()
        dt = datetime.fromtimestamp(ts_ms / 1000)
        day_dir = self._base_dir / kind / dt.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{dt.strftime('%H')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._cleanup_expired_if_new_day(dt.strftime("%Y-%m-%d"))

    def _write_snapshot_sync(self, kind: str, envelope: Mapping[str, Any]) -> None:
        kind_dir = self._base_dir / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        target = kind_dir / str(envelope["__file_name__"])
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(envelope["__payload__"], f, ensure_ascii=False, default=str, indent=2)
        os.replace(tmp, target)

    def _cleanup_expired_if_new_day(self, today: str) -> None:
        """跨天首次写入触发过期清理（每天至多一次）。"""
        if self._last_cleanup_date is None:
            self._last_cleanup_date = today
            return
        if self._last_cleanup_date != today:
            self._last_cleanup_date = today
            try:
                self._cleanup_expired()
            except Exception:  # noqa: BLE001 清理失败不影响写入
                logger.exception("诊断记录每日清理失败")

    def _cleanup_expired(self) -> None:
        """删除超过保留期的天目录（按目录名日期判定，快照文件不参与）。"""
        if self._retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime("%Y-%m-%d")
        if self._base_dir.exists():
            for kind_dir in self._base_dir.iterdir():
                if not kind_dir.is_dir():
                    continue
                # 天目录在 kind 目录下（logs/<kind>/<YYYY-MM-DD>/）；快照等
                # 非日期形态条目不匹配、不清理
                for day_dir in kind_dir.iterdir():
                    if day_dir.is_dir() and _is_date_dir_name(day_dir.name) and day_dir.name < cutoff:
                        shutil.rmtree(day_dir, ignore_errors=True)
                        logger.info(f"已清理过期诊断记录目录: {day_dir}")


def _is_date_dir_name(name: str) -> bool:
    """目录名是否为 YYYY-MM-DD 日期形态（清理只认日期目录，防误伤）。"""
    if len(name) != 10 or name[4] != "-" or name[7] != "-":
        return False
    try:
        datetime.strptime(name, "%Y-%m-%d")
        return True
    except ValueError:
        return False


__all__ = ["DiagnosticsWriter"]
