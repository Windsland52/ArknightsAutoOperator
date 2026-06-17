"""棋盘记号（如 D2）↔ 格子坐标 (row, col)。

prts-plus 约定：Pos 列 = 字母（A=0, B=1, ...），行 = 数字（从下往上）。
row=0 是最下面一行，row=height-1 是最上面一行。
col=0 是最左边一列。

例如 7 行 10 列的地图中：
  A1 → (row=6, col=0)    G10 → (row=0, col=9)
  D2 → (row=5, col=3)
"""

from __future__ import annotations


def convert_position(pos: str, map_height: int, map_width: int) -> tuple[int, int]:
    """棋盘记号 → (col, row)。

    prts-plus 格式：字母+数字，如 "D2" → col=3, row=5（7行地图）。
    """
    if not pos:
        raise ValueError("位置不能为空")

    col_str = ""
    row_str = ""
    for ch in pos:
        if ch.isalpha():
            col_str += ch.upper()
        elif ch.isdigit():
            row_str += ch

    if not col_str or not row_str:
        raise ValueError(f"无效位置: {pos}")

    col = 0
    for ch in col_str:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    col -= 1  # A=0, B=1, ...

    row_num = int(row_str)
    row = map_height - row_num  # row=0 是最下面

    if not (0 <= col < map_width and 0 <= row < map_height):
        raise ValueError(f"位置 {pos} 超出地图范围 ({map_width}x{map_height})")

    return col, row


def tile_position_to_str(col: int, row: int, map_height: int) -> str:
    """(col, row) → 棋盘记号。"""
    row_num = map_height - row

    col_str = ""
    c = col + 1
    while c > 0:
        c -= 1
        col_str = chr(ord("A") + c % 26) + col_str
        c //= 26

    return f"{col_str}{row_num}"
