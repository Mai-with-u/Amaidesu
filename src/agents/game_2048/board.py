"""2048 规则引擎（纯逻辑，零框架依赖）

经典 2048 规则的唯一实现：滑动、同值合并（每块每回合至多合并一次）、
有效移动后随机生成新块（2 概率 0.9 / 4 概率 0.1）、无空格且无可合并
即终局。

纯代码承载规则的工程理由：LLM "心算"棋盘必然漂移，规则的每一次状态
迁移都必须由确定性代码完成；LLM（无论主播还是策略）只消费这里的
结果。随机性经构造注入 ``random.Random`` 实例，固定种子即可复现整局。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import List, Optional, Sequence, Tuple

# 棋盘边长（经典 2048 为 4x4；改值需同步调整 strategy 的权重表尺寸）
SIZE = 4


class MoveDirection(StrEnum):
    """四个滑动方向（成员值即工具入参与事件载荷的方向拼写）。"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


ALL_DIRECTIONS: Tuple[MoveDirection, ...] = (
    MoveDirection.UP,
    MoveDirection.DOWN,
    MoveDirection.LEFT,
    MoveDirection.RIGHT,
)


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """一次滑动尝试的结果。

    Attributes:
        moved: 棋盘是否发生了变化（移动或合并）；无效移动棋盘原地不动
            且不生成新块
        gained: 本次合并的得分增量（无合并为 0）
    """

    moved: bool
    gained: int


def slide_line(line: Sequence[int]) -> Tuple[List[int], int]:
    """单行向左压缩合并：非零块靠左、相邻同值合并一次、得分累加合并值。

    经典规则要点：合并后的块不再与本回合后续块二次合并——
    ``[2, 2, 4, 0]`` 得 ``[4, 4, 0, 0]`` 而非 ``[8, 0, 0, 0]``。

    Args:
        line: 一行 4 格（0 表示空格）

    Returns:
        ``(新行, 合并得分)``
    """
    tiles = [v for v in line if v != 0]
    result: List[int] = []
    gained = 0
    i = 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            merged = tiles[i] * 2
            result.append(merged)
            gained += merged
            i += 2
        else:
            result.append(tiles[i])
            i += 1
    result.extend([0] * (len(line) - len(result)))
    return result, gained


def apply_direction(grid: Sequence[Sequence[int]], direction: MoveDirection) -> Tuple[List[List[int]], int]:
    """对整盘执行一次方向滑动（纯函数，不改入参）。

    Returns:
        ``(新棋盘, 合并得分)``；棋盘无变化时新棋盘与入参内容相同、得分为 0。
    """
    work = [list(row) for row in grid]

    # 统一变换到"行方向朝左"的坐标系处理，再逆变换回去
    if direction is MoveDirection.RIGHT:
        work = [list(reversed(row)) for row in work]
    elif direction is MoveDirection.UP:
        work = [list(col) for col in zip(*work, strict=True)]
    elif direction is MoveDirection.DOWN:
        work = [list(reversed(col)) for col in zip(*work, strict=True)]

    gained_total = 0
    moved = False
    slid: List[List[int]] = []
    for row in work:
        new_row, gained = slide_line(row)
        gained_total += gained
        if new_row != row:
            moved = True
        slid.append(new_row)

    if not moved:
        return [list(row) for row in grid], 0

    result = slid
    if direction is MoveDirection.RIGHT:
        result = [list(reversed(row)) for row in result]
    elif direction is MoveDirection.UP:
        result = [list(col) for col in zip(*result, strict=True)]
    elif direction is MoveDirection.DOWN:
        # 正变换为"转置后逐行反转"，逆变换按反序：先逐行反转再转置
        result = [list(col) for col in zip(*[reversed(row) for row in result], strict=True)]
    return result, gained_total


class Board2048:
    """一局 2048 的棋盘状态与规则执行器。

    Attributes:
        grid: 棋盘矩阵（0 表示空格）
        score: 累计得分（合并值累加）
        moves: 已执行的有效移动步数
        over: 是否终局（无空格且任一方向均无可合并）
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        """Args:
        rng: 新块生成的随机源；注入固定种子的实例即可复现整局，
            缺省用模块级随机源
        """
        self._rng = rng if rng is not None else random.Random()
        self.grid: List[List[int]] = [[0] * SIZE for _ in range(SIZE)]
        self.score: int = 0
        self.moves: int = 0
        self.over: bool = False

    # ------------------------------------------------------------------
    # 局面操作
    # ------------------------------------------------------------------

    def new_game(self) -> None:
        """清盘并生成两个初始块，进入新的一局。"""
        self.grid = [[0] * SIZE for _ in range(SIZE)]
        self.score = 0
        self.moves = 0
        self.over = False
        self._spawn()
        self._spawn()

    def apply_move(self, direction: MoveDirection) -> MoveOutcome:
        """执行一次方向滑动。

        终局或无效移动（棋盘无变化）时不生成新块、不计步，棋盘原地
        不动——调用方据 ``moved`` 判定本次按键是否生效。

        Args:
            direction: 滑动方向

        Returns:
            滑动结果（是否生效 + 得分增量）
        """
        if self.over:
            return MoveOutcome(moved=False, gained=0)

        new_grid, gained = apply_direction(self.grid, direction)
        if new_grid == self.grid:
            # 无效移动也做终局判定：死局可能由上一步的新块生成直接造成，
            # 若不在无效路径补判，对死局按任何键都永远不会亮终局
            if not self._can_move():
                self.over = True
            return MoveOutcome(moved=False, gained=0)

        self.grid = new_grid
        self.score += gained
        self.moves += 1
        self._spawn()
        if not self._can_move():
            self.over = True
        return MoveOutcome(moved=True, gained=gained)

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def max_tile(self) -> int:
        """当前盘面最大块值（空盘为 0）。"""
        return max((v for row in self.grid for v in row), default=0)

    def empty_cells(self) -> int:
        """空格数量。"""
        return sum(1 for row in self.grid for v in row if v == 0)

    def to_matrix(self) -> List[List[int]]:
        """导出棋盘矩阵的拷贝（防外部改写内部状态）。"""
        return [list(row) for row in self.grid]

    def render(self) -> str:
        """渲染人类/LLM 可读的文本棋盘（等宽对齐，空格以 ``.`` 表示）。"""
        width = max(len(str(self.max_tile)), 4)
        lines = [f"score={self.score} moves={self.moves}"]
        for row in self.grid:
            lines.append(" ".join(str(v).rjust(width) if v else ".".rjust(width) for v in row))
        if self.over:
            lines.append("游戏结束")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部机制
    # ------------------------------------------------------------------

    def _spawn(self) -> None:
        """随机空格生成一个新块（2 概率 0.9 / 4 概率 0.1）；满盘为静默空操作。"""
        empties = [(r, c) for r in range(SIZE) for c in range(SIZE) if self.grid[r][c] == 0]
        if not empties:
            return
        r, c = self._rng.choice(empties)
        self.grid[r][c] = 2 if self._rng.random() < 0.9 else 4

    def _can_move(self) -> bool:
        """是否仍存在可行移动：有空格，或任一方向能产生变化。"""
        if self.empty_cells() > 0:
            return True
        return any(apply_direction(self.grid, d)[0] != self.grid for d in ALL_DIRECTIONS)


__all__ = [
    "ALL_DIRECTIONS",
    "SIZE",
    "Board2048",
    "MoveDirection",
    "MoveOutcome",
    "apply_direction",
    "slide_line",
]
