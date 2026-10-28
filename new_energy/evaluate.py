#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : evaluate.py

可选评估：三类风速场景与历史同类年的量化指标（内存返回，不写文件）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .constants import RESOURCE_YEAR_TYPES
from .utils.time_axis import no_feb29

AUTOCORR_LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]


def historical_reference(history: pd.Series, years: list[int]) -> dict[str, object]:
    """汇总同类历史年的参考指标（均值、立方均值、持续曲线、自相关、爬坡、日/月均值）。

    Args:
        history (pd.Series): 北京时间索引的风速历史序列。
        years (list[int]): 同类资源年对应的年份列表。

    Returns:
        dict[str, object]: 参考指标字典（blocks/mean/mean_v3/duration/autocorr/ramp1/ramp3/daily/monthly/mean_curve）。
    """
    blocks = []
    for year in years:
        block = history.loc[history.index.year == year].copy()
        block = block.loc[no_feb29(block.index)].iloc[:8760]
        if len(block) == 8760:
            blocks.append(block.reset_index(drop=True))
    if not blocks:
        raise ValueError("同类历史年为空，无法评估")
    matrix = np.vstack([block.to_numpy(float) for block in blocks])
    ac = np.vstack([[block.autocorr(lag=lag) for lag in AUTOCORR_LAGS] for block in blocks])
    ramp1 = np.array([[block.diff(1).abs().dropna().quantile(q) for q in (0.95, 0.99)] for block in blocks])
    ramp3 = np.array([[block.diff(3).abs().dropna().quantile(q) for q in (0.95, 0.99)] for block in blocks])
    daily = matrix.reshape(len(blocks), 365, 24).mean(axis=2)
    monthly_rows = []
    for year in years:
        block = history.loc[history.index.year == year].copy()
        block = block.loc[no_feb29(block.index)]
        monthly_rows.append(block.groupby(block.index.month).mean().reindex(range(1, 13)).to_numpy(float))
    return {
        "blocks": matrix,
        "mean": float(matrix.mean()),
        "mean_v3": float(np.mean(matrix**3)),
        "duration": np.mean(np.sort(matrix, axis=1)[:, ::-1], axis=0),
        "autocorr": np.nanmean(ac, axis=0),
        "ramp1": np.nanmean(ramp1, axis=0),
        "ramp3": np.nanmean(ramp3, axis=0),
        "daily": np.nanmean(daily, axis=0),
        "monthly": np.nanmean(np.vstack(monthly_rows), axis=0),
        "mean_curve": np.nanmean(matrix, axis=0),
    }


def metric_row(city: str, case: str, gen: pd.Series, reference: dict, years: list[int], plan: pd.DataFrame) -> dict:
    """计算一个地市、一类资源年的场景与历史参考的对比指标行。

    Args:
        city (str): 地市键。
        case (str): 资源年类型名称。
        gen (pd.Series): 生成的场景序列。
        reference (dict): historical_reference 输出的参考指标。
        years (list[int]): 同类历史年份列表。
        plan (pd.DataFrame): 该资源年的事件插入方案表。

    Returns:
        dict: 单行指标（平均风速/立方均值误差、持续曲线 nRMSE、自相关、爬坡、事件统计）。
    """
    gen_values = gen.to_numpy(float)
    hist_mean = float(reference["mean"])
    hist_v3 = float(reference["mean_v3"])
    hist_ac = np.asarray(reference["autocorr"])
    gen_ac = np.array([gen.autocorr(lag=lag) for lag in AUTOCORR_LAGS], dtype=float)
    hist_dur = np.asarray(reference["duration"])
    gen_dur = np.sort(gen_values)[::-1]
    ramp_metrics = {}
    for hours, key in [(1, "1h"), (3, "3h")]:
        hist_q = np.asarray(reference["ramp1" if hours == 1 else "ramp3"])
        ramp = gen.diff(hours).abs().dropna()
        gen_q = np.asarray([ramp.quantile(0.95), ramp.quantile(0.99)])
        ramp_metrics[f"{key}爬坡P95历史_m_s"] = float(hist_q[0])
        ramp_metrics[f"{key}爬坡P95生成_m_s"] = float(gen_q[0])
        ramp_metrics[f"{key}爬坡P95误差_%"] = float((gen_q[0] - hist_q[0]) / max(hist_q[0], 1e-9) * 100)
        ramp_metrics[f"{key}爬坡P99历史_m_s"] = float(hist_q[1])
        ramp_metrics[f"{key}爬坡P99生成_m_s"] = float(gen_q[1])
        ramp_metrics[f"{key}爬坡P99误差_%"] = float((gen_q[1] - hist_q[1]) / max(hist_q[1], 1e-9) * 100)
    return {
        "地市": city,
        "年景类型": case,
        "历史同类年": "、".join(map(str, years)),
        "同类历史年数": len(years),
        "历史平均风速_m_s": hist_mean,
        "生成平均风速_m_s": float(gen.mean()),
        "平均风速误差_%": (float(gen.mean()) - hist_mean) / max(hist_mean, 1e-9) * 100,
        "历史年平均风速立方_m3_s3": hist_v3,
        "生成年平均风速立方_m3_s3": float(np.mean(gen_values**3)),
        "年平均风速立方误差_%": (float(np.mean(gen_values**3)) - hist_v3) / max(hist_v3, 1e-9) * 100,
        "风速持续曲线nRMSE_%": float(np.sqrt(np.mean((gen_dur - hist_dur) ** 2)) / max(hist_mean, 1e-9) * 100),
        "自相关平均绝对误差": float(np.nanmean(np.abs(gen_ac - hist_ac))),
        **ramp_metrics,
        "插入低风事件次数": int((plan["事件类型"] == "低风").sum()) if len(plan) else 0,
        "插入大风事件次数": int((plan["事件类型"] == "大风").sum()) if len(plan) else 0,
        "插入事件总持续小时": int(plan["持续小时"].sum()) if len(plan) else 0,
    }


def evaluate_city(city: str, history: pd.Series, classes: pd.DataFrame, scenarios: dict[str, pd.Series], plans: dict[str, pd.DataFrame], train_years: tuple[int, ...]) -> pd.DataFrame:
    """计算一个地市三类场景的完整指标表（内存返回，不写文件）。

    Args:
        city (str): 地市键。
        history (pd.Series): 北京时间索引的风速历史序列。
        classes (pd.DataFrame): classify_years 的输出。
        scenarios (dict[str, pd.Series]): 资源年类型 → 场景序列。
        plans (dict[str, pd.DataFrame]): 资源年类型 → 事件插入方案表。
        train_years (tuple[int, ...]): 训练年份。

    Returns:
        pd.DataFrame: 三行指标表，每行对应一类资源年。
    """
    rows = []
    for case in RESOURCE_YEAR_TYPES:
        years = classes.loc[
            classes["资源年类型"].eq(case) & classes.year.isin(train_years), "year"
        ].astype(int).tolist()
        reference = historical_reference(history, years)
        rows.append(metric_row(city, case, scenarios[case], reference, years, plans.get(case, pd.DataFrame())))
    return pd.DataFrame(rows)
