"""面捕合成器离线回放：用事件历史日志驱动合成器，输出曲线图（HTML）与指标

不需要打开 VTS。在仓库根目录运行，例如：

    uv run python scripts/avatar_tracking_replay.py logs/events/2026-10-18/13.jsonl -o docs/tmp/replay.html

可以给多个文件（按时间合并）；``--seed`` 固定眨眼、眼神与漫步的随机序列，便于前后对比。
调参直接改 ``config/avatar.toml`` 的 ``[tracking]`` 段，加 ``--config`` 读进来。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.avatar.tracking.config import TrackingConfig  # noqa: E402 - 先把仓库根放进导入路径
from src.modules.avatar.tracking.replay import load_event_files, render_html, replay  # noqa: E402
from src.modules.logging import configure_from_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="面捕合成器离线回放")
    parser.add_argument("events", nargs="+", type=Path, help="事件历史 JSONL 文件（logs/events/<日期>/<小时>.jsonl）")
    parser.add_argument("-o", "--output", type=Path, default=Path("docs/tmp/tracking-replay.html"), help="输出 HTML")
    parser.add_argument("--config", type=Path, help="从 avatar.toml 读 [tracking] 段（缺省用默认调参）")
    parser.add_argument("--seed", type=int, default=0, help="随机种子")
    args = parser.parse_args()
    # 回放会发上千条事件，事件总线的调试日志只会刷屏：只留警告以上，不写日志文件
    configure_from_config({"enabled": False, "console_level": "WARNING"})

    config = TrackingConfig()
    if args.config is not None:
        with args.config.open("rb") as fh:
            config = TrackingConfig.from_dict(tomllib.load(fh).get("tracking", {}))

    events = load_event_files(args.events)
    result = asyncio.run(replay(events, config, seed=args.seed))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(result, title=f"面捕合成器回放（{len(events)} 条事件）"), encoding="utf-8")
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    print(f"曲线图: {args.output}")


if __name__ == "__main__":
    main()
