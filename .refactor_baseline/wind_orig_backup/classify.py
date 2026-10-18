# -*- coding: utf-8 -*-
"""步2：资源年分类（mean(v³) 的 P25/P75 → 偏小/正常/偏大资源年）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .history import no_feb29

CASE_NAMES = ["偏小资源年", "正常资源年", "偏大资源年"]


def classify_years(
    history: pd.Series,
    train_years: tuple[int, ...] = tuple(range(2000, 2025)),
) -> pd.DataFrame:
    rows = []
    for year in sorted(set(history.index.year)):
        block = history.loc[history.index.year == year]
        block = block.loc[no_feb29(block.index)]
        rows.append(
            {
                "year": year,
                "年平均风速_m_s": float(block.mean()),
                "年平均风速立方_m3_s3": float(np.mean(np.power(block.to_numpy(float), 3))),
                "P90风速_m_s": float(block.quantile(0.90)),
            }
        )
    result = pd.DataFrame(rows)
    training = result[result.year.isin(train_years)]
    p25, p75 = training["年平均风速立方_m3_s3"].quantile([0.25, 0.75])
    result["P25阈值_mean_v3"] = float(p25)
    result["P75阈值_mean_v3"] = float(p75)
    result["资源年类型"] = np.where(
        result["年平均风速立方_m3_s3"] <= p25,
        "偏小资源年",
        np.where(result["年平均风速立方_m3_s3"] >= p75, "偏大资源年", "正常资源年"),
    )
    result["是否训练年"] = result.year.isin(train_years)
    return result
