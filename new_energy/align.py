#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : align.py

数据对齐（风/光共用）：UTC 口径历史序列 → (对齐序列, (年数, 8760) 矩阵, 年标签)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .utils.time_axis import beijing_aligned, year_grid


# ─────────────────────────────────────────────────────────────────────────────
# 数据对齐（风/光共用）
# ─────────────────────────────────────────────────────────────────────────────

class Align:
    """1. 数据对齐：UTC 口径历史序列 → (对齐序列, (年数, 8760) 矩阵, 年标签)。"""

    def __init__(self, source_years: tuple[int, ...]):
        """保存对齐所需年份。

        Args:
            source_years (tuple[int, ...]): 历史数据年份。
        """
        self.source_years = source_years

    def __call__(self, series: pd.Series) -> tuple[pd.Series, np.ndarray, np.ndarray]:
        """历史序列 Series(UTC, 全量年) → (对齐序列, (年数, 8760) 数组, 年份标签)。

        Args:
            series (pd.Series): UTC 口径历史序列（风速/辐照度）。

        Returns:
            tuple[pd.Series, np.ndarray, np.ndarray]: (对齐序列, (年数, 8760) 数组, 年份标签)。
        """
        # 步骤1~4: Series(UTC, 全量年) → Series(北京时, 每年恒 8760 小时)
        #（共用工具：索引 +8h/排序/去重 → 连续小时轴重索引 → 时间插值补缺/截负 → 删 2/29）
        aligned = beijing_aligned(series, self.source_years)

        # 步骤5: 逐年取行组装，不足 8760 小时的年份报错 → ((n, 8760), (n,))
        #（共用工具 year_grid：装箱 + 年份校验，风电/光伏同一路径）
        return aligned, *year_grid(aligned, self.source_years)  # (pd.Series), (n, 8760), (n, )
