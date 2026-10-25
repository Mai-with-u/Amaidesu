"""直播画面采集器的 OBS 诊断：一步步检查到能截出一帧为止，并实测截图耗时。

在开着 OBS 的那台机器上运行。默认读 ``config/collectors.toml`` 的 ``[live_frame]`` 段
（只读，不改任何配置），命令行参数可覆盖。每一步失败都给出照着改的原因；全部通过后
连续截几帧，报告单帧耗时、体积与是否全黑，用来判断能不能当常态截图来源。

用法：
    uv run python scripts/check_obs_frame.py
    uv run python scripts/check_obs_frame.py --port 4455 --password 你的密码 --source 游戏画面 --save frame.jpg
"""

from __future__ import annotations

import argparse
import base64
import io
import socket
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from src.modules.collectors.frame.live_frame_collector import (  # noqa: E402
    LiveFrameCollector,
    describe_live_frame_obs_error,
)
from src.modules.logging import silence_third_party_logger  # noqa: E402

# 连续截几帧算平均耗时
_SHOTS = 5
# 单帧耗时超过它就提示调低分辨率或拉长间隔（毫秒）
_SLOW_SHOT_MS = 300
# 缩略图平均亮度低于它视为全黑
_BLACK_LEVEL = 3


def _load_config(args: argparse.Namespace) -> Dict[str, Any]:
    """[live_frame] 段 + 采集器默认值 + 命令行覆盖。"""
    section: Dict[str, Any] = {}
    path = PROJECT_ROOT / "config" / "collectors.toml"
    if path.exists():
        # utf-8-sig 兼容用户编辑器写出的 BOM 头：裸 tomllib 会把它当非法语句在第 1 列报错
        section = dict(tomllib.loads(path.read_text(encoding="utf-8-sig")).get("live_frame") or {})
    config = LiveFrameCollector.ConfigSchema.from_dict(section).model_dump()
    for key, value in (
        ("obs_host", args.host),
        ("obs_port", args.port),
        ("obs_password", args.password),
        ("obs_source", args.source),
        ("max_width", args.width),
    ):
        if value is not None:
            config[key] = value
    return config


def _fail(step: str, error: BaseException, config: Dict[str, Any]) -> int:
    reason = describe_live_frame_obs_error(
        error, host=config["obs_host"], port=config["obs_port"], source=config["obs_source"]
    )
    print(f"  [失败] {step}失败：{reason}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="直播画面采集器的 OBS 诊断")
    parser.add_argument("--host", help="OBS WebSocket 主机（默认取 [live_frame].obs_host）")
    parser.add_argument("--port", type=int, help="OBS WebSocket 端口")
    parser.add_argument("--password", help="OBS WebSocket 密码（不会被打印）")
    parser.add_argument("--source", help="要截的场景或来源名；空串=当前节目场景")
    parser.add_argument("--width", type=int, help="截图宽度（像素）")
    parser.add_argument("--save", help="把第一帧存成文件，便于肉眼确认画面")
    args = parser.parse_args()
    # Windows 控制台多是 GBK：个别字符编码不了时替换掉，不让诊断因打印崩溃
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    config = _load_config(args)
    host, port = config["obs_host"], config["obs_port"]
    print(f"目标 OBS：{host}:{port}，密码{'已填' if config['obs_password'] else '未填'}，宽度 {config['max_width']}")

    print("[1/5] 端口能不能连上")
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError as error:
        return _fail("连接端口", error, config)
    print("  [通过] 端口可达")

    print("[2/5] WebSocket 握手与身份验证")
    silence_third_party_logger("obswebsocket")
    try:
        # 可选重型依赖延迟加载：脚本其余诊断步骤（端口探测等）不依赖它也能给出结果
        from src.modules.studio.obs.obs_client import ObsClient, obs_requests

        client = ObsClient(host=host, port=port, password=config["obs_password"] or "", timeout=3)
        client.connect()
    except Exception as error:  # noqa: BLE001 - 诊断脚本把任何失败翻译成原因后退出
        return _fail("身份验证", error, config)
    print("  [通过] 已连上并通过验证")

    try:
        print("[3/5] OBS 版本与支持的图片格式")
        version = client.call(obs_requests.GetVersion())
        formats: List[str] = [str(item).lower() for item in version.getSupportedImageFormats() or []]
        print(
            f"  OBS {version.getObsVersion()}，obs-websocket {version.getObsWebSocketVersion()}；"
            f"JPEG {'支持' if 'jpg' in formats or 'jpeg' in formats else '不支持（将用 PNG，体积更大）'}"
        )
        video = client.call(obs_requests.GetVideoSettings())
        print(
            f"  画布 {video.getBaseWidth()}×{video.getBaseHeight()}，输出 {video.getOutputWidth()}×{video.getOutputHeight()}"
        )

        print("[4/5] 要截的场景或来源")
        program = client.call(obs_requests.GetCurrentProgramScene()).getCurrentProgramSceneName()
        scenes = [item.get("sceneName", "") for item in client.call(obs_requests.GetSceneList()).getScenes() or []]
        source = config["obs_source"] or program
        print(f"  当前节目场景：{program}；全部场景：{'、'.join(reversed(scenes)) or '（无）'}")
        print(f"  将截：{source}{'（当前节目场景）' if not config['obs_source'] else ''}")

        print(f"[5/5] 连续截 {_SHOTS} 帧")
        fmt = next((item for item in ("jpg", "jpeg", "png") if item in formats), "jpg")
        width = int(config["max_width"])
        height = max(8, round(width * video.getOutputHeight() / video.getOutputWidth()))
        costs: List[float] = []
        first = b""
        for _ in range(_SHOTS):
            started = time.perf_counter()
            response = client.call(
                obs_requests.GetSourceScreenshot(
                    sourceName=source,
                    imageFormat=fmt,
                    imageWidth=width,
                    imageHeight=height,
                    imageCompressionQuality=70,
                )
            )
            costs.append((time.perf_counter() - started) * 1000)
            image = base64.b64decode(str(response.getImageData()).partition(",")[2])
            first = first or image
    except Exception as error:  # noqa: BLE001 - 诊断脚本把任何失败翻译成原因后退出
        return _fail("截图", error, config)
    finally:
        client.disconnect()

    with Image.open(io.BytesIO(first)) as picture:
        size = picture.size
        brightness = sum(picture.convert("L").resize((32, 18)).tobytes()) / (32 * 18)
    average = sum(costs) / len(costs)
    print(
        f"  [通过] 单帧 {size[0]}×{size[1]}，约 {len(first) // 1024} KB，平均耗时 {average:.0f} ms（最慢 {max(costs):.0f} ms）"
    )
    if args.save:
        target = Path(args.save)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(first)
            print(f"  已保存第一帧：{target.resolve()}")
        except OSError as error:
            print(f"  [注意] 第一帧没能保存到 {target}：{error}")
    if brightness < _BLACK_LEVEL:
        print("  [注意] 画面几乎全黑：游戏/窗口捕获来源可能没挂上，先在 OBS 预览里确认能看到画面")
    if average > _SLOW_SHOT_MS:
        print(
            f"  [注意] 单帧耗时偏高：调小 [live_frame].max_width，或把 interval_ms 调大（当前 {config['interval_ms']} ms）"
        )
    per_minute = 60_000 // int(config["interval_ms"])
    print(f"结论：可以作为直播画面来源。按当前间隔每分钟最多截 {per_minute} 帧，画面没明显变化时不发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
