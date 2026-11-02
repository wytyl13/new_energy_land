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

# 非闰年逐月起点（小时）：31/28/31/30/31/30/31/31/30/31/30/31 天（风/光共用）
MONTH_STARTS = np.array([0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016, 8760])


# ─────────────────────────────────────────────────────────────────────────────
# 时间轴查表（风/光共用）
# ─────────────────────────────────────────────────────────────────────────────

def month_of(position: np.ndarray | int) -> np.ndarray | int:
    """位置 → 月份（查 MONTH_STARTS 表，0-based 返回 0~11，调用处 +1 得 1~12）。

    Args:
        position (np.ndarray | int): 年内小时位置（0~8759）。

    Returns:
        np.ndarray | int: 对应月份序号（0~11）。
    """
    return np.searchsorted(MONTH_STARTS, position, side="right") - 1


def year_grid(aligned: pd.Series, source_years: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    """对齐序列逐年取行装箱 → ((年数, 8760), 年份数组)，年份不足 8760 小时报错。

    Args:
        aligned (pd.Series): 北京时间索引的对齐序列（每年恒 8760 小时）。
        source_years (tuple[int, ...]): 装箱年份（升序，行序与此一致）。

    Returns:
        tuple[np.ndarray, np.ndarray]: ((年数, 8760) 数组, 年份数组 (年数, ))。
    """
    rows = [aligned.loc[aligned.index.year == year].to_numpy(float) for year in source_years]
    bad = [year for year, row in zip(source_years, rows) if len(row) != 8760]
    if bad:
        raise ValueError(f"{aligned.name} 年份 {bad} 对齐后不足 8760 小时（数据缺失或时间轴不完整）")
    return np.vstack(rows), np.array(source_years, dtype=np.int64)


def year_index(year: int, drop_feb29: bool = False) -> pd.DatetimeIndex:
    """生成某一年的整点小时时间轴（默认含 2 月 29 日，闰年 8784 小时）。

    Args:
        year (int): 年份。
        drop_feb29 (bool): True 时剔除 2 月 29 日（恒 8760 小时）。

    Returns:
        pd.DatetimeIndex: 该年 1 月 1 日 00:00 到 12 月 31 日 23:00 的逐小时索引。
    """
    index = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")
    return index[no_feb29(index)] if drop_feb29 else index


def no_feb29(index: pd.DatetimeIndex) -> np.ndarray:
    """生成剔除 2 月 29 日的布尔掩码。

    Args:
        index (pd.DatetimeIndex): 待过滤的时间轴。

    Returns:
        np.ndarray: 布尔数组，True 表示该时刻保留（非闰日 2 月 29 日）。
    """
    return ~((index.month == 2) & (index.day == 29))


def beijing_aligned(series: pd.Series, source_years: tuple[int, ...], drop_feb29: bool = True) -> pd.Series:
    """把 UTC 口径的地市序列对齐到连续小时北京时间轴（风电/光伏共用）。

    Args:
        series (pd.Series): UTC 时间索引的地市序列（biz.assignment 返回）。
        source_years (tuple[int, ...]): 历史数据覆盖的年份范围。
        drop_feb29 (bool): True（默认）剔除 2 月 29 日（每年恒 8760 小时）；
            False 保留闰日（闰年 8784 小时）。

    Returns:
        pd.Series: 北京时间索引、插值补齐的历史序列。
    """
    # 步骤1: Series(UTC, 全量年) → Series(UTC + 8)（索引 +8h、排序、去重复索引）
    source = pd.Series(
        series.to_numpy(float), index=series.index + pd.Timedelta(hours=8), name=series.name
    ).sort_index()
    source = source[~source.index.duplicated(keep="last")]

    # 步骤2: 重索引到 min~max 年连续小时轴
    start, end = min(source_years), max(source_years)
    complete_index = pd.date_range(f"{start}-01-01", f"{end}-12-31 23:00:00", freq="h") # (n*8760+闰年数*24, )
    aligned = source.reindex(complete_index)
    if aligned.isna().all():
        raise ValueError(f"{series.name} 在 {start}-{end} 年无任何数据")

    # 步骤3: 按时间插值补缺（双向）→ 负值截 0
    aligned = aligned.interpolate(method="time", limit_direction="both").clip(lower=0.0)

    # 步骤4: 删 2 月 29 日 → Series(北京时, 每年恒 8760 小时)
    return aligned.loc[no_feb29(aligned.index)] if drop_feb29 else aligned


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
