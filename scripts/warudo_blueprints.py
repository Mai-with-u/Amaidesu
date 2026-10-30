"""生成 Warudo 蓝图，导入 Warudo 后即可接收 Amaidesu 的头部动作、站姿与风格化标记。

脸、眼、头不经蓝图：Amaidesu 按 iFacialMocap 协议把面捕数据发给 Warudo 的 iFacialMocap 接收器。
蓝图只接 WebSocket 通道上的动作：头部动作与站姿（播放 Warudo 自带动画）总会生成；
``config/avatar.toml`` 的 ``[platform.warudo.markers]`` 登记了风格化标记（脸红、眼泪……）时，
再生成一个把标记值写到模型形状上的蓝图（只读配置，与适配器用同一份，动作名天然对齐）。
在 Warudo 的蓝图页选"导入蓝图"，逐个导入生成的文件即可。

风格化标记两种写法：
    --mode shape  直接写模型网格上的同名形状（默认）
    --mode vrm    走 VRM 表情代理写同名的自定义表情

导入后可以用 ``--probe`` 连上 Warudo 逐项试发一遍（头部动作、站姿、风格化标记），
对着画面核对每一项有没有动。

用法：
    uv run python scripts/warudo_blueprints.py
    uv run python scripts/warudo_blueprints.py --scene "D:/.../Warudo_Data/StreamingAssets/Scenes/我的场景.json"
    uv run python scripts/warudo_blueprints.py --probe ws://localhost:19190
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path
from typing import Dict

import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.modules.avatar.platform.warudo.blueprint_builder import (  # noqa: E402
    HEAD_MOTIONS,
    MODES,
    POSES,
    build_blueprints,
    find_scene_character,
)
from src.modules.avatar.platform.warudo.tracking_driver import marker_action  # noqa: E402


def _load_markers(config_path: Path) -> Dict[str, str]:
    """读配置里登记的风格化标记；文件或段缺失时为空（只生成动作蓝图）"""
    if not config_path.is_file():
        print(f"未找到 {config_path}，只生成头部动作与站姿蓝图")
        return {}
    # utf-8-sig 兼容带 BOM 的文件（历史版本管线写回带 BOM；tomllib 拒绝 BOM）
    data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    markers = data.get("platform", {}).get("warudo", {}).get("markers") or {}
    return {str(name): str(shape) for name, shape in markers.items()}


async def _probe(markers: Dict[str, str], uri: str, hold_s: float) -> None:
    """导入蓝图后逐项试一遍：头部动作 → 站姿 → 风格化标记，每项停一会儿方便肉眼核对"""
    async with websockets.connect(uri) as ws:

        async def send(action: str, data: object) -> None:
            await ws.send(json.dumps({"action": action, "data": data}))

        for action in (*HEAD_MOTIONS, *POSES):
            print(f"动作 {action}")
            await send(action, 1)
            await asyncio.sleep(hold_s * 2)
        for name, shape in markers.items():
            print(f"风格化标记 {name} → {shape}")
            await send(marker_action(name), 1.0)
            await asyncio.sleep(hold_s)
            await send(marker_action(name), 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Warudo 蓝图（头部动作、站姿、风格化标记）")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "avatar.toml")
    parser.add_argument("--mode", choices=MODES, default="shape")
    parser.add_argument("--scene", type=Path, help="Warudo 场景文件；给出时角色引用取场景里的第一个角色")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "warudo_blueprints")
    parser.add_argument("--probe", metavar="WS_URI", help="不生成，改为连上 Warudo（如 ws://localhost:19190）逐项试发")
    parser.add_argument("--hold", type=float, default=1.5, help="试发时每项停留秒数")
    args = parser.parse_args()

    markers = _load_markers(args.config)
    if args.probe:
        asyncio.run(_probe(markers, args.probe, args.hold))
        return 0
    character = None
    if args.scene is not None:
        character = find_scene_character(args.scene)
        if character is None:
            print(f"{args.scene} 里没有角色资源，先在 Warudo 里加载角色并保存场景")
            return 1
    blueprints = build_blueprints(markers, mode=args.mode, character=character)

    args.out.mkdir(parents=True, exist_ok=True)
    for file_name, blueprint in blueprints.items():
        path = args.out / file_name
        path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成 {path}（{len(blueprint['nodes'])} 个节点）")
    if character:
        print(f"角色引用：{character['name']}（{character['id']}）；在 Warudo 蓝图页逐个导入以上文件")
    else:
        # 蓝图按资源 ID 找角色：删过或重新添加过角色的场景，角色 ID 已经变了，默认 ID 会指空、整套蓝图不动
        print("角色引用：新建场景的默认角色。若在 Warudo 里删过或重新添加过角色，")
        print("请先保存场景，再加 --scene <场景文件> 重新生成，否则蓝图找不到角色、什么都不会动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
