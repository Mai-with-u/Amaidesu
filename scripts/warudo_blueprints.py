"""按表情档案生成 Warudo 蓝图，导入 Warudo 后即可接收 Amaidesu 的表情、口型、眨眼与头部动作。

读 ``config/avatar.toml`` 的 ``[platform.warudo.expression]`` 段（只读，与适配器用同一份档案，
动作名天然对齐），生成四个蓝图 JSON：表情、口型、眨眼与移眼、头部动作与站姿。
在 Warudo 的蓝图页选"导入蓝图"，逐个导入生成的文件即可。

三种写法：
    --mode vrm    走 VRM 表情代理写 VRM 1.0 标准名（默认；VRM 1.0 模型通用）
    --mode vrm0   走 VRM 表情代理写 VRM 0.x 预设名（Joy / A / Blink 等；VRM 0.x 模型用）
    --mode shape  直接写模型网格上的同名形状（麦麦这类表情做在自定义形状上的模型）
不确定模型是哪一版时，Warudo 日志里"Available VRM blendshape clips"一行列出的就是它认的名字。

导入后可以用 ``--probe`` 连上 Warudo 逐项试发一遍（情绪、口型、眨眼移眼、头部动作、站姿），
对着画面核对每一项有没有动。

用法：
    uv run python scripts/warudo_blueprints.py
    uv run python scripts/warudo_blueprints.py --mode shape --scene "D:/.../Warudo_Data/StreamingAssets/Scenes/我的场景.json"
    uv run python scripts/warudo_blueprints.py --probe ws://localhost:19190
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

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
from src.modules.avatar.platform.warudo.expression_profile import (  # noqa: E402
    BLINK_KEY,
    EYE_SHIFT_KEYS,
    VOWEL_KEYS,
    WarudoExpressionConfig,
)


def _load_expression(config_path: Path) -> WarudoExpressionConfig:
    """读配置里的表情档案；文件或段缺失时用默认 VRM 标准档案"""
    if not config_path.is_file():
        print(f"未找到 {config_path}，按默认 VRM 标准档案生成")
        return WarudoExpressionConfig()
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("platform", {}).get("warudo", {}).get("expression")
    if section is None:
        print(f"{config_path} 里没有 [platform.warudo.expression]，按默认 VRM 标准档案生成")
        return WarudoExpressionConfig()
    return WarudoExpressionConfig.from_dict(section)


async def _probe(expression: WarudoExpressionConfig, uri: str, hold_s: float) -> None:
    """导入蓝图后逐项试一遍：情绪 → 口型 → 眨眼移眼 → 头部动作 → 站姿，每项停一会儿方便肉眼核对"""
    async with websockets.connect(uri) as ws:

        async def send(action: str, data: object) -> None:
            await ws.send(json.dumps({"action": action, "data": data}))

        for emotion, weights in expression.emotions.items():
            # 情绪：写入档案里的表情键 → 停一会儿 → 全部归零
            print(f"情绪 {emotion}: {weights or '（中性，无表情键）'}")
            for key, weight in weights.items():
                await send(key, weight)
            await asyncio.sleep(hold_s)
            for key in weights:
                await send(key, 0.0)
        for key in (*VOWEL_KEYS, BLINK_KEY, *EYE_SHIFT_KEYS):
            print(f"固定键 {key}")
            await send(key, 1.0)
            await asyncio.sleep(hold_s)
            await send(key, 0.0)
        for action in (*HEAD_MOTIONS, *POSES):
            print(f"动作 {action}")
            await send(action, 1)
            await asyncio.sleep(hold_s * 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="按表情档案生成 Warudo 蓝图")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "avatar.toml")
    parser.add_argument("--mode", choices=MODES, default="vrm")
    parser.add_argument("--scene", type=Path, help="Warudo 场景文件；给出时角色引用取场景里的第一个角色")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "warudo_blueprints")
    parser.add_argument("--probe", metavar="WS_URI", help="不生成，改为连上 Warudo（如 ws://localhost:19190）逐项试发")
    parser.add_argument("--hold", type=float, default=1.5, help="试发时每项停留秒数")
    args = parser.parse_args()

    expression = _load_expression(args.config)
    if args.probe:
        asyncio.run(_probe(expression, args.probe, args.hold))
        return 0
    character = None
    if args.scene is not None:
        character = find_scene_character(args.scene)
        if character is None:
            print(f"{args.scene} 里没有角色资源，先在 Warudo 里加载角色并保存场景")
            return 1
    blueprints = build_blueprints(expression, mode=args.mode, character=character)

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
