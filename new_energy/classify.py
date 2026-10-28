#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : classify.py

步2：资源年分类（年际指标 P25/P75 → 偏小/正常/偏大资源年），风/光共用分位框架。

风/光只差年际指标一行：风 = 年平均风速立方，光 = 年累计辐照量，经构造注入。
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from .utils.time_axis import no_feb29


class ResourceYearClassifier:
    """按年际指标把每一年分为偏小/正常/偏大资源年（分位框架风/光共用）。"""

    def __init__(self, metric: Callable[[pd.Series], float], value_name: str, train_years: tuple[int, ...]):
        """注入年际指标与统计年份。

        Args:
            metric (Callable): 年际指标函数（该年 Series → 标量，表达式与原实现逐字一致）。
            value_name (str): 指标列名（如 年平均风速立方_m3_s3）。
            train_years (tuple[int, ...]): 参与分位数统计的训练年份。
        """
        self.metric = metric
        self.value_name = value_name
        self.train_years = train_years

    def __call__(self, history: pd.Series) -> pd.DataFrame:
        """逐年计算指标并按 P25/P75 分三类。

        Args:
            history (pd.Series): 北京时间索引的历史序列（全量年份，含最后一年）。

        Returns:
            pd.DataFrame: 每年一行，含 year、指标列、资源年类型三列。
        """
        rows = []
        for year in sorted(set(history.index.year)):
            block = history.loc[history.index.year == year]
            block = block.loc[no_feb29(block.index)]
            rows.append({"year": year, self.value_name: self.metric(block)})
        result = pd.DataFrame(rows)
        p25, p75 = result.loc[result.year.isin(self.train_years), self.value_name].quantile([0.25, 0.75])
        values = result[self.value_name]
        result["资源年类型"] = np.where(
            values <= p25,
            "偏小资源年",
            np.where(values >= p75, "偏大资源年", "正常资源年"),
        )
        return result
