# -*- coding: utf-8 -*-
"""步3：极端风速事件库（P10/P90、持续≥3h）与分资源年分月事件标准。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .classify import CASE_NAMES


def contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, mask.astype(bool), False].astype(int)
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def extract_events(
    history: pd.Series,
    year_class: pd.DataFrame,
    train_years: tuple[int, ...] = tuple(range(2000, 2025)),
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    threshold_low, threshold_high = history.quantile([0.10, 0.90])
    class_map = year_class.set_index("year")["资源年类型"].to_dict()
    rows = []
    segments: dict[str, np.ndarray] = {}
    sequence = 0
    for year in train_years:
        block = history.loc[history.index.year == year].reset_index(drop=True)
        block_index = history.loc[history.index.year == year].index
        for event_type, mask, threshold in [
            ("低风", block.to_numpy() <= threshold_low, threshold_low),
            ("大风", block.to_numpy() >= threshold_high, threshold_high),
        ]:
            for start, end in contiguous_runs(mask):
                if end - start < 3:
                    continue
                sequence += 1
                event_id = f"WIND_{event_type}_{sequence:05d}"
                segment = block.iloc[start:end].to_numpy(float)
                segments[event_id] = segment
                rows.append(
                    {
                        "event_id": event_id,
                        "year": year,
                        "年景类型": class_map[year],
                        "事件类型": event_type,
                        "月份": int(block_index[start].month),
                        "开始时间": block_index[start],
                        "结束时间": block_index[end - 1],
                        "持续小时": int(end - start),
                        "阈值_m_s": float(threshold),
                        "事件平均风速_m_s": float(segment.mean()),
                    }
                )
    return pd.DataFrame(rows), segments


def build_event_standards(
    events: pd.DataFrame,
    year_class: pd.DataFrame,
    train_years: tuple[int, ...] = tuple(range(2000, 2025)),
) -> pd.DataFrame:
    rows = []
    for case in CASE_NAMES:
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
                        "同类历史年数": len(years),
                        "同类历史事件次数": len(subset),
                        "年均事件次数": len(subset) / max(len(years), 1),
                        "年均事件持续小时": float(subset["持续小时"].sum() / max(len(years), 1)),
                        "同类历史事件平均持续小时": float(subset["持续小时"].mean()) if len(subset) else 0.0,
                    }
                )
    return pd.DataFrame(rows)
