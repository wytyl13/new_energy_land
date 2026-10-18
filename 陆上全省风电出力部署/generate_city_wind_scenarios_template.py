# -*- coding: utf-8 -*-
"""儋州市本地分类、生成与评估三类风速场景。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "hainan_city_weather_csv_strict_inside" / "wind"
OUTPUT_DIR = BASE_DIR / "海南儋州市三类风速场景_地市独立生成_严格边界_2000_2024训练_2025目标_全局分位数50%校正版"
CITY = "460400_儋州市"
TRAIN_YEARS = tuple(range(2000, 2025))
SOURCE_YEARS = tuple(range(2000, 2026))
TARGET_YEAR = 2025
CASE_NAMES = ["偏小资源年", "正常资源年", "偏大资源年"]
AUTOCORR_LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]
RANDOM_SEED = 20261008
GPR_SAMPLE_SIZE = 800
ENC = "utf-8-sig"


def year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")


def no_feb29(index: pd.DatetimeIndex) -> np.ndarray:
    return ~((index.month == 2) & (index.day == 29))


def time_features(index: pd.DatetimeIndex) -> np.ndarray:
    hour_of_year = ((index.dayofyear - 1) * 24 + index.hour).to_numpy(float)
    leap_day_shift = index.is_leap_year & (index.month > 2)
    hour_of_year[leap_day_shift] -= 24.0
    hour_of_year = np.clip(hour_of_year, 0.0, 8759.0)
    hour = index.hour.to_numpy(float)
    month = index.month.to_numpy(float)
    return np.column_stack(
        [
            np.sin(2 * np.pi * hour_of_year / 8760.0),
            np.cos(2 * np.pi * hour_of_year / 8760.0),
            np.sin(2 * np.pi * hour / 24.0),
            np.cos(2 * np.pi * hour / 24.0),
            np.sin(2 * np.pi * month / 12.0),
            np.cos(2 * np.pi * month / 12.0),
            hour_of_year / 8760.0,
        ]
    )


def load_history() -> tuple[pd.Series, pd.DataFrame]:
    pieces = []
    for year in SOURCE_YEARS:
        path = INPUT_DIR / f"hainan_wind_city_{year}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        raw = pd.read_csv(path, encoding=ENC)
        if CITY not in raw.columns:
            raise KeyError(f"{path.name} 缺少列 {CITY}")
        times = pd.to_datetime(raw["time"], errors="coerce") + pd.Timedelta(hours=8)
        values = pd.to_numeric(raw[CITY], errors="coerce").to_numpy(float)
        pieces.append(pd.Series(values, index=times, name=CITY))

    source = pd.concat(pieces).sort_index()
    source = source[~source.index.duplicated(keep="last")]
    complete_index = pd.date_range(
        f"{TRAIN_YEARS[0]}-01-01", f"{TARGET_YEAR}-12-31 23:00:00", freq="h"
    )
    aligned = source.reindex(complete_index)
    missing = int(aligned.isna().sum())
    aligned = aligned.interpolate(method="time", limit_direction="both").clip(lower=0.0)

    blocks = []
    quality = []
    for year in SOURCE_YEARS:
        full = aligned.reindex(year_index(year))
        block = full.loc[no_feb29(full.index)]
        if len(block) != 8760 or block.isna().any():
            raise ValueError(f"{year} 年儋州序列不是完整 8760 小时")
        blocks.append(block)
        quality.append(
            {
                "年份": year,
                "时间口径": "ERA5 valid_time UTC + 8 h，北京时间",
                "小时数": len(block),
                "跨年连续对齐后缺失小时总数": missing,
                "平均风速_m_s": float(block.mean()),
            }
        )
    return pd.concat(blocks).sort_index(), pd.DataFrame(quality)


def classify_years(history: pd.Series) -> pd.DataFrame:
    rows = []
    for year in SOURCE_YEARS:
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
    training = result[result.year.isin(TRAIN_YEARS)]
    p25, p75 = training["年平均风速立方_m3_s3"].quantile([0.25, 0.75])
    result["P25阈值_mean_v3"] = float(p25)
    result["P75阈值_mean_v3"] = float(p75)
    result["资源年类型"] = np.where(
        result["年平均风速立方_m3_s3"] <= p25,
        "偏小资源年",
        np.where(result["年平均风速立方_m3_s3"] >= p75, "偏大资源年", "正常资源年"),
    )
    result["是否训练年"] = result.year.isin(TRAIN_YEARS)
    return result


def fit_base(history: pd.Series, target_index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(RANDOM_SEED)
    x_all = time_features(history.index)
    y_all = history.to_numpy(float)
    chosen = np.sort(rng.choice(len(y_all), size=min(GPR_SAMPLE_SIZE, len(y_all)), replace=False))
    scaler = StandardScaler().fit(x_all[chosen])
    kernel = (
        ConstantKernel(1.0, constant_value_bounds="fixed")
        * RBF(
            length_scale=[1.2, 1.2, 0.9, 0.9, 1.3, 1.3, 1.5],
            length_scale_bounds="fixed",
        )
        + WhiteKernel(noise_level=0.15, noise_level_bounds="fixed")
    )
    model = GaussianProcessRegressor(
        kernel=kernel,
        optimizer=None,
        normalize_y=True,
        alpha=1e-6,
        random_state=RANDOM_SEED,
    )
    model.fit(scaler.transform(x_all[chosen]), y_all[chosen])

    def predict(index: pd.DatetimeIndex) -> np.ndarray:
        x = scaler.transform(time_features(index))
        return np.concatenate([model.predict(x[pos : pos + 3000]) for pos in range(0, len(x), 3000)])

    pred_hist = predict(history.index)
    pred_target = predict(target_index)
    residual = y_all - pred_hist
    phi = float(np.clip(pd.Series(residual).autocorr(lag=1), 0.0, 0.97))
    innovations = rng.normal(size=len(target_index))
    latent = np.zeros(len(target_index), dtype=float)
    latent[0] = innovations[0]
    innovation_scale = np.sqrt(max(1.0 - phi**2, 1e-4))
    for i in range(1, len(latent)):
        latent[i] = phi * latent[i - 1] + innovation_scale * innovations[i]
    ranks = rankdata(residual, method="average")
    residual_sorted = np.sort(residual)
    residual_probability = (ranks - 0.5) / len(ranks)
    quantile_probability = np.clip(norm.cdf(latent), residual_probability.min(), residual_probability.max())
    residual_sample = np.quantile(residual_sorted, quantile_probability)
    base = np.clip(pred_target + residual_sample, 0.0, None)
    return base, np.array([phi])


def monthly_level_calibration(base: pd.Series, reference: pd.Series) -> pd.Series:
    output = base.copy()
    for month in range(1, 13):
        target_mask = output.index.month == month
        ref = reference.loc[reference.index.month == month].to_numpy(float)
        current = output.loc[target_mask].to_numpy(float)
        if len(ref) < 10 or len(current) < 10:
            continue
        current_std = float(np.std(current))
        ref_std = float(np.std(ref))
        if current_std > 1e-8:
            output.loc[target_mask] = (current - current.mean()) * (ref_std / current_std) + float(ref.mean())
        else:
            output.loc[target_mask] = float(ref.mean())
    return output.clip(lower=0.0)


def monthly_soft_quantile_calibration(
    scene: pd.Series, reference: pd.Series, strength: float = 0.35
) -> pd.Series:
    """按月份温和调整边缘分布，保留部分GPR残差生成结果。"""
    output = scene.copy()
    for month in range(1, 13):
        mask = output.index.month == month
        ref = reference.loc[reference.index.month == month].dropna().to_numpy(float)
        values = output.loc[mask].to_numpy(float)
        if len(ref) < 20 or len(values) < 20:
            continue
        order = np.argsort(values, kind="mergesort")
        probabilities = (np.arange(len(values)) + 0.5) / len(values)
        mapped = np.empty(len(values), dtype=float)
        mapped[order] = np.quantile(ref, probabilities)
        output.loc[mask] = (1.0 - strength) * values + strength * mapped
    return output.clip(lower=0.0)


def contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, mask.astype(bool), False].astype(int)
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def extract_events(history: pd.Series, year_class: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    threshold_low, threshold_high = history.quantile([0.10, 0.90])
    class_map = year_class.set_index("year")["资源年类型"].to_dict()
    rows = []
    segments: dict[str, np.ndarray] = {}
    sequence = 0
    for year in TRAIN_YEARS:
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
                event_id = f"DANZHOU_{event_type}_{sequence:05d}"
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


def build_event_standards(events: pd.DataFrame, year_class: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for case in CASE_NAMES:
        years = year_class.loc[
            year_class.year.isin(TRAIN_YEARS) & year_class["资源年类型"].eq(case), "year"
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


def insert_with_ramp_blend(scene: np.ndarray, start: int, segment: np.ndarray) -> None:
    stop = min(start + len(segment), len(scene))
    values = segment[: stop - start]
    scene[start:stop] = values
    for step in range(1, 4):
        pos = start - step
        if pos >= 0:
            event_weight = (4 - step) / 4.0
            scene[pos] = (1.0 - event_weight) * scene[pos] + event_weight * values[0]
        pos = stop - 1 + step
        if pos < len(scene):
            event_weight = (4 - step) / 4.0
            scene[pos] = event_weight * values[-1] + (1.0 - event_weight) * scene[pos]


def make_event_plan(
    case: str,
    target_index: pd.DatetimeIndex,
    scene: pd.Series,
    events: pd.DataFrame,
    standards: pd.DataFrame,
    segments: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> tuple[pd.Series, pd.DataFrame]:
    values = scene.to_numpy(float).copy()
    occupied = np.zeros(len(values), dtype=bool)
    rows = []
    for month in range(1, 13):
        month_positions = np.flatnonzero(target_index.month == month)
        for event_type in ["低风", "大风"]:
            standard = standards.loc[
                standards["年景类型"].eq(case)
                & standards["月份"].eq(month)
                & standards["事件类型"].eq(event_type)
            ].iloc[0]
            mean_count = float(standard["年均事件次数"])
            count = int(np.floor(mean_count)) + int(rng.random() < mean_count % 1)
            pool = events.loc[
                events["年景类型"].eq(case)
                & events["月份"].eq(month)
                & events["事件类型"].eq(event_type)
            ]
            if pool.empty:
                continue
            for _ in range(count):
                source = pool.iloc[int(rng.integers(0, len(pool)))]
                event_values = segments[source.event_id]
                duration = len(event_values)
                starts = month_positions[month_positions + duration <= len(target_index)]
                starts = starts[target_index[starts + duration - 1].month == month]
                if len(starts) == 0:
                    continue
                candidate_starts = rng.choice(starts, size=min(80, len(starts)), replace=False)
                best: tuple[float, int] | None = None
                for pos in candidate_starts:
                    pos = int(pos)
                    left, right = max(0, pos - 3), min(len(values), pos + duration + 3)
                    if occupied[left:right].any():
                        continue
                    baseline = values[pos : pos + duration]
                    if event_type == "低风":
                        score = float(np.mean(baseline))
                    else:
                        score = -float(np.mean(baseline))
                    before = abs(float(values[pos - 1] - event_values[0])) if pos else 0.0
                    after_pos = pos + duration
                    after = abs(float(values[after_pos] - event_values[-1])) if after_pos < len(values) else 0.0
                    score += 0.08 * (before + after)
                    if best is None or score < best[0]:
                        best = (score, pos)
                if best is None:
                    continue
                pos = best[1]
                insert_with_ramp_blend(values, pos, event_values)
                occupied[max(0, pos - 3) : min(len(values), pos + duration + 3)] = True
                rows.append(
                    {
                        "事件编号": f"SCENE_{case}_{len(rows) + 1:04d}",
                        "年景类型": case,
                        "事件类型": event_type,
                        "月份": month,
                        "开始时间": target_index[pos],
                        "持续小时": duration,
                        "来源年份": int(source.year),
                        "来源事件编号": source.event_id,
                    }
                )
    output = pd.Series(np.clip(values, 0.0, None), index=target_index, name=CITY)
    return output, pd.DataFrame(rows)


def post_event_distribution_calibration(
    scene: pd.Series, reference: pd.Series, plan: pd.DataFrame, strength: float = 0.85
) -> pd.Series:
    """事件插入后只校正非事件时段，避免把极端事件重新抹平。"""
    output = scene.copy()
    occupied = np.zeros(len(output), dtype=bool)
    positions = {t: i for i, t in enumerate(output.index)}
    for row in plan.itertuples(index=False):
        start = positions.get(pd.Timestamp(row.开始时间))
        if start is not None:
            occupied[start:min(start + int(row.持续小时), len(output))] = True
    for month in range(1, 13):
        month_mask = output.index.month == month
        eligible = month_mask & ~occupied
        ref = reference.loc[reference.index.month == month].dropna().to_numpy(float)
        values = output.loc[eligible].to_numpy(float)
        if len(ref) < 20 or len(values) < 20:
            continue
        order = np.argsort(values, kind="mergesort")
        probabilities = (np.arange(len(values)) + 0.5) / len(values)
        mapped = np.empty(len(values), dtype=float)
        mapped[order] = np.quantile(ref, probabilities)
        output.loc[eligible] = (1.0 - strength) * values + strength * mapped
    return output.clip(lower=0.0)


def global_empirical_quantile_mapping(
    scene: pd.Series, reference: pd.Series, strength: float = 1.0
) -> pd.Series:
    """全年经验分位数映射，直接约束边缘分布并保留场景时间排序。"""
    values = scene.to_numpy(float)
    ref = reference.dropna().to_numpy(float)
    if len(values) < 20 or len(ref) < 20:
        return scene.clip(lower=0.0)
    order = np.argsort(values, kind="mergesort")
    probabilities = (np.arange(len(values)) + 0.5) / len(values)
    mapped = np.empty(len(values), dtype=float)
    mapped[order] = np.quantile(ref, probabilities)
    return pd.Series((1.0 - strength) * values + strength * mapped, index=scene.index, name=scene.name).clip(lower=0.0)


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
    # The reference monthly profile is computed from each training year's original month labels.
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


def metric_row(case: str, gen: pd.Series, reference: dict[str, object], years: list[int], plan: pd.DataFrame) -> dict[str, object]:
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
        "地市": "儋州市",
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


def plot_evaluation(history: pd.Series, scenarios: dict[str, pd.Series], classes: pd.DataFrame, metrics: pd.DataFrame) -> Path:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 4, figsize=(17, 11), constrained_layout=True)
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    for row, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        ref = historical_reference(history, years)
        gen = scenarios[case]
        color = colors[case]
        hist_samples = np.asarray(ref["blocks"]).ravel()

        ax = axes[row, 0]
        bins = np.linspace(0, max(float(np.quantile(hist_samples, 0.999)), float(gen.quantile(0.999))) * 1.05, 45)
        ax.hist(hist_samples, bins=bins, density=True, alpha=0.52, color="#6c9cc3", label="历史同类年")
        ax.hist(gen, bins=bins, density=True, alpha=0.48, color=color, label="生成场景")
        ax.set_title(f"{case}：风速概率密度")
        ax.set_xlabel("风速 (m/s)")
        ax.set_ylabel("概率密度")
        ax.grid(alpha=0.2)
        if row == 0:
            ax.legend(fontsize=8)

        ax = axes[row, 1]
        ax.plot(np.arange(1, 8761), ref["duration"], color="#6c9cc3", label="历史同类年均值")
        ax.plot(np.arange(1, 8761), duration_curve(gen.to_numpy(float)), color=color, label="生成场景")
        ax.set_title(f"{case}：风速持续曲线")
        ax.set_xlabel("超越小时数")
        ax.set_ylabel("风速 (m/s)")
        ax.grid(alpha=0.2)
        if row == 0:
            ax.legend(fontsize=8)

        ax = axes[row, 2]
        lags = np.asarray(AUTOCORR_LAGS)
        ax.plot(lags, ref["autocorr"], "o-", color="#6c9cc3", label="历史同类年均值")
        ax.plot(lags, autocorr(gen), "s-", color=color, label="生成场景")
        ax.set_title(f"{case}：风速自相关")
        ax.set_xlabel("滞后小时")
        ax.set_ylabel("自相关系数")
        ax.grid(alpha=0.2)
        if row == 0:
            ax.legend(fontsize=8)

        ax = axes[row, 3]
        hist_ramp = np.concatenate([np.abs(block[3:] - block[:-3]) for block in np.asarray(ref["blocks"])])
        gen_ramp = np.abs(gen.to_numpy()[3:] - gen.to_numpy()[:-3])
        max_ramp = max(float(np.quantile(hist_ramp, 0.999)), float(np.quantile(gen_ramp, 0.999)))
        bins = np.linspace(0, max_ramp * 1.05, 40)
        ax.hist(hist_ramp, bins=bins, density=True, alpha=0.52, color="#6c9cc3", label="历史同类年")
        ax.hist(gen_ramp, bins=bins, density=True, alpha=0.48, color=color, label="生成场景")
        ax.set_title(f"{case}：3 h绝对爬坡分布")
        ax.set_xlabel("风速变化量 (m/s)")
        ax.set_ylabel("概率密度")
        ax.grid(alpha=0.2)
        if row == 0:
            ax.legend(fontsize=8)
    fig.suptitle("儋州市三类风速场景与历史同类年评估", fontsize=16, fontweight="bold")
    out = OUTPUT_DIR / "评估图" / "儋州市三类风速场景评估图.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_evaluation_separate(history: pd.Series, scenarios: dict[str, pd.Series], classes: pd.DataFrame) -> list[Path]:
    """按资源年分别输出四联评估图。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    outputs = []
    for case in CASE_NAMES:
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        ref = historical_reference(history, years); gen = scenarios[case]; hist_samples = np.asarray(ref["blocks"]).ravel()
        fig, axes = plt.subplots(1, 4, figsize=(17, 4.2), constrained_layout=True)
        bins = np.linspace(0, max(float(np.quantile(hist_samples, .999)), float(gen.quantile(.999))) * 1.05, 45)
        axes[0].hist(hist_samples, bins=bins, density=True, alpha=.52, color="#6c9cc3", label="历史同类年")
        axes[0].hist(gen, bins=bins, density=True, alpha=.48, color=colors[case], label="生成场景")
        axes[0].set_title("风速概率密度"); axes[0].set_xlabel("风速 (m/s)"); axes[0].set_ylabel("概率密度")
        axes[1].plot(np.arange(1,8761), ref["duration"], color="#6c9cc3", label="历史同类年均值")
        axes[1].plot(np.arange(1,8761), duration_curve(gen.to_numpy(float)), color=colors[case], label="生成场景")
        axes[1].set_title("风速持续曲线"); axes[1].set_xlabel("超越小时数"); axes[1].set_ylabel("风速 (m/s)")
        axes[2].plot(AUTOCORR_LAGS, ref["autocorr"], "o-", color="#6c9cc3", label="历史同类年均值")
        axes[2].plot(AUTOCORR_LAGS, autocorr(gen), "s-", color=colors[case], label="生成场景")
        axes[2].set_title("风速自相关"); axes[2].set_xlabel("滞后小时"); axes[2].set_ylabel("自相关系数")
        hist_ramp=np.concatenate([np.abs(block[3:]-block[:-3]) for block in np.asarray(ref["blocks"])]); gen_ramp=np.abs(gen.to_numpy()[3:]-gen.to_numpy()[:-3]); mx=max(float(np.quantile(hist_ramp,.999)),float(np.quantile(gen_ramp,.999))); rb=np.linspace(0,mx*1.05,40)
        axes[3].hist(hist_ramp,bins=rb,density=True,alpha=.52,color="#6c9cc3",label="历史同类年"); axes[3].hist(gen_ramp,bins=rb,density=True,alpha=.48,color=colors[case],label="生成场景")
        axes[3].set_title("3 h绝对爬坡分布"); axes[3].set_xlabel("风速变化量 (m/s)"); axes[3].set_ylabel("概率密度")
        for ax in axes: ax.grid(alpha=.2); ax.legend(fontsize=8)
        fig.suptitle(f"儋州市{case}风速场景与历史同类年评估", fontsize=15, fontweight="bold")
        out=OUTPUT_DIR/"评估图"/f"儋州市_{case}_风速场景评估图.png"; out.parent.mkdir(parents=True,exist_ok=True); fig.savefig(out,dpi=220,bbox_inches="tight"); plt.close(fig); outputs.append(out)
    return outputs


def plot_curves(history: pd.Series, scenarios: dict[str, pd.Series], classes: pd.DataFrame) -> Path:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 3, figsize=(16, 10), constrained_layout=True)
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    days = np.arange(1, 366)
    months = np.arange(1, 13)
    for row, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        gen = scenarios[case]
        color = colors[case]
        ref_blocks = []
        monthly = []
        for year in years:
            block = history.loc[history.index.year == year]
            block = block.loc[no_feb29(block.index)].iloc[:8760]
            ref_blocks.append(block.to_numpy(float))
            monthly.append(block.groupby(block.index.month).mean().reindex(months).to_numpy(float))
        hist_mat = np.vstack(ref_blocks)

        ax = axes[row, 0]
        for block in hist_mat:
            ax.plot(np.arange(8760), block, color="#8daec8", alpha=0.10, linewidth=0.45)
        ax.plot(np.arange(8760), gen.to_numpy(float), color=color, linewidth=0.9, label="生成场景")
        ax.set_title(f"{case}：8760小时风速")
        ax.set_xlabel("小时")
        ax.set_ylabel("风速 (m/s)")
        ax.grid(alpha=0.18)
        if row == 0:
            ax.legend(fontsize=8)

        ax = axes[row, 1]
        hist_daily = hist_mat.reshape(len(hist_mat), 365, 24).mean(axis=2)
        gen_daily = gen.to_numpy(float).reshape(365, 24).mean(axis=1)
        for block in hist_daily:
            ax.plot(days, block, color="#8daec8", alpha=0.16, linewidth=0.55)
        ax.plot(days, gen_daily, color=color, linewidth=1.1, label="生成场景")
        ax.set_title(f"{case}：365天日均风速")
        ax.set_xlabel("日序")
        ax.set_ylabel("风速 (m/s)")
        ax.grid(alpha=0.18)
        if row == 0:
            ax.legend(fontsize=8)

        ax = axes[row, 2]
        monthly_matrix = np.vstack(monthly)
        gen_monthly = gen.groupby(gen.index.month).mean().reindex(months).to_numpy(float)
        for block in monthly_matrix:
            ax.plot(months, block, "o-", color="#8daec8", alpha=0.35, linewidth=0.7, markersize=2.5)
        ax.plot(months, gen_monthly, "o-", color=color, linewidth=1.5, markersize=4, label="生成场景")
        ax.set_title(f"{case}：月均风速")
        ax.set_xlabel("月份")
        ax.set_ylabel("风速 (m/s)")
        ax.set_xticks(months)
        ax.grid(alpha=0.18)
        if row == 0:
            ax.legend(fontsize=8)
    fig.suptitle("儋州市三类风速场景与历史同类年曲线对比", fontsize=16, fontweight="bold")
    out = OUTPUT_DIR / "评估图" / "儋州市8760小时_365天_月均风速对比.png"
    fig.savefig(out, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[1/6] 读取严格边界格点儋州风速并按 UTC+8 对齐...")
    history_all, quality = load_history()
    history = history_all.loc[history_all.index.year.isin(TRAIN_YEARS)]
    target_index = year_index(TARGET_YEAR)
    target_index = target_index[no_feb29(target_index)]
    print("[2/6] 按儋州本地 mean(v^3) 的 P25/P75 划分资源年...")
    year_class = classify_years(history_all)
    print(year_class.to_string(index=False))
    events, segments = extract_events(history, year_class)
    standards = build_event_standards(events, year_class)
    print(f"[3/6] 儋州本地识别到 {len(events)} 个持续至少3小时的低风/大风事件")
    print("[4/6] 训练 GPR + 自相关残差，生成2025年基础场景...")
    base_values, phi = fit_base(history, target_index)
    base = pd.Series(base_values, index=target_index, name=CITY)
    scenarios: dict[str, pd.Series] = {}
    metric_rows = []
    autocorr_rows = []
    all_plans = []
    print("[5/6] 按儋州同类年逐月校准并插入本地极端事件...")
    for i, case in enumerate(CASE_NAMES):
        years = year_class.loc[
            year_class["资源年类型"].eq(case) & year_class.year.isin(TRAIN_YEARS), "year"
        ].astype(int).tolist()
        if not years:
            raise ValueError(f"儋州没有{case}训练年")
        reference_series = history.loc[history.index.year.isin(years)]
        calibrated = monthly_level_calibration(base.copy(), reference_series)
        calibrated = monthly_soft_quantile_calibration(
            calibrated, reference_series, strength=1.0
        )
        generated, plan = make_event_plan(
            case,
            target_index,
            calibrated,
            events,
            standards,
            segments,
            np.random.default_rng(RANDOM_SEED + 100 + i),
        )
        generated = global_empirical_quantile_mapping(
            generated, reference_series, strength=0.50
        )
        # 事件插入可能抬高整体均值，向儋州同类历史均值回拉一半，避免资源水平漂移。
        reference_mean = float(reference_series.mean())
        if generated.mean() > 1e-8:
            correction = 1.0 + 0.30 * (reference_mean / float(generated.mean()) - 1.0)
            generated = (generated * correction).clip(lower=0.0)
        scenarios[case] = generated
        all_plans.append(plan)
        case_dir = OUTPUT_DIR / case
        case_dir.mkdir(parents=True, exist_ok=True)
        generated.to_frame().to_csv(case_dir / "01_儋州市8760风速场景.csv", index_label="时间", encoding=ENC, float_format="%.6f")
        plan.to_csv(case_dir / "02_儋州市极端事件插入方案.csv", index=False, encoding=ENC)
        reference = historical_reference(history, years)
        metric_rows.append(metric_row(case, generated, reference, years, plan))
        generated_ac = autocorr(generated)
        for lag, hist_value, gen_value in zip(AUTOCORR_LAGS, reference["autocorr"], generated_ac):
            autocorr_rows.append(
                {
                    "地市": "儋州市",
                    "年景类型": case,
                    "滞后小时": lag,
                    "历史同类年自相关": hist_value,
                    "生成场景自相关": gen_value,
                    "绝对误差": abs(gen_value - hist_value),
                }
            )

    metric_table = pd.DataFrame(metric_rows)
    metric_dir = OUTPUT_DIR / "评估指标"
    metric_dir.mkdir(parents=True, exist_ok=True)
    metric_table.to_csv(metric_dir / "儋州市三类风速场景量化评估指标.csv", index=False, encoding=ENC)
    pd.DataFrame(autocorr_rows).to_csv(metric_dir / "儋州市三类场景自相关明细.csv", index=False, encoding=ENC)
    pd.concat(all_plans, ignore_index=True).to_csv(metric_dir / "儋州市三类场景极端事件插入汇总.csv", index=False, encoding=ENC)

    history_all.rename(CITY).to_frame().to_csv(OUTPUT_DIR / "01_儋州市严格边界风速_2000_2025_UTC校时.csv", index_label="时间", encoding=ENC, float_format="%.6f")
    quality.to_csv(OUTPUT_DIR / "02_时间对齐与数据质量.csv", index=False, encoding=ENC)
    year_class.to_csv(OUTPUT_DIR / "03_儋州市资源年分类_mean_v3_P25_P75.csv", index=False, encoding=ENC)
    events.to_csv(OUTPUT_DIR / "04_儋州市极端风速事件库_P10_P90_3h.csv", index=False, encoding=ENC)
    standards.to_csv(OUTPUT_DIR / "05_儋州市分资源年分月事件标准.csv", index=False, encoding=ENC)

    figure1 = plot_evaluation(history, scenarios, year_class, metric_table)
    separate_figures = plot_evaluation_separate(history, scenarios, year_class)
    figure2 = plot_curves(history, scenarios, year_class)
    info = {
        "地市": "儋州市",
        "空间数据": "严格落入地市边界内的ERA5格点均值",
        "训练年份": "2000-2024",
        "目标场景年份": TARGET_YEAR,
        "时间处理": "原始 ERA5 valid_time UTC + 8 h 转为北京时间后连续拼接；闰年剔除2月29日，形成8760小时年序列",
        "资源年划分": "仅基于儋州市2000-2024年mean(v^3)的P25/P75；偏小<=P25，偏大>=P75",
        "基础生成": "儋州市历史序列GPR趋势 + lag-1自相关残差模拟",
        "本地校准": "事件插入后进行50%强度的全局经验分位数映射，并进行30%强度的均值微调",
        "极端事件": "儋州市历史风速P10/P90、连续至少3小时，按同类年-月份历史年均频次抽取并在事件边界渐变3小时",
        "残差lag1系数": float(phi[0]),
        "评估对照": "每类场景对比儋州市2000-2024年本地同类历史年均值",
        "运行耗时_s": round(time.perf_counter() - started, 2),
    }
    (OUTPUT_DIR / "运行说明.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n量化指标：")
    print(metric_table.to_string(index=False))
    print(f"\n图表：\n{figure1}\n{figure2}\n" + "\n".join(map(str, separate_figures)))
    print(f"输出目录：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
