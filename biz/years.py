#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : years.py

历史年份方案与数据完整性校验（原 reader.py 拆出）：期望窗口 ∩ 各数据类型
实际存在的年份 → 最大连续段 → 逐年行数校验 → YearsPlan。
"""
from __future__ import annotations

import calendar
import logging
import re
from pathlib import Path
from typing import NamedTuple

from .constants import DEFAULT_YEARS, MIN_HISTORY_YEARS
from .grids import find_grid_file

logger = logging.getLogger(__name__)


class YearsPlan(NamedTuple):
    """历史年份方案。

    source_years 实际使用的历史年份（两端含）；train_years = 除最后一年
    （训练统计用）；axis_year = 最后一年（不参与统计，只借 8760 小时日历
    模板，之后重排到请求的输出年份）。
    """

    source_years: tuple[int, ...]
    train_years: tuple[int, ...]
    axis_year: int


def _file_years(grid_dir: Path, kind: str) -> set[int]:
    """扫 kind 子目录（*_{kind}_{year}.csv，区域前缀任意），返回实际存在的年份集合。"""
    pattern = re.compile(rf".+_{kind}_(\d{{4}})\.csv")
    return {
        int(match.group(1))
        for path in (Path(grid_dir) / kind).glob(f"*_{kind}_*.csv")
        if (match := pattern.fullmatch(path.name))
    }


def _row_count(path: Path) -> int:
    """数 CSV 数据行数（减去表头行）。"""
    with path.open("rb") as handle:
        return sum(1 for _ in handle) - 1


def resolve_years(
    grid_dir: Path,
    kinds: tuple[str, ...],
    window: tuple[int, int] = DEFAULT_YEARS,
) -> YearsPlan:
    """期望窗口 ∩ 各 kind 实际年份 → 历史年份方案。

    规则：
        1. 多个 kind 取交集（最小可用范围），窗口外年份不纳入；
        2. 取最大连续年份段，段外年份丢弃并告警（防止跨年缺口被插值）；
        3. 段内每个文件校验行数（闰年 8784 / 平年 8760 小时），不符报错；
        4. 年份数低于 MIN_HISTORY_YEARS 报错。
    """
    start, end = int(window[0]), int(window[1])
    if start >= end:
        raise ValueError(f"历史年份窗口配置错误: 起始 {start} ≥ 结束 {end}")
    per_kind = {kind: _file_years(grid_dir, kind) for kind in kinds}
    in_window = sorted(year for year in set.intersection(*per_kind.values()) if start <= year <= end)
    if not in_window:
        detail = {kind: sorted(years) for kind, years in per_kind.items()}
        raise ValueError(f"数据目录在期望窗口 {start}-{end} 内没有任何年份: {grid_dir}，各类型实际年份 {detail}")

    runs: list[list[int]] = []
    for year in in_window:
        if runs and year == runs[-1][-1] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    best = max(runs, key=len)
    dropped = sorted(set(in_window) - set(best))
    if dropped:
        logger.warning("历史年份不连续，丢弃 %s，使用 %d-%d", dropped, best[0], best[-1])

    for year in best:
        expected = 8784 if calendar.isleap(year) else 8760
        for kind in kinds:
            path = find_grid_file(Path(grid_dir) / kind, kind, year)
            if (rows := _row_count(path)) != expected:
                raise ValueError(f"{path} 数据行数 {rows} ≠ {year} 年应有的 {expected} 行，数据不完整")

    if len(best) < MIN_HISTORY_YEARS:
        raise ValueError(
            f"历史年份数不足: 实际 {len(best)} 年（{best[0]}-{best[-1]}），下限 {MIN_HISTORY_YEARS} 年"
        )
    missing = sorted(set(range(start, end + 1)) - set(best))
    if missing:
        logger.warning("期望窗口 %d-%d 缺 %s，实际使用 %d-%d", start, end, missing, best[0], best[-1])
    return YearsPlan(tuple(best), tuple(best[:-1]), best[-1])


if __name__ == "__main__":
    """冒烟自检：默认数据目录的风/光年份方案。运行: python -m biz.years"""
    data = Path(__file__).resolve().parents[1] / "data" / "hainan_era5_grid_csv"
    for kinds in (("wind",), ("solar", "temperature")):
        plan = resolve_years(data, kinds)
        print(f"{'+'.join(kinds)}: source={plan.source_years[0]}-{plan.source_years[-1]}"
              f" 共{len(plan.source_years)}年 train={len(plan.train_years)}年 axis={plan.axis_year}")
