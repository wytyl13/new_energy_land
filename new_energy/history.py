#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : history.py

步1：地市历史序列构建（UTC+8 对齐、删闰日、完整小时校验），风/光共用。

时间轴工具（year_index / no_feb29 / beijing_aligned）在 utils.time_axis，
格点读取在业务层 biz.assignment。
"""
from __future__ import annotations

import pandas as pd

from .utils.time_axis import beijing_aligned


class HistoryBuilder:
    """把格点气象序列对齐到连续小时时间轴并删闰日（风/光共用）。"""

    def __init__(self, source_years: tuple[int, ...]):
        """记录历史数据覆盖的年份范围。

        Args:
            source_years (tuple[int, ...]): 历史数据覆盖的年份范围。
        """
        self.source_years = source_years

    def __call__(self, series: pd.Series) -> pd.Series:
        """构建北京时间索引的历史序列。

        Args:
            series (pd.Series): biz.assignment 返回的地市序列（UTC 时间索引）。

        Returns:
            pd.Series: 北京时间索引、插值补齐、剔除 2 月 29 日的历史序列。
        """
        return beijing_aligned(series, self.source_years)
