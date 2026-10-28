#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : events.py

步3：极端事件库与分资源年分月事件标准。

风：风速 P10/P90 双侧阈值、持续 ≥3h 的低风/大风事件。
光：核心日照时段内晴空指数 Kt ≤ P10、持续 ≥3h 的低辐照事件。
contiguous_runs 与统计框架共用；提取算法风/光不同，各写各的阶段类。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SolarConfig
from .constants import RESOURCE_YEAR_TYPES


@dataclass
class EventLibrary:
    """事件库三件套：事件表、年均次数标准表、事件序列字典。"""

    events: pd.DataFrame                        # 事件表（event_id/year/年景类型/月份/...）
    standards: pd.DataFrame                     # 分年景分月（分事件类型）年均次数标准
    segments: dict[str, np.ndarray]             # event_id → 事件原始序列


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


class WindEventStage:
    """从训练年份风速历史中提取低风/大风事件库与标准。"""

    def __init__(self, train_years: tuple[int, ...]):
        """记录参与事件提取的训练年份。

        Args:
            train_years (tuple[int, ...]): 训练年份。
        """
        self.train_years = train_years

    def __call__(self, history: pd.Series, year_class: pd.DataFrame) -> EventLibrary:
        """提取事件并统计标准。

        Args:
            history (pd.Series): 北京时间索引的风速历史序列（训练年）。
            year_class (pd.DataFrame): 资源年分类结果（year → 资源年类型）。

        Returns:
            EventLibrary: 事件库三件套。
        """
        events, segments = self._extract(history, year_class)
        standards = self._standards(events, year_class)
        return EventLibrary(events=events, standards=standards, segments=segments)

    def _extract(self, history: pd.Series, year_class: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
        """按 P10/P90 阈值提取持续 ≥3h 的低风/大风事件及各事件的原始序列。

        Args:
            history (pd.Series): 北京时间索引的风速历史序列。
            year_class (pd.DataFrame): classify 的输出（year → 资源年类型）。

        Returns:
            tuple[pd.DataFrame, dict[str, np.ndarray]]: (事件表：event_id/year/年景类型/事件类型/月份，
                事件序列字典：event_id → 该事件的风速序列)。
        """
        threshold_low, threshold_high = history.quantile([0.10, 0.90])
        class_map = year_class.set_index("year")["资源年类型"].to_dict()
        rows = []
        segments: dict[str, np.ndarray] = {}
        sequence = 0
        for year in self.train_years:
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

    def _standards(self, events: pd.DataFrame, year_class: pd.DataFrame) -> pd.DataFrame:
        """统计分资源年、分月份、分事件类型的年均发生次数，作为插入标准。

        Args:
            events (pd.DataFrame): 事件表。
            year_class (pd.DataFrame): 资源年分类结果。

        Returns:
            pd.DataFrame: 每行一个（年景类型, 月份, 事件类型）组合的年均事件次数。
        """
        rows = []
        for case in RESOURCE_YEAR_TYPES:
            years = year_class.loc[
                year_class.year.isin(self.train_years) & year_class["资源年类型"].eq(case), "year"
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


class SolarEventStage:
    """从训练年辐照度历史中提取低辐照事件库与标准。"""

    def __init__(self, config: SolarConfig):
        """记录配置（训练年份与事件识别阈值）。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config
        self.train_years = config.train_years

    def __call__(self, history: pd.Series, ideal: pd.Series, classes: pd.DataFrame) -> EventLibrary:
        """提取事件并统计标准。

        Args:
            history (pd.Series): 北京时间索引的辐照度历史序列（训练年）。
            ideal (pd.Series): 同时间轴的理想辐照度序列。
            classes (pd.DataFrame): 资源年分类结果。

        Returns:
            EventLibrary: 事件库三件套。
        """
        events, segments = self._extract(history, ideal, classes)
        standards = self._standards(events, classes)
        return EventLibrary(events=events, standards=standards, segments=segments)

    def _extract(self, history: pd.Series, ideal: pd.Series, classes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
        """核心日照时段内识别 Kt ≤ P10 且持续 ≥3 小时的低辐照事件。

        Args:
            history (pd.Series): 北京时间索引的辐照度历史序列（训练年）。
            ideal (pd.Series): 同时间轴的理想辐照度序列。
            classes (pd.DataFrame): 资源年分类结果。

        Returns:
            tuple[pd.DataFrame, dict[str, np.ndarray]]: (事件表, 事件编号 → 辐照损失段)。
        """
        kt = (history / ideal.replace(0.0, np.nan)).clip(0.0, self.config.kt_upper)
        core = ideal > self.config.event_ideal_threshold
        threshold = float(kt.loc[core].dropna().quantile(self.config.kt_quantile))
        class_map = classes.set_index("year")["资源年类型"].to_dict()
        records = []
        segments: dict[str, np.ndarray] = {}
        sequence = 0
        for year in self.train_years:
            idx = history.index[history.index.year == year]
            year_mask = (kt.loc[idx] <= threshold) & core.loc[idx]
            for start, end in contiguous_runs(year_mask.to_numpy()):
                if end - start < self.config.min_duration:
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

    def _standards(self, events: pd.DataFrame, classes: pd.DataFrame) -> pd.DataFrame:
        """统计分资源年、分月份的低辐照事件年均次数与持续小时标准。

        Args:
            events (pd.DataFrame): 事件表。
            classes (pd.DataFrame): 资源年分类结果。

        Returns:
            pd.DataFrame: 年景类型 × 12 个月一行，含年均事件次数与年均持续小时。
        """
        rows = []
        for case in RESOURCE_YEAR_TYPES:
            years = classes.loc[classes.year.isin(self.train_years) & classes["资源年类型"].eq(case), "year"].astype(int).tolist()
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
