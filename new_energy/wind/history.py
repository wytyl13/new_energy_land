#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : history.py

步1：地市风速历史构建（UTC+8 对齐、删闰日、完整小时校验）。

时间轴工具（year_index / no_feb29）已提到 utils.time_axis，风电/光伏共用。
"""
from __future__ import annotations

import pandas as pd

from ..utils.time_axis import beijing_aligned


def build_history(
    city_wind: pd.Series,
    source_years: tuple[int, ...],
) -> pd.Series:
    """把格点风速序列对齐到连续小时时间轴并删闰日。

    Args:
        city_wind (pd.Series): biz.assignment 返回的地市风速序列（UTC 时间索引）。
        source_years (tuple[int, ...]): 历史数据覆盖的年份范围。

    Returns:
        pd.Series: 北京时间索引、插值补齐、剔除 2 月 29 日的风速历史序列。
    """
    return beijing_aligned(city_wind, source_years)
