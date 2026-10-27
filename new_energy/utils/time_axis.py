#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : time_axis.py

时间轴工具：逐年小时索引与闰日处理，风电/光伏流程共用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def year_index(year: int) -> pd.DatetimeIndex:
    """生成某一年的整点小时时间轴（含 2 月 29 日，闰年 8784 小时）。

    Args:
        year (int): 年份。

    Returns:
        pd.DatetimeIndex: 该年 1 月 1 日 00:00 到 12 月 31 日 23:00 的逐小时索引。
    """
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")


def no_feb29(index: pd.DatetimeIndex) -> np.ndarray:
    """生成剔除 2 月 29 日的布尔掩码。

    Args:
        index (pd.DatetimeIndex): 待过滤的时间轴。

    Returns:
        np.ndarray: 布尔数组，True 表示该时刻保留（非闰日 2 月 29 日）。
    """
    return ~((index.month == 2) & (index.day == 29))


def beijing_aligned(series: pd.Series, source_years: tuple[int, ...]) -> pd.Series:
    """把 UTC 口径的地市序列对齐到连续小时北京时间轴并删闰日（风电/光伏共用）。

    Args:
        series (pd.Series): UTC 时间索引的地市序列（biz.assignment 返回）。
        source_years (tuple[int, ...]): 历史数据覆盖的年份范围。

    Returns:
        pd.Series: 北京时间索引、插值补齐、剔除 2 月 29 日的历史序列。
    """
    # UTC → 北京时间
    source = pd.Series(series.to_numpy(float), index=series.index + pd.Timedelta(hours=8), name=series.name)
    source = source.sort_index()
    source = source[~source.index.duplicated(keep="last")]

    start, end = min(source_years), max(source_years)
    complete_index = pd.date_range(f"{start}-01-01", f"{end}-12-31 23:00:00", freq="h")
    aligned = source.reindex(complete_index)
    if aligned.isna().all():
        raise ValueError(f"{series.name} 在 {start}-{end} 年无任何数据")
    aligned = aligned.interpolate(method="time", limit_direction="both").clip(lower=0.0)
    return aligned.loc[no_feb29(aligned.index)]


def remap_to_year(series: pd.Series, year: int) -> pd.Series:
    """把场景轴年份时间轴的场景重排到输出年份（闰年补 2 月 29 日，时间插值）。

    Args:
        series (pd.Series): 场景轴年份（8760 小时）时间轴的场景序列。
        year (int): 输出年份。

    Returns:
        pd.Series: 对齐输出年份完整时间轴的场景序列。
    """
    target = year_index(year)
    nonleap = target[no_feb29(target)]
    return pd.Series(series.to_numpy(float), index=nonleap, name=series.name).reindex(target).interpolate(
        method="time", limit_direction="both"
    )
