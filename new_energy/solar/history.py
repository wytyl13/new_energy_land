#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : history.py

步1：地市辐照度历史构建（UTC+8 对齐、删闰日）。

时间轴工具（year_index / no_feb29 / beijing_aligned）在 utils.time_axis，
格点读取在业务层 biz.assignment，风电/光伏共用；地市格点中心 city_centers
亦在业务层 biz.assignment。
"""
from __future__ import annotations

import pandas as pd

from ..utils.time_axis import beijing_aligned


def build_history(
    city_solar: pd.Series,
    source_years: tuple[int, ...],
) -> pd.Series:
    """把格点辐照度序列对齐到连续小时时间轴并删闰日。

    Args:
        city_solar (pd.Series): biz.assignment 返回的地市辐照度序列（UTC 时间索引）。
        source_years (tuple[int, ...]): 历史数据覆盖的年份范围。

    Returns:
        pd.Series: 北京时间索引、插值补齐、剔除 2 月 29 日的辐照度历史序列。
    """
    return beijing_aligned(city_solar, source_years)
