#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : events.py

步3：低辐照事件库与标准。

核心日照时段（理想辐照度超过阈值）内识别晴空指数 Kt ≤ P10 且持续 ≥3 小时的
低辐照事件；统计分资源年、分月份的年均发生次数，作为场景生成的插入标准。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..constants import RESOURCE_YEAR_TYPES

EVENT_IDEAL_THRESHOLD = 200.0  # 核心日照时段阈值：仅理想辐照度超过该值时识别事件（W/m2）
KT_QUANTILE = 0.10            # 晴空指数分位阈值
KT_UPPER = 1.2                # 晴空指数上限（数据噪声截断）
MIN_DURATION = 3              # 事件最短持续小时数


def contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """把布尔掩码切分为连续 True 区段。

    Args:
        mask (np.ndarray): 布尔数组。

    Returns:
        list[tuple[int, int]]: 每个区段的 (起始下标, 结束下标不含)。
    """
    padded = np.r_[False, mask.astype(bool), False].astype(int)
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def extract_low_irradiance_events(
    history: pd.Series,
    ideal: pd.Series,
    classes: pd.DataFrame,
    train_years: tuple[int, ...],
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """从训练年历史中提取低辐照事件库。

    Args:
        history (pd.Series): 北京时间索引的辐照度历史序列（训练年）。
        ideal (pd.Series): 同时间轴的理想辐照度序列。
        classes (pd.DataFrame): classify_years 的逐年分类结果。
        train_years (tuple[int, ...]): 训练年份范围。

    Returns:
        tuple[pd.DataFrame, dict[str, np.ndarray]]: (事件表, 事件编号 → 辐照损失段)。
    """
    kt = (history / ideal.replace(0.0, np.nan)).clip(0.0, KT_UPPER)
    core = ideal > EVENT_IDEAL_THRESHOLD
    threshold = float(kt.loc[core].dropna().quantile(KT_QUANTILE))
    class_map = classes.set_index("year")["资源年类型"].to_dict()
    records = []
    segments: dict[str, np.ndarray] = {}
    sequence = 0
    for year in train_years:
        idx = history.index[history.index.year == year]
        year_mask = (kt.loc[idx] <= threshold) & core.loc[idx]
        for start, end in contiguous_runs(year_mask.to_numpy()):
            if end - start < MIN_DURATION:
                continue
            sequence += 1
            event_id = f"LOW_KT_{sequence:05d}"
            start_time, end_time = idx[start], idx[end - 1]
            segments[event_id] = (ideal.loc[start_time:end_time] - history.loc[start_time:end_time]).clip(lower=0.0).to_numpy(float)
            records.append(
                {
                    "event_id": event_id,
                    "year": year,
                    "年景类型": class_map[year],
                    "月份": int(start_time.month),
                    "开始时间": start_time,
                    "结束时间": end_time,
                    "持续小时": int(end - start),
                    "晴空指数P10阈值": threshold,
                    "事件平均晴空指数": float(kt.loc[start_time:end_time].mean()),
                }
            )
    columns = ["event_id", "year", "年景类型", "月份", "开始时间", "结束时间", "持续小时", "晴空指数P10阈值", "事件平均晴空指数"]
    return pd.DataFrame(records, columns=columns), segments


def build_event_standards(
    events: pd.DataFrame,
    classes: pd.DataFrame,
    train_years: tuple[int, ...],
) -> pd.DataFrame:
    """统计分资源年、分月份的低辐照事件年均次数与持续小时标准。

    Args:
        events (pd.DataFrame): extract_low_irradiance_events 的事件表。
        classes (pd.DataFrame): classify_years 的逐年分类结果。
        train_years (tuple[int, ...]): 训练年份范围。

    Returns:
        pd.DataFrame: 年景类型 × 12 个月一行，含年均事件次数与年均持续小时。
    """
    rows = []
    for case in RESOURCE_YEAR_TYPES:
        years = classes.loc[classes.year.isin(train_years) & classes["资源年类型"].eq(case), "year"].astype(int).tolist()
        for month in range(1, 13):
            block = events.loc[events["年景类型"].eq(case) & events["月份"].eq(month)]
            rows.append(
                {
                    "年景类型": case,
                    "月份": month,
                    "同类历史年数": len(years),
                    "历史事件次数": len(block),
                    "年均事件次数": len(block) / max(len(years), 1),
                    "年均事件持续小时": float(block["持续小时"].sum() / max(len(years), 1)),
                }
            )
    return pd.DataFrame(rows)
