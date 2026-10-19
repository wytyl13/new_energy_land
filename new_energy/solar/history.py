#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : history.py

步1：地市辐照度历史构建（UTC+8 对齐、删闰日）与地市代表性经纬度。

时间轴工具（year_index / no_feb29 / beijing_aligned）在 common.time_axis，
格点读取在 common.city_weather，风电/光伏共用。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..common.request import ENC
from ..common.time_axis import beijing_aligned


def build_history(
    city_solar: pd.Series,
    source_years: tuple[int, ...],
) -> pd.Series:
    """把格点辐照度序列对齐到连续小时时间轴并删闰日。

    Args:
        city_solar (pd.Series): common.city_weather 返回的地市辐照度序列（UTC 时间索引）。
        source_years (tuple[int, ...]): 历史数据覆盖的年份范围。

    Returns:
        pd.Series: 北京时间索引、插值补齐、剔除 2 月 29 日的辐照度历史序列。
    """
    return beijing_aligned(city_solar, source_years)


def city_centers(assignment_csv: Path) -> dict[str, tuple[float, float]]:
    """按严格边界内格点的平均经纬度给出各地市代表性坐标。

    Args:
        assignment_csv (Path): 格点归属表 CSV 路径。

    Returns:
        dict[str, tuple[float, float]]: 地市键 → (纬度, 经度)。
    """
    table = pd.read_csv(assignment_csv, encoding=ENC)
    inside = table.loc[table["assignment_method"].eq("inside")]
    return {
        f"{int(code)}_{city}": (float(block["latitude"].mean()), float(block["longitude"].mean()))
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True)
    }
