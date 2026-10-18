# -*- coding: utf-8 -*-
"""可选评估：三类风速场景与历史同类年的量化指标；绘图函数仅在显式调用时使用。"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .classify import CASE_NAMES
from .history import no_feb29

AUTOCORR_LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]


def duration_curve(values: np.ndarray) -> np.ndarray:
    return np.sort(np.asarray(values, dtype=float))[::-1]


def autocorr(series: pd.Series) -> np.ndarray:
    return np.array([series.autocorr(lag=lag) for lag in AUTOCORR_LAGS], dtype=float)


def ramp_quantiles(series: pd.Series, hours: int) -> tuple[float, float]:
    ramp = series.diff(hours).abs().dropna()
    return float(ramp.quantile(0.95)), float(ramp.quantile(0.99))


def historical_reference(history: pd.Series, years: list[int]) -> dict[str, object]:
    blocks = []
    for year in years:
        block = history.loc[history.index.year == year].copy()
        block = block.loc[no_feb29(block.index)].iloc[:8760]
        if len(block) == 8760:
            blocks.append(block.reset_index(drop=True))
    if not blocks:
        raise ValueError("同类历史年为空，无法评估")
    matrix = np.vstack([block.to_numpy(float) for block in blocks])
    ac = np.vstack([autocorr(block) for block in blocks])
    ramp1 = np.array([ramp_quantiles(block, 1) for block in blocks])
    ramp3 = np.array([ramp_quantiles(block, 3) for block in blocks])
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
    gen_values = gen.to_numpy(float)
    hist_mean = float(reference["mean"])
    hist_v3 = float(reference["mean_v3"])
    hist_ac = np.asarray(reference["autocorr"])
    gen_ac = autocorr(gen)
    hist_dur = np.asarray(reference["duration"])
    gen_dur = duration_curve(gen_values)
    ramp_metrics = {}
    for hours, key in [(1, "1h"), (3, "3h")]:
        hist_q = np.asarray(reference["ramp1" if hours == 1 else "ramp3"])
        gen_q = np.asarray(ramp_quantiles(gen, hours))
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
    """一个地市三类场景的指标表（内存返回，不落盘）。"""
    rows = []
    for case in CASE_NAMES:
        years = classes.loc[
            classes["资源年类型"].eq(case) & classes.year.isin(train_years), "year"
        ].astype(int).tolist()
        reference = historical_reference(history, years)
        rows.append(metric_row(city, case, scenarios[case], reference, years, plans.get(case, pd.DataFrame())))
    return pd.DataFrame(rows)


def plot_evaluation(city: str, history: pd.Series, scenarios: dict[str, pd.Series], output_dir: Path) -> Path:
    """四联评估图：概率密度、持续曲线、自相关、3h爬坡。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for case in CASE_NAMES:
        gen = scenarios[case]
        ref_blocks = np.vstack(
            [history.loc[history.index.year == y].to_numpy(float) for y in sorted(set(history.index.year))]
        )
        hist_samples = ref_blocks.ravel()
        fig, axes = plt.subplots(1, 4, figsize=(17, 4.2), constrained_layout=True)
        bins = np.linspace(0, max(float(np.quantile(hist_samples, 0.999)), float(gen.quantile(0.999))) * 1.05, 45)
        axes[0].hist(hist_samples, bins=bins, density=True, alpha=0.52, color="#6c9cc3", label="历史年")
        axes[0].hist(gen, bins=bins, density=True, alpha=0.48, color=colors[case], label="生成场景")
        axes[0].set_title("风速概率密度")
        axes[1].plot(np.arange(1, 8761), duration_curve(hist_samples), color="#6c9cc3", label="历史年")
        axes[1].plot(np.arange(1, 8761), duration_curve(gen.to_numpy(float)), color=colors[case], label="生成场景")
        axes[1].set_title("风速持续曲线")
        axes[2].plot(AUTOCORR_LAGS, autocorr(history), "o-", color="#6c9cc3", label="历史年")
        axes[2].plot(AUTOCORR_LAGS, autocorr(gen), "s-", color=colors[case], label="生成场景")
        axes[2].set_title("风速自相关")
        hist_ramp = np.abs(np.diff(hist_samples, 3))
        gen_ramp = np.abs(gen.to_numpy()[3:] - gen.to_numpy()[:-3])
        bins_r = np.linspace(0, max(float(np.quantile(hist_ramp, 0.999)), float(np.quantile(gen_ramp, 0.999))) * 1.05, 40)
        axes[3].hist(hist_ramp, bins=bins_r, density=True, alpha=0.52, color="#6c9cc3", label="历史年")
        axes[3].hist(gen_ramp, bins=bins_r, density=True, alpha=0.48, color=colors[case], label="生成场景")
        axes[3].set_title("3 h绝对爬坡分布")
        for ax in axes:
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
        fig.suptitle(f"{city}{case}风速场景评估", fontsize=15, fontweight="bold")
        out = output_dir / f"{city}_{case}_风速场景评估图.png"
        fig.savefig(out, dpi=200, bbox_inches="tight")
        plt.close(fig)
        outputs.append(out)
    return outputs[0]
