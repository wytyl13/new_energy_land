# -*- coding: utf-8 -*-
"""步1：地市风速历史构建（UTC+8 对齐、删闰日、逐年 8760 小时校验）。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class History:
    series: pd.Series          # 2000-2025 连续序列，北京时间，名称=地市键
    quality: pd.DataFrame      # 逐年数据质量


def year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")


def no_feb29(index: pd.DatetimeIndex) -> np.ndarray:
    return ~((index.month == 2) & (index.day == 29))


def build_history(
    city_wind: pd.Series,
    source_years: tuple[int, ...] = tuple(range(2000, 2026)),
) -> History:
    """输入 common.city_weather 返回的地市风速 Series（UTC 索引），输出对齐后的历史序列。"""
    # UTC → 北京时间
    source = pd.Series(city_wind.to_numpy(float), index=city_wind.index + pd.Timedelta(hours=8), name=city_wind.name)
    source = source.sort_index()
    source = source[~source.index.duplicated(keep="last")]

    start, end = min(source_years), max(source_years)
    complete_index = pd.date_range(f"{start}-01-01", f"{end}-12-31 23:00:00", freq="h")
    aligned = source.reindex(complete_index)
    missing = int(aligned.isna().sum())
    aligned = aligned.interpolate(method="time", limit_direction="both").clip(lower=0.0)

    blocks, quality = [], []
    for year in source_years:
        full = aligned.reindex(year_index(year))
        block = full.loc[no_feb29(full.index)]
        if len(block) != 8760 or block.isna().any():
            raise ValueError(f"{year} 年 {city_wind.name} 序列不是完整 8760 小时")
        blocks.append(block)
        quality.append(
            {
                "年份": year,
                "时间口径": "ERA5 valid_time UTC + 8 h，北京时间",
                "小时数": len(block),
                "跨年连续对齐后缺失小时总数": missing,
                "平均风速_m_s": float(block.mean()),
            }
        )
    return History(series=pd.concat(blocks).sort_index(), quality=pd.DataFrame(quality))
