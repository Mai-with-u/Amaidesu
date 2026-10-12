"""2048 规则引擎测试（纯逻辑切片）

覆盖：
- slide_line 表驱动：压缩、单次合并、合并后不二次合并、得分
- apply_direction 四方向：左/右/上/下变换正确性、无效移动原样返回
- Board2048：new_game 初始块、apply_move 计分计步、无效移动不生成新块、
  终局判定（死局 over=True / 一步可走 over=False）
- render：含分数、步数、非零块值
"""

from __future__ import annotations

import random

import pytest

from src.agents.game_2048.board import (
    SIZE,
    Board2048,
    MoveDirection,
    apply_direction,
    slide_line,
)


# ---------------------------------------------------------------------------
# slide_line（向左压缩合并）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected", "gained"),
    [
        ([0, 0, 0, 0], [0, 0, 0, 0], 0),
        ([2, 0, 0, 0], [2, 0, 0, 0], 0),
        ([0, 0, 0, 2], [2, 0, 0, 0], 0),
        ([2, 2, 2, 2], [4, 4, 0, 0], 8),
        ([2, 0, 2, 4], [4, 4, 0, 0], 4),
        ([4, 4, 8, 8], [8, 16, 0, 0], 24),
        # 合并后的块不与本回合后续块二次合并
        ([2, 2, 4, 0], [4, 4, 0, 0], 4),
        # 四块两两合并从左配对
        ([2, 2, 2, 0], [4, 2, 0, 0], 4),
    ],
)
def test_slide_line_table(line: list[int], expected: list[int], gained: int) -> None:
    new_line, got = slide_line(line)
    assert new_line == expected
    assert got == gained


# ---------------------------------------------------------------------------
# apply_direction（方向变换）
# ---------------------------------------------------------------------------


def test_apply_direction_left() -> None:
    grid = [[2, 0, 2, 0], [0, 0, 0, 0], [4, 4, 0, 0], [0, 0, 0, 8]]
    new_grid, gained = apply_direction(grid, MoveDirection.LEFT)
    assert new_grid == [[4, 0, 0, 0], [0, 0, 0, 0], [8, 0, 0, 0], [8, 0, 0, 0]]
    assert gained == 12


def test_apply_direction_right() -> None:
    grid = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    new_grid, _ = apply_direction(grid, MoveDirection.RIGHT)
    assert new_grid == [[0, 0, 0, 4], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]


def test_apply_direction_up_and_down() -> None:
    grid = [[2, 0, 0, 0], [2, 0, 0, 0], [0, 0, 0, 0], [2, 0, 0, 0]]
    up_grid, up_gained = apply_direction(grid, MoveDirection.UP)
    assert up_grid == [[4, 0, 0, 0], [2, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    assert up_gained == 4
    down_grid, down_gained = apply_direction(grid, MoveDirection.DOWN)
    assert down_grid == [[0, 0, 0, 0], [0, 0, 0, 0], [2, 0, 0, 0], [4, 0, 0, 0]]
    assert down_gained == 4


def test_apply_direction_invalid_move_returns_original() -> None:
    grid = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    for direction in MoveDirection:
        new_grid, gained = apply_direction(grid, direction)
        assert new_grid == grid
        assert gained == 0


def test_apply_direction_does_not_mutate_input() -> None:
    grid = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    apply_direction(grid, MoveDirection.LEFT)
    assert grid[0] == [2, 2, 0, 0]


# ---------------------------------------------------------------------------
# Board2048
# ---------------------------------------------------------------------------


def test_new_game_spawns_two_tiles() -> None:
    board = Board2048(rng=random.Random(42))
    board.new_game()
    tiles = [v for row in board.grid for v in row if v != 0]
    assert len(tiles) == 2
    assert all(v in (2, 4) for v in tiles)
    assert board.score == 0
    assert board.moves == 0
    assert not board.over


def test_apply_move_scores_and_counts() -> None:
    board = Board2048(rng=random.Random(7))
    board.grid = [[2, 2, 0, 0], [0] * SIZE, [0] * SIZE, [0] * SIZE]
    outcome = board.apply_move(MoveDirection.LEFT)
    assert outcome.moved
    assert outcome.gained == 4
    assert board.score == 4
    assert board.moves == 1
    assert board.grid[0][0] == 4


def test_invalid_move_no_spawn_no_count() -> None:
    board = Board2048(rng=random.Random(7))
    board.grid = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    before = board.to_matrix()
    outcome = board.apply_move(MoveDirection.LEFT)
    assert not outcome.moved
    assert board.to_matrix() == before
    assert board.moves == 0
    assert board.score == 0


def test_game_over_on_dead_board() -> None:
    board = Board2048(rng=random.Random(1))
    board.grid = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    assert not board._can_move()  # noqa: SLF001 - 死局判定的直接验证
    outcome = board.apply_move(MoveDirection.LEFT)
    assert not outcome.moved
    assert board.over


def test_not_over_when_one_move_available() -> None:
    board = Board2048(rng=random.Random(1))
    # 满盘但第一行可合并：向左走一步可行
    board.grid = [[2, 2, 4, 2], [4, 8, 2, 8], [2, 4, 8, 4], [8, 2, 4, 8]]
    assert board._can_move()  # noqa: SLF001 - 活局判定的直接验证
    assert not board.over


def test_move_after_over_is_rejected() -> None:
    board = Board2048(rng=random.Random(1))
    board.grid = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    board.over = True
    outcome = board.apply_move(MoveDirection.LEFT)
    assert not outcome.moved


def test_render_contains_score_and_tiles() -> None:
    board = Board2048(rng=random.Random(3))
    board.new_game()
    text = board.render()
    assert f"score={board.score}" in text
    assert f"moves={board.moves}" in text
    assert str(board.max_tile) in text


def test_to_matrix_is_copy() -> None:
    board = Board2048(rng=random.Random(3))
    board.new_game()
    matrix = board.to_matrix()
    matrix[0][0] = 999
    assert board.grid[0][0] != 999
