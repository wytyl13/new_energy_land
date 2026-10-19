#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : classify.py

步2：资源年分类（年累计辐照量 P25/P75 → 偏小/正常/偏大资源年）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.time_axis import no_feb29


def classify_years(
    history: pd.Series,
    train_years: tuple[int, ...],
) -> pd.DataFrame:
    """按年累计水平面辐照量把每一年分为偏小/正常/偏大资源年。

    Args:
        history (pd.Series): 北京时间索引的辐照度历史序列。
        train_years (tuple[int, ...]): 参与分位数统计的训练年份。

    Returns:
        pd.DataFrame: 每年一行，含 year、年累计辐照量_kWh_m2、资源年类型三列。
    """
    rows = []
    for year in sorted(set(history.index.year)):
        block = history.loc[history.index.year == year]
        block = block.loc[no_feb29(block.index)]
        rows.append({"year": year, "年累计辐照量_kWh_m2": float(block.sum() / 1000.0)})
    result = pd.DataFrame(rows)
    p25, p75 = result.loc[result.year.isin(train_years), "年累计辐照量_kWh_m2"].quantile([0.25, 0.75])
    result["资源年类型"] = np.where(
        result["年累计辐照量_kWh_m2"] <= p25,
        "偏小资源年",
        np.where(result["年累计辐照量_kWh_m2"] >= p75, "偏大资源年", "正常资源年"),
    )
    return result
