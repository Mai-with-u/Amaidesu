"""表情差分表切分工具：把网格排版的差分表图切成按情绪命名的单张差分图。

配合 expression_sheet 虚拟形象平台的素材准备流程使用——GPT 等图像工具按
2x2 网格一批生成 4 个表情，本脚本按等分网格切出每格并以 `<情绪名>.png`
落盘，输出目录可直接作为平台配置的 image_dir。

用法：

    uv run python scripts/slice_expression_sheet.py sheet1.png \
        --names neutral,happy,sad,angry --out data/avatar/expression_sheet

- 格子顺序按行排列：左上 → 右上 → 左下 → 右下（与生成提示词的批次顺序一致）。
- 单张名单（--names 只有 1 个名字）时不切分，整图原样另存为该名字。
- `--grid 2x3` 可显式指定列数x行数；默认按名字数量自动取 2 列网格。
- 名字数量少于格子数量时只切前 N 格（多余格子忽略并提示）。
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把网格差分表切分为按情绪命名的单图")
    parser.add_argument("sheet", type=Path, help="差分表图片路径（png/jpg/webp）")
    parser.add_argument(
        "--names",
        required=True,
        help="按格子顺序的名字清单，逗号分隔（如 neutral,happy,sad,angry）",
    )
    parser.add_argument("--out", type=Path, required=True, help="输出目录（可直接作为 image_dir）")
    parser.add_argument(
        "--grid",
        default=None,
        help="显式网格，格式 列数x行数（如 2x2）；默认按名字数量自动取 2 列",
    )
    parser.add_argument(
        "--inset",
        type=int,
        default=0,
        help="每格四边内缩的像素数（去除邻格越过等分线的渗色），默认 0",
    )
    return parser.parse_args(argv)


def _resolve_grid(name_count: int, grid_arg: str | None) -> tuple[int, int]:
    """决定 (列数, 行数)：显式参数优先，否则按名字数量取 2 列网格。"""
    if grid_arg is None:
        if name_count == 1:
            return (1, 1)
        cols = 2
        rows = math.ceil(name_count / cols)
        return (cols, rows)
    text = grid_arg.lower().strip().replace("×", "x")
    cols_text, sep, rows_text = text.partition("x")
    if not sep or not cols_text.isdigit() or not rows_text.isdigit():
        raise ValueError(f"--grid 格式应为 列数x行数（如 2x2），收到：{grid_arg!r}")
    cols, rows = int(cols_text), int(rows_text)
    if cols < 1 or rows < 1:
        raise ValueError(f"--grid 行列数必须为正整数，收到：{grid_arg!r}")
    return (cols, rows)


def _normalize_names(names_arg: str) -> list[str]:
    names = [item.strip().removesuffix(".png") for item in names_arg.split(",") if item.strip()]
    if not names:
        raise ValueError("--names 至少需要一个名字")
    return names


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    names = _normalize_names(args.names)
    cols, rows = _resolve_grid(len(names), args.grid)

    if cols * rows < len(names):
        print(f"错误：网格 {cols}x{rows} 只有 {cols * rows} 格，装不下 {len(names)} 个名字", file=sys.stderr)
        return 2

    image = Image.open(args.sheet)
    print(f"读取 {args.sheet.name}：{image.width}x{image.height}，网格 {cols}x{rows}")

    args.out.mkdir(parents=True, exist_ok=True)
    cell_w = image.width // cols
    cell_h = image.height // rows
    if args.inset < 0 or args.inset * 2 >= cell_w or args.inset * 2 >= cell_h:
        print(f"错误：--inset {args.inset} 超出格子尺寸（{cell_w}x{cell_h}）允许范围", file=sys.stderr)
        return 2
    saved: list[Path] = []
    for index, name in enumerate(names):
        col = index % cols
        row = index // cols
        box = (
            col * cell_w + args.inset,
            row * cell_h + args.inset,
            (col + 1) * cell_w - args.inset,
            (row + 1) * cell_h - args.inset,
        )
        target = args.out / f"{name}.png"
        image.crop(box).save(target)
        saved.append(target)
        print(f"  [{row},{col}] -> {target}（{cell_w}x{cell_h}）")

    skipped = cols * rows - len(names)
    if skipped > 0:
        print(f"提示：末尾 {skipped} 格未使用")
    print(f"完成：共 {len(saved)} 张 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
