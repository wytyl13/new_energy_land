#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : events.py

步3：极端风速事件库（P10/P90、持续≥3h）与分资源年分月事件标准。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..constants import RESOURCE_YEAR_TYPES


def contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """找出布尔序列中连续 True 区段的位置。

    Args:
        mask (np.ndarray): 布尔序列。

    Returns:
        list[tuple[int, int]]: 每段 (起始下标, 结束下标)（左闭右开）。
    """
    padded = np.r_[False, mask.astype(bool), False].astype(int)
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def extract_events(
    history: pd.Series,
    year_class: pd.DataFrame,
    train_years: tuple[int, ...],
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """从训练年份历史中提取低风/大风极端事件及各事件的原始序列。

    Args:
        history (pd.Series): 北京时间索引的风速历史序列。
        year_class (pd.DataFrame): classify_years 的输出（year → 资源年类型）。
        train_years (tuple[int, ...]): 参与事件提取的训练年份。

    Returns:
        tuple[pd.DataFrame, dict[str, np.ndarray]]: (事件表：event_id/year/年景类型/事件类型/月份，
            事件序列字典：event_id → 该事件的风速序列)。
    """
    threshold_low, threshold_high = history.quantile([0.10, 0.90])
    class_map = year_class.set_index("year")["资源年类型"].to_dict()
    rows = []
    segments: dict[str, np.ndarray] = {}
    sequence = 0
    for year in train_years:
        block = history.loc[history.index.year == year]
        values = block.to_numpy(float)
        for event_type, mask in [
            ("低风", values <= threshold_low),
            ("大风", values >= threshold_high),
        ]:
            for start, end in contiguous_runs(mask):
                if end - start < 3:
                    continue
                sequence += 1
                event_id = f"WIND_{event_type}_{sequence:05d}"
                segments[event_id] = values[start:end]
                rows.append(
                    {
                        "event_id": event_id,
                        "year": year,
                        "年景类型": class_map[year],
                        "事件类型": event_type,
                        "月份": int(block.index[start].month),
                    }
                )
    return pd.DataFrame(rows), segments


def build_event_standards(
    events: pd.DataFrame,
    year_class: pd.DataFrame,
    train_years: tuple[int, ...],
) -> pd.DataFrame:
    """统计分资源年、分月份、分事件类型的年均发生次数，作为插入标准。

    Args:
        events (pd.DataFrame): extract_events 输出的事件表。
        year_class (pd.DataFrame): classify_years 的输出（year → 资源年类型）。
        train_years (tuple[int, ...]): 训练年份。

    Returns:
        pd.DataFrame: 每行一个（年景类型, 月份, 事件类型）组合的年均事件次数。
    """
    rows = []
    for case in RESOURCE_YEAR_TYPES:
        years = year_class.loc[
            year_class.year.isin(train_years) & year_class["资源年类型"].eq(case), "year"
        ].astype(int).tolist()
        for month in range(1, 13):
            for event_type in ["低风", "大风"]:
                subset = events.loc[
                    events["年景类型"].eq(case)
                    & events["月份"].eq(month)
                    & events["事件类型"].eq(event_type)
                ]
                rows.append(
                    {
                        "年景类型": case,
                        "月份": month,
                        "事件类型": event_type,
                        "年均事件次数": len(subset) / max(len(years), 1),
                    }
                )
    return pd.DataFrame(rows)
