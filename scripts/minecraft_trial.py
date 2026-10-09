"""游戏 Agent 实测运行器：下达一项任务，等它结束，再汇总这次试跑的真实效果与请求开销。

用法（Amaidesu 与游戏都已启动）：

    uv run python scripts/minecraft_trial.py --instruction "用蜂房现成的机器做一个蜂蜜胶，收进背包" \\
        --expect-item simulated:honey_glue

- ``--via delegate``（默认）直接给游戏 Agent 派任务，不经过主播；``--via danmaku`` 注入一条调试弹幕，
  走主播决策、委派、游戏执行的完整链路。
- ``--expect-item`` 在任务前后经 MaiCraft v1 的 ``observe(what=self)`` 读背包，按实际数量变化判断成品是否到手。
- 汇总写到 ``data/trials/<时间>.json`` 并打印：结果、耗时、各档位请求数与 RPM、token、整理次数、
  被拒调用和游戏 Agent 的调用分布。只读数据库与日志，不改动任何运行状态（超时取消需显式 ``--cancel-on-timeout``）。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"succeeded", "failed", "cancelled", "timeout"}
# 任务停在待定夺时游戏侧已上报卡点，试跑到此为止，交由人决定
STOPPING = TERMINAL | {"waiting_for_decision"}
# MaiCraft v1 的工具调用失败里，这两种是请求本身写错了（参数不对、能力名不存在），算作被拒；
# 不在世界里、游戏腾不出手、程序出错不是模型的错，不计入。
REJECTION_MARKERS = (
    "invalid_parameter",
    "unknown_ability",
)


def http_json(
    method: str, url: str, body: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None
) -> Any:
    """发一次 JSON 请求；HTTP 错误连同响应正文一起抛出，便于看清控制面拒绝的原因。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{method} {url} -> {error.code}: {error.read().decode('utf-8', 'replace')}") from error


class MaicraftMcp:
    """只读访问 MaiCraft MCP：用来在任务前后读背包，不提交任何游戏动作。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self.session = ""

    def _post(self, payload: Dict[str, Any]) -> Any:
        headers = {"Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-06-18"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.url, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=30) as response:
            if not self.session:
                self.session = response.headers.get("Mcp-Session-Id", "")
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None

    def connect(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "minecraft-trial", "version": "1"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def item_count(self, item_id: str) -> Optional[int]:
        """读背包里某物品的总数；读不到返回 None（按未知处理，不当成 0）。

        用 v1 的 observe(what=self)：背包按物品合并成 {item, count}；回复是 {ok, data} 信封，
        角色不在世界里等调用失败时 ok 为 false，同样按读不到处理。
        """
        reply = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "observe", "arguments": {"what": "self"}},
            }
        )
        try:
            body = json.loads(reply["result"]["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None
        data = body.get("data") if isinstance(body, dict) and body.get("ok") else None
        inventory = data.get("inventory") if isinstance(data, dict) else None
        if not isinstance(inventory, list):
            return None
        return sum(int(row.get("count", 0)) for row in inventory if row.get("item") == item_id)


def find_task(dashboard: str, task_id: str) -> Optional[Dict[str, Any]]:
    snapshot = http_json("GET", f"{dashboard}/api/v1/tasks")
    for card in (snapshot.get("running") or []) + (snapshot.get("finished") or []):
        if card.get("task_id") == task_id:
            return card
    return None


def newest_minecraft_task(dashboard: str, since_ms: int) -> Optional[Dict[str, Any]]:
    """弹幕链路下由主播发起的委派：取开测之后最早登记、执行者是 minecraft 的任务。"""
    snapshot = http_json("GET", f"{dashboard}/api/v1/tasks")
    cards = [
        card
        for card in (snapshot.get("running") or []) + (snapshot.get("finished") or [])
        if card.get("executor") == "minecraft" and int(card.get("created_at_ms") or 0) >= since_ms
    ]
    return min(cards, key=lambda card: int(card.get("created_at_ms") or 0)) if cards else None


def request_metrics(t0_ms: int, t1_ms: int) -> Dict[str, Any]:
    """按档位统计这段时间的 LLM 请求：次数、RPM、token、缓存命中、延迟；游戏 Agent 另列整理与调用分布。"""
    database = sqlite3.connect(f"file:{ROOT / 'data' / 'amaidesu.db'}?mode=ro", uri=True)
    rows = database.execute(
        "select timestamp_ms, profile_name, prompt_tokens, completion_tokens, cache_hit_tokens, latency_ms, tool_calls "
        "from llm_requests where timestamp_ms between ? and ? order by timestamp_ms",
        (t0_ms, t1_ms),
    ).fetchall()
    minutes = max((t1_ms - t0_ms) / 60000, 1e-6)
    profiles: Dict[str, List[Any]] = collections.defaultdict(list)
    for row in rows:
        profiles[row[1]].append(row)
    summary: Dict[str, Any] = {"total_requests": len(rows), "rpm": round(len(rows) / minutes, 2), "profiles": {}}
    for name, items in profiles.items():
        prompt = [item[2] or 0 for item in items]
        summary["profiles"][name] = {
            "requests": len(items),
            "rpm": round(len(items) / minutes, 2),
            "prompt_tokens_median": int(statistics.median(prompt)),
            "prompt_tokens_max": max(prompt),
            "completion_tokens_total": sum(item[3] or 0 for item in items),
            "cache_hit_ratio": round(sum(item[4] or 0 for item in items) / max(1, sum(prompt)), 3),
            "latency_seconds_total": round(sum(item[5] or 0 for item in items) / 1000, 1),
        }
    calls: collections.Counter[str] = collections.Counter()
    compaction = 0
    for _, profile, *_rest, tool_calls in rows:
        if profile != "minecraft":
            continue
        parsed = json.loads(tool_calls or "[]")
        if not parsed:
            compaction += 1
            continue
        for call in parsed:
            function = call.get("function", call)
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            name = function.get("name", "?")
            # 按 v1 工具细分：下达的能力、看的对象、对目标做的操作，便于看出模型把调用花在了哪里。
            if name == "maicraft_execute":
                name += ":" + str((arguments.get("goal") or {}).get("ability"))
            elif name == "maicraft_observe":
                name += ":" + str(arguments.get("what"))
            elif name == "maicraft_goal":
                name += ":" + str(arguments.get("operation"))
            calls[name] += 1
    summary["minecraft_compactions"] = compaction
    summary["minecraft_calls"] = dict(calls.most_common())
    return summary


def log_rejections(t0: datetime.datetime, t1: datetime.datetime) -> int:
    """数这段时间 MCP 回执里的参数/字段拒收次数。"""
    count = 0
    for day in {t0.date(), t1.date()}:
        path = ROOT / "logs" / f"amaidesu_{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = datetime.datetime.fromisoformat(entry["timestamp"]).replace(tzinfo=None)
            if t0 <= stamp <= t1 and entry.get("module") == "McpClient":
                if any(marker in entry.get("message", "") for marker in REJECTION_MARKERS):
                    count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="下达一项游戏任务并汇总试跑结果")
    parser.add_argument("--instruction", required=True, help="任务指令（delegate）或弹幕原文（danmaku）")
    parser.add_argument("--via", choices=("delegate", "danmaku"), default="delegate")
    parser.add_argument("--source", default="调试观众", help="danmaku 模式的观众昵称")
    parser.add_argument("--expect-item", default="", help="期望到手的物品 ID，任务前后读背包比较")
    parser.add_argument("--timeout", type=int, default=600, help="最长等待秒数")
    parser.add_argument("--dashboard", default="http://127.0.0.1:60214")
    parser.add_argument("--mcp", default="http://127.0.0.1:8766/mcp")
    parser.add_argument("--cancel-on-timeout", action="store_true", help="超时后取消该任务")
    parser.add_argument("--report", default="", help="汇总 JSON 路径，缺省写到 data/trials/")
    args = parser.parse_args()

    state = http_json("GET", f"{args.dashboard}/api/v1/agents/minecraft/state")
    mcp = MaicraftMcp(args.mcp)
    before: Optional[int] = None
    if args.expect_item:
        mcp.connect()
        before = mcp.item_count(args.expect_item)

    started = datetime.datetime.now()
    t0_ms = int(started.timestamp() * 1000)
    task_id = ""
    if args.via == "delegate":
        accepted = http_json(
            "POST", f"{args.dashboard}/api/v1/agents/minecraft/delegate", {"instruction": args.instruction}
        )
        task_id = accepted["task_id"]
    else:
        http_json(
            "POST", f"{args.dashboard}/api/v1/debug/inject-message", {"text": args.instruction, "source": args.source}
        )
    print(f"已下达（{args.via}），开始等待…", flush=True)

    card: Optional[Dict[str, Any]] = None
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not task_id:
            found = newest_minecraft_task(args.dashboard, t0_ms)
            task_id = found["task_id"] if found else ""
        if task_id:
            card = find_task(args.dashboard, task_id)
            if card and card.get("status") in STOPPING:
                break
        time.sleep(3)
    timed_out = not card or card.get("status") not in STOPPING
    if timed_out and task_id and args.cancel_on_timeout:
        http_json("POST", f"{args.dashboard}/api/v1/agents/minecraft/tasks/{task_id}/cancel")

    finished = datetime.datetime.now()
    t1_ms = int(finished.timestamp() * 1000)
    after = mcp.item_count(args.expect_item) if args.expect_item else None
    report: Dict[str, Any] = {
        "instruction": args.instruction,
        "via": args.via,
        "agent_state_at_start": state,
        "task_id": task_id,
        "status": card.get("status") if card else "not_found",
        "summary": card.get("summary") if card else "",
        "timed_out": timed_out,
        "seconds": round((t1_ms - t0_ms) / 1000, 1),
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "requests": request_metrics(t0_ms, t1_ms),
        "mcp_rejections": log_rejections(started, finished),
    }
    if args.expect_item:
        report["expected_item"] = {
            "item_id": args.expect_item,
            "before": before,
            "after": after,
            "gained": None if before is None or after is None else after - before,
        }
    path = Path(args.report) if args.report else ROOT / "data" / "trials" / f"{started:%Y%m%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"汇总已写入 {path}")
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
