# -*- coding: utf-8 -*-
"""儋州市严格边界格点的三类辐照度场景生成与评估。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler


BASE_DIR = Path(__file__).resolve().parent
GRID_DIR = BASE_DIR / "hainan_era5_common80_csv" / "solar"
ASSIGNMENT_FILE = BASE_DIR / "海南地市分区" / "海南共同80格点_地市归属.csv"
OUTPUT_DIR = BASE_DIR / "海南儋州市三类辐照度场景_地市独立生成_严格边界_2000_2024训练_2025目标"
CITY_CODE = "460400"
CITY_NAME = "儋州市"
CITY_COLUMN = f"{CITY_CODE}_{CITY_NAME}"
TRAIN_YEARS = tuple(range(2000, 2025))
SOURCE_YEARS = tuple(range(2000, 2026))
TARGET_YEAR = 2025
CASE_NAMES = ["偏小资源年", "正常资源年", "偏大资源年"]
DAYLIGHT_THRESHOLD = 50.0
EVENT_IDEAL_THRESHOLD = 200.0
KT_UPPER = 1.2
QUANTILE_BLEND = 0.45
RANDOM_SEED = 20261008
GPR_SAMPLE_SIZE = 900
AUTOCORR_LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]
ENC = "utf-8-sig"


def year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")


def no_feb29(index: pd.DatetimeIndex) -> np.ndarray:
    return ~((index.month == 2) & (index.day == 29))


def load_assignment() -> pd.DataFrame:
    data = pd.read_csv(ASSIGNMENT_FILE, encoding=ENC)
    data = data.loc[
        data["city_code"].astype(str).str.zfill(6).eq(CITY_CODE)
        & data["assignment_method"].eq("inside")
    ].copy()
    if data.empty:
        raise ValueError(f"没有找到{CITY_NAME}严格边界内的辐照度格点")
    return data


def load_city_history() -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    assignment = load_assignment()
    grid_columns = assignment["solar_grid"].astype(str).tolist()
    parts = []
    for year in SOURCE_YEARS:
        path = GRID_DIR / f"hainan_solar_{year}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        raw = pd.read_csv(path, encoding=ENC)
        missing_columns = sorted(set(grid_columns) - set(raw.columns))
        if missing_columns:
            raise ValueError(f"{path.name} 缺少严格边界格点列: {missing_columns}")
        timestamps = pd.to_datetime(raw["time"], errors="coerce") + pd.Timedelta(hours=8)
        values = raw[grid_columns].apply(pd.to_numeric, errors="coerce").mean(axis=1)
        parts.append(pd.Series(values.to_numpy(float), index=timestamps, name=CITY_COLUMN))

    source = pd.concat(parts).sort_index()
    source = source[~source.index.duplicated(keep="last")]
    full_index = pd.date_range("2000-01-01", "2025-12-31 23:00:00", freq="h")
    aligned = source.reindex(full_index)
    missing_count = int(aligned.isna().sum())
    aligned = aligned.interpolate(method="time", limit_direction="both").clip(lower=0.0)

    years = []
    quality = []
    for year in SOURCE_YEARS:
        block = aligned.reindex(year_index(year))
        block = block.loc[no_feb29(block.index)]
        if len(block) != 8760 or block.isna().any():
            raise ValueError(f"{year}年辐照度未形成完整8760小时")
        years.append(block)
        quality.append(
            {
                "年份": year,
                "格点数": len(grid_columns),
                "时间口径": "ERA5 UTC + 8 h，北京时间",
                "小时数": len(block),
                "跨年对齐后插值小时数": missing_count,
                "年累计辐照量_kWh_m2": float(block.sum() / 1000.0),
            }
        )
    return pd.concat(years).sort_index(), pd.DataFrame(quality), assignment


def ideal_irradiance(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    day = index.dayofyear.to_numpy(float)
    hour = index.hour.to_numpy(float) + index.minute.to_numpy(float) / 60.0
    b = 2.0 * np.pi * (day - 81.0) / 364.0
    equation_minutes = 9.87 * np.sin(2.0 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)
    # Local timestamps are Beijing time, so use the 120-degree standard meridian.
    solar_time = hour + (4.0 * (longitude - 120.0) + equation_minutes) / 60.0
    hour_angle = np.deg2rad(15.0 * (solar_time - 12.0))
    declination = np.deg2rad(23.45 * np.sin(2.0 * np.pi * (284.0 + day) / 365.0))
    latitude_rad = np.deg2rad(latitude)
    sin_elevation = (
        np.sin(latitude_rad) * np.sin(declination)
        + np.cos(latitude_rad) * np.cos(declination) * np.cos(hour_angle)
    )
    earth_sun = 1.0 + 0.033 * np.cos(2.0 * np.pi * day / 365.0)
    return np.maximum(1368.0 * earth_sun * np.maximum(sin_elevation, 0.0), 0.0)


def build_ideal(history: pd.Series, assignment: pd.DataFrame) -> pd.Series:
    latitude = float(assignment["latitude"].mean())
    longitude = float(assignment["longitude"].mean())
    values = np.concatenate(
        [ideal_irradiance(year_index(year)[no_feb29(year_index(year))], latitude, longitude) for year in SOURCE_YEARS]
    )
    return pd.Series(values, index=history.index, name="理想辐照度_W_m2")


def classify_years(history: pd.Series, ideal: pd.Series) -> pd.DataFrame:
    rows = []
    for year in SOURCE_YEARS:
        ghi = history.loc[history.index.year == year].to_numpy(float)
        ideal_values = ideal.loc[ideal.index.year == year].to_numpy(float)
        daylight = ideal_values > DAYLIGHT_THRESHOLD
        rows.append(
            {
                "year": year,
                "年累计辐照量_kWh_m2": float(np.sum(ghi) / 1000.0),
                "白天平均辐照度_W_m2": float(np.mean(ghi[daylight])) if daylight.any() else np.nan,
                "白天小时数": int(daylight.sum()),
                "白天辐照度P10_W_m2": float(np.quantile(ghi[daylight], 0.10)) if daylight.any() else np.nan,
                "白天辐照度P50_W_m2": float(np.quantile(ghi[daylight], 0.50)) if daylight.any() else np.nan,
                "白天辐照度P90_W_m2": float(np.quantile(ghi[daylight], 0.90)) if daylight.any() else np.nan,
            }
        )
    table = pd.DataFrame(rows)
    train = table[table.year.isin(TRAIN_YEARS)]
    p25, p75 = train["年累计辐照量_kWh_m2"].quantile([0.25, 0.75])
    table["P25阈值_kWh_m2"] = float(p25)
    table["P75阈值_kWh_m2"] = float(p75)
    table["资源年类型"] = np.where(
        table["年累计辐照量_kWh_m2"] <= p25,
        "偏小资源年",
        np.where(table["年累计辐照量_kWh_m2"] >= p75, "偏大资源年", "正常资源年"),
    )
    table["是否训练年"] = table.year.isin(TRAIN_YEARS)
    return table


def time_features(index: pd.DatetimeIndex, ideal: np.ndarray) -> np.ndarray:
    hour_of_year = ((index.dayofyear - 1) * 24 + index.hour).to_numpy(float)
    hour = index.hour.to_numpy(float)
    month = index.month.to_numpy(float)
    return np.column_stack(
        [
            np.sin(2.0 * np.pi * hour_of_year / 8760.0),
            np.cos(2.0 * np.pi * hour_of_year / 8760.0),
            np.sin(2.0 * np.pi * hour / 24.0),
            np.cos(2.0 * np.pi * hour / 24.0),
            np.sin(2.0 * np.pi * month / 12.0),
            np.cos(2.0 * np.pi * month / 12.0),
            hour_of_year / 8760.0,
            np.asarray(ideal, dtype=float) / 1000.0,
        ]
    )


def predict_model(model, scaler, index: pd.DatetimeIndex, ideal: np.ndarray) -> np.ndarray:
    x = scaler.transform(time_features(index, ideal))
    return np.concatenate([model.predict(x[i : i + 3000]) for i in range(0, len(x), 3000)])


def generate_base_loss(history: pd.Series, ideal: pd.Series, target_ideal: pd.Series) -> tuple[pd.Series, float]:
    rng = np.random.default_rng(RANDOM_SEED)
    loss = (ideal - history).clip(lower=0.0)
    daylight = (ideal > DAYLIGHT_THRESHOLD).to_numpy()
    valid = np.flatnonzero(daylight & np.isfinite(loss.to_numpy(float)))
    chosen = np.sort(rng.choice(valid, size=min(GPR_SAMPLE_SIZE, len(valid)), replace=False))
    x = time_features(history.index, ideal.to_numpy(float))
    scaler = StandardScaler().fit(x[chosen])
    kernel = (
        ConstantKernel(1.0, constant_value_bounds="fixed")
        * RBF(length_scale=[1.1, 1.1, 0.9, 0.9, 1.3, 1.3, 1.8, 1.2], length_scale_bounds="fixed")
        + WhiteKernel(noise_level=0.25, noise_level_bounds="fixed")
    )
    model = GaussianProcessRegressor(
        kernel=kernel,
        optimizer=None,
        normalize_y=True,
        alpha=1e-6,
        random_state=RANDOM_SEED,
    )
    model.fit(scaler.transform(x[chosen]), loss.to_numpy(float)[chosen])
    hist_pred = predict_model(model, scaler, history.index, ideal.to_numpy(float))
    target_pred = predict_model(model, scaler, target_ideal.index, target_ideal.to_numpy(float))
    residual = loss.to_numpy(float) - hist_pred
    residual[~daylight] = np.nan

    # Estimate lag-1 residual persistence only within the same daylight period.
    valid_pair = daylight[1:] & daylight[:-1]
    a = residual[:-1][valid_pair]
    b = residual[1:][valid_pair]
    if len(a) > 10 and np.std(a) > 1e-8 and np.std(b) > 1e-8:
        phi = float(np.clip(np.corrcoef(a, b)[0, 1], 0.0, 0.97))
    else:
        phi = 0.0

    target_day = (target_ideal.to_numpy(float) > DAYLIGHT_THRESHOLD)
    sampled_residual = np.zeros(len(target_ideal), dtype=float)
    target_index = target_ideal.index
    for month in range(1, 13):
        hist_mask = daylight & (history.index.month.to_numpy() == month) & np.isfinite(residual)
        target_month = np.flatnonzero(target_day & (target_index.month.to_numpy() == month))
        pool = residual[hist_mask]
        if len(pool) < 20:
            pool = residual[np.isfinite(residual)]
        residual_sorted = np.sort(pool)
        month_dates = target_index[target_month].normalize().unique()
        for date in month_dates:
            positions = target_month[target_index[target_month].normalize() == date]
            if len(positions) == 0:
                continue
            z = np.empty(len(positions), dtype=float)
            z[0] = rng.normal()
            innovations = rng.normal(size=max(0, len(positions) - 1))
            scale = np.sqrt(max(1.0 - phi**2, 1e-4))
            for i in range(1, len(positions)):
                z[i] = phi * z[i - 1] + scale * innovations[i - 1]
            probabilities = np.clip(norm.cdf(z), 0.001, 0.999)
            sampled_residual[positions] = np.quantile(residual_sorted, probabilities)
    base_loss = np.clip(target_pred + sampled_residual, 0.0, target_ideal.to_numpy(float))
    base_loss[~target_day] = target_ideal.to_numpy(float)[~target_day]
    return pd.Series(base_loss, index=target_index, name="辐照损失_W_m2"), phi


def contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, mask.astype(bool), False].astype(int)
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def extract_low_irradiance_events(history: pd.Series, ideal: pd.Series, classes: pd.DataFrame):
    kt = (history / ideal.replace(0.0, np.nan)).clip(0.0, KT_UPPER)
    core = ideal > EVENT_IDEAL_THRESHOLD
    threshold = float(kt.loc[core].dropna().quantile(0.10))
    class_map = classes.set_index("year")["资源年类型"].to_dict()
    records = []
    segments: dict[str, np.ndarray] = {}
    sequence = 0
    for year in TRAIN_YEARS:
        idx = history.index[history.index.year == year]
        year_mask = (kt.loc[idx] <= threshold) & core.loc[idx]
        for start, end in contiguous_runs(year_mask.to_numpy()):
            if end - start < 3:
                continue
            sequence += 1
            event_id = f"DANZHOU_LOW_KT_{sequence:05d}"
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
    return pd.DataFrame(records, columns=["event_id", "year", "年景类型", "月份", "开始时间", "结束时间", "持续小时", "晴空指数P10阈值", "事件平均晴空指数"]), segments


def build_event_standards(events: pd.DataFrame, classes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for case in CASE_NAMES:
        years = classes.loc[classes.year.isin(TRAIN_YEARS) & classes["资源年类型"].eq(case), "year"].astype(int).tolist()
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


def calibrate_daylight_quantiles(scene: pd.Series, ideal: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
    output = scene.copy()
    daylight = ideal > DAYLIGHT_THRESHOLD
    for month in range(1, 13):
        for hour in range(24):
            mask = daylight & (scene.index.month == month) & (scene.index.hour == hour)
            ref_mask = (reference.index.month == month) & (reference.index.hour == hour) & (reference > 1.0)
            current = scene.loc[mask].to_numpy(float)
            ref = reference.loc[ref_mask].to_numpy(float)
            if len(current) < 4 or len(ref) < 20:
                continue
            order = np.argsort(current, kind="mergesort")
            probabilities = (np.arange(len(current)) + 0.5) / len(current)
            mapped = np.empty(len(current), dtype=float)
            mapped[order] = np.quantile(ref, probabilities)
            output.loc[mask] = (1.0 - strength) * current + strength * mapped
    output.loc[~daylight] = 0.0
    return output.clip(lower=0.0).where(output <= ideal, ideal)


def insert_event_loss(loss_scene: pd.Series, target_ideal: pd.Series, events: pd.DataFrame, standards: pd.DataFrame, segments: dict[str, np.ndarray], case: str, rng: np.random.Generator):
    values = loss_scene.to_numpy(float).copy()
    occupied = np.zeros(len(values), dtype=bool)
    target_index = loss_scene.index
    core = (target_ideal > EVENT_IDEAL_THRESHOLD).to_numpy()
    rows = []
    for month in range(1, 13):
        month_pos = np.flatnonzero(target_index.month == month)
        standard = standards.loc[standards["年景类型"].eq(case) & standards["月份"].eq(month)].iloc[0]
        mean_count = float(standard["年均事件次数"])
        count = int(np.floor(mean_count)) + int(rng.random() < mean_count % 1)
        pool = events.loc[events["年景类型"].eq(case) & events["月份"].eq(month)]
        if pool.empty:
            pool = events.loc[events["年景类型"].eq(case)]
        for _ in range(count):
            if pool.empty:
                continue
            source = pool.iloc[int(rng.integers(0, len(pool)))]
            segment = segments[source.event_id]
            duration = len(segment)
            starts = month_pos[month_pos + duration <= len(target_index)]
            starts = starts[target_index[starts + duration - 1].month == month]
            starts = starts[np.array([core[pos : pos + duration].all() for pos in starts], dtype=bool)]
            starts = starts[np.array([not occupied[max(0, pos - 3) : min(len(values), pos + duration + 3)].any() for pos in starts], dtype=bool)]
            if len(starts) == 0:
                continue
            pos = int(rng.choice(starts))
            end = pos + duration
            values[pos:end] = segment
            for step in range(1, 4):
                before = pos - step
                if before >= 0:
                    w = (4 - step) / 4.0
                    values[before] = (1.0 - w) * values[before] + w * segment[0]
                after = end - 1 + step
                if after < len(values):
                    w = (4 - step) / 4.0
                    values[after] = w * segment[-1] + (1.0 - w) * values[after]
            occupied[max(0, pos - 3) : min(len(values), end + 3)] = True
            rows.append(
                {
                    "事件编号": f"SCENE_DANZHOU_{len(rows) + 1:04d}",
                    "年景类型": case,
                    "月份": month,
                    "开始时间": target_index[pos],
                    "持续小时": duration,
                    "来源年份": int(source.year),
                    "来源事件编号": source.event_id,
                }
            )
    return pd.Series(np.maximum(values, 0.0), index=target_index, name="辐照损失_W_m2"), pd.DataFrame(rows)


def monthly_energy_correction(scene: pd.Series, ideal: pd.Series, reference: pd.Series) -> tuple[pd.Series, pd.Series]:
    output = scene.copy()
    factors = {}
    daylight = ideal > DAYLIGHT_THRESHOLD
    for month in range(1, 13):
        mask = (output.index.month == month) & daylight
        ref = reference.loc[reference.index.month == month]
        target_total = float(ref.sum() / ref.index.year.nunique())
        current = float(output.loc[mask].sum())
        factor = target_total / current if current > 1e-9 else 1.0
        factor = float(np.clip(factor, 0.70, 1.60))
        factors[month] = factor
        output.loc[mask] = np.minimum(output.loc[mask] * factor, ideal.loc[mask])
    output.loc[~daylight] = 0.0
    return output.clip(lower=0.0), pd.Series(factors, name="月度能量修正系数")


def autocorr_values(series: pd.Series) -> np.ndarray:
    values = series.to_numpy(float)
    values = values - np.mean(values)
    denominator = float(np.dot(values, values))
    if denominator <= 0:
        return np.zeros(len(AUTOCORR_LAGS))
    return np.array([np.dot(values[:-lag], values[lag:]) / denominator for lag in AUTOCORR_LAGS], dtype=float)


def ramp_values(series: pd.Series, ideal: pd.Series, hours: int) -> np.ndarray:
    values = series.to_numpy(float)
    day = (ideal.to_numpy(float) > DAYLIGHT_THRESHOLD)
    valid = day[hours:] & day[:-hours]
    return np.abs(values[hours:] - values[:-hours])[valid]


def reference_stats(history: pd.Series, ideal_history: pd.Series, years: list[int]) -> dict[str, object]:
    blocks, daylight_values, monthly_values = [], [], []
    ac, ramp1, ramp3, daily = [], [], [], []
    for year in years:
        block = history.loc[history.index.year == year]
        ideal = ideal_history.loc[ideal_history.index.year == year]
        block = block.loc[no_feb29(block.index)]
        ideal = ideal.loc[no_feb29(ideal.index)]
        values = block.to_numpy(float)
        daylight = ideal.to_numpy(float) > DAYLIGHT_THRESHOLD
        blocks.append(values)
        daylight_values.append(values[daylight])
        ac.append(autocorr_values(block.reset_index(drop=True)))
        ramp1.append(ramp_values(block.reset_index(drop=True), ideal.reset_index(drop=True), 1))
        ramp3.append(ramp_values(block.reset_index(drop=True), ideal.reset_index(drop=True), 3))
        daily.append(values.reshape(365, 24).mean(axis=1))
        monthly_values.append(block.groupby(block.index.month).sum().reindex(range(1, 13)).to_numpy(float))
    matrix = np.vstack(blocks)
    pooled_daylight = np.concatenate(daylight_values)
    quantiles = np.linspace(0, 1, 101)
    duration = np.quantile(pooled_daylight, quantiles)
    return {
        "blocks": matrix,
        "pooled_daylight": pooled_daylight,
        "duration": duration,
        "mean_daylight": float(np.mean(pooled_daylight)),
        "annual_energy": float(np.mean(matrix.sum(axis=1) / 1000.0)),
        "acf": np.nanmean(np.vstack(ac), axis=0),
        "ramp1": np.concatenate(ramp1),
        "ramp3": np.concatenate(ramp3),
        "daily": np.nanmean(np.vstack(daily), axis=0),
        "monthly": np.nanmean(np.vstack(monthly_values), axis=0) / np.array([31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]),
        "hourly_mean": np.nanmean(matrix, axis=0),
    }


def metric_row(case: str, scene: pd.Series, ideal: pd.Series, stats: dict, years: list[int], plan: pd.DataFrame) -> dict[str, object]:
    day = ideal > DAYLIGHT_THRESHOLD
    daylight = scene.loc[day].to_numpy(float)
    duration = np.quantile(daylight, np.linspace(0, 1, 101))
    hist_duration = np.asarray(stats["duration"])
    gen_energy = float(scene.sum() / 1000.0)
    hist_energy = float(stats["annual_energy"])
    gen_ac = autocorr_values(scene)
    row = {
        "地市": CITY_NAME,
        "年景类型": case,
        "历史同类年": "、".join(map(str, years)),
        "同类历史年数": len(years),
        "历史年均累计辐照量_kWh_m2": hist_energy,
        "生成累计辐照量_kWh_m2": gen_energy,
        "累计辐照量误差_%": (gen_energy - hist_energy) / max(hist_energy, 1e-9) * 100.0,
        "历史白天平均辐照度_W_m2": float(stats["mean_daylight"]),
        "生成白天平均辐照度_W_m2": float(daylight.mean()),
        "白天平均辐照度误差_%": (float(daylight.mean()) - float(stats["mean_daylight"])) / max(float(stats["mean_daylight"]), 1e-9) * 100.0,
        "白天概率分布KS距离": float(ks_2samp(daylight, np.asarray(stats["pooled_daylight"])).statistic),
        "白天持续曲线nRMSE_%": float(np.sqrt(np.mean((duration - hist_duration) ** 2)) / max(float(stats["mean_daylight"]), 1e-9) * 100.0),
        "自相关平均绝对误差": float(np.nanmean(np.abs(gen_ac - np.asarray(stats["acf"])))),
        "低辐照事件发生次数": int(len(plan)),
        "低辐照事件持续小时": int(plan["持续小时"].sum()) if len(plan) else 0,
    }
    for hours, key in [(1, "1h"), (3, "3h")]:
        gen_ramp = ramp_values(scene, ideal, hours)
        hist_ramp = np.asarray(stats["ramp1" if hours == 1 else "ramp3"])
        for q in [0.95, 0.99]:
            name = f"{key}爬坡P{int(q * 100)}"
            gen_value = float(np.quantile(gen_ramp, q))
            hist_value = float(np.quantile(hist_ramp, q))
            row[f"历史{name}_W_m2"] = hist_value
            row[f"生成{name}_W_m2"] = gen_value
            row[f"{name}误差_%"] = (gen_value - hist_value) / max(hist_value, 1e-9) * 100.0
    return row


def plot_evaluation(history: pd.Series, ideal_history: pd.Series, scenarios: dict[str, pd.Series], classes: pd.DataFrame) -> Path:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 4, figsize=(17, 11), constrained_layout=True)
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    for r, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        stats = reference_stats(history, ideal_history, years)
        gen, color = scenarios[case], colors[case]
        pooled = stats["pooled_daylight"]
        gen_day = gen.loc[(ideal_irradiance(gen.index, float(load_assignment()["latitude"].mean()), float(load_assignment()["longitude"].mean())) > DAYLIGHT_THRESHOLD)].to_numpy(float)

        ax = axes[r, 0]
        bins = np.linspace(0, max(float(np.quantile(pooled, 0.999)), float(np.quantile(gen_day, 0.999))) * 1.03, 45)
        ax.hist(pooled, bins=bins, density=True, alpha=0.52, color="#6c9cc3", label="历史同类年")
        ax.hist(gen_day, bins=bins, density=True, alpha=0.50, color=color, label="生成场景")
        ax.set_title(f"{case}：白天辐照度概率分布")
        ax.set_xlabel("辐照度 (W/m²)")
        ax.set_ylabel("概率密度")
        ax.grid(alpha=0.2)
        if r == 0:
            ax.legend(fontsize=8)

        ax = axes[r, 1]
        probs = np.linspace(0, 1, 101)
        ax.plot(probs * 100, stats["duration"], color="#6c9cc3", label="历史同类年")
        ax.plot(probs * 100, np.quantile(gen_day, probs), color=color, label="生成场景")
        ax.set_title(f"{case}：白天辐照度持续曲线")
        ax.set_xlabel("超越概率 (%)")
        ax.set_ylabel("辐照度 (W/m²)")
        ax.grid(alpha=0.2)
        if r == 0:
            ax.legend(fontsize=8)

        ax = axes[r, 2]
        ax.plot(AUTOCORR_LAGS, stats["acf"], "o-", color="#6c9cc3", label="历史同类年")
        ax.plot(AUTOCORR_LAGS, autocorr_values(gen), "s-", color=color, label="生成场景")
        ax.set_title(f"{case}：辐照度自相关")
        ax.set_xlabel("滞后小时")
        ax.set_ylabel("自相关系数")
        ax.grid(alpha=0.2)
        if r == 0:
            ax.legend(fontsize=8)

        ax = axes[r, 3]
        hist_ramp, gen_ramp = stats["ramp3"], ramp_values(gen, pd.Series(ideal_irradiance(gen.index, float(load_assignment()["latitude"].mean()), float(load_assignment()["longitude"].mean())), index=gen.index), 3)
        maximum = max(float(np.quantile(hist_ramp, 0.999)), float(np.quantile(gen_ramp, 0.999)))
        bins = np.linspace(0, maximum * 1.03, 42)
        ax.hist(hist_ramp, bins=bins, density=True, alpha=0.52, color="#6c9cc3", label="历史同类年")
        ax.hist(gen_ramp, bins=bins, density=True, alpha=0.50, color=color, label="生成场景")
        ax.set_title(f"{case}：连续白天3 h爬坡分布")
        ax.set_xlabel("变化量 (W/m²)")
        ax.set_ylabel("概率密度")
        ax.grid(alpha=0.2)
        if r == 0:
            ax.legend(fontsize=8)
    fig.suptitle("儋州市三类光伏资源年辐照度场景评估", fontsize=16, fontweight="bold")
    path = OUTPUT_DIR / "评估图" / "儋州市三类辐照度场景评估图.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_curves(history: pd.Series, ideal_history: pd.Series, scenarios: dict[str, pd.Series], target_ideal: pd.Series, classes: pd.DataFrame) -> Path:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 3, figsize=(16, 10), constrained_layout=True)
    colors = {"偏小资源年": "#e67e22", "正常资源年": "#138a72", "偏大资源年": "#3c78b5"}
    for r, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        blocks, monthly = [], []
        for year in years:
            block = history.loc[history.index.year == year]
            block = block.loc[no_feb29(block.index)]
            blocks.append(block.to_numpy(float))
            monthly.append(block.groupby(block.index.month).mean().reindex(range(1, 13)).to_numpy(float))
        matrix = np.vstack(blocks)
        gen = scenarios[case]
        color = colors[case]
        ax = axes[r, 0]
        for block in matrix:
            ax.plot(np.arange(8760), block, color="#8daec8", alpha=0.12, linewidth=0.45)
        ax.plot(np.arange(8760), gen.to_numpy(float), color=color, linewidth=0.9, label="生成场景")
        ax.set_title(f"{case}：8760小时辐照度")
        ax.set_xlabel("小时")
        ax.set_ylabel("辐照度 (W/m²)")
        ax.grid(alpha=0.18)
        if r == 0:
            ax.legend(fontsize=8)

        ax = axes[r, 1]
        hist_daily = matrix.reshape(len(matrix), 365, 24).mean(axis=2)
        gen_daily = gen.to_numpy(float).reshape(365, 24).mean(axis=1)
        for block in hist_daily:
            ax.plot(np.arange(1, 366), block, color="#8daec8", alpha=0.18, linewidth=0.55)
        ax.plot(np.arange(1, 366), gen_daily, color=color, linewidth=1.1, label="生成场景")
        ax.set_title(f"{case}：365天日均辐照度")
        ax.set_xlabel("日序")
        ax.set_ylabel("辐照度 (W/m²)")
        ax.grid(alpha=0.18)
        if r == 0:
            ax.legend(fontsize=8)

        ax = axes[r, 2]
        for block in np.vstack(monthly):
            ax.plot(range(1, 13), block, "o-", color="#8daec8", alpha=0.35, linewidth=0.7, markersize=2.5)
        gen_month = gen.groupby(gen.index.month).mean().reindex(range(1, 13)).to_numpy(float)
        ax.plot(range(1, 13), gen_month, "o-", color=color, linewidth=1.5, markersize=4, label="生成场景")
        ax.set_title(f"{case}：月均辐照度")
        ax.set_xlabel("月份")
        ax.set_ylabel("辐照度 (W/m²)")
        ax.set_xticks(range(1, 13))
        ax.grid(alpha=0.18)
        if r == 0:
            ax.legend(fontsize=8)
    fig.suptitle("儋州市三类辐照度场景与历史同类年曲线对比", fontsize=16, fontweight="bold")
    path = OUTPUT_DIR / "评估图" / "儋州市8760小时_365天_月均辐照度对比.png"
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[1/7] 读取儋州严格边界内辐照度格点并转为北京时间...")
    history_all, quality, assignment = load_city_history()
    ideal_all = build_ideal(history_all, assignment)
    history = history_all.loc[history_all.index.year.isin(TRAIN_YEARS)]
    ideal_history = ideal_all.loc[ideal_all.index.year.isin(TRAIN_YEARS)]
    target_index = year_index(TARGET_YEAR)
    target_index = target_index[no_feb29(target_index)]
    target_ideal = pd.Series(
        ideal_irradiance(target_index, float(assignment["latitude"].mean()), float(assignment["longitude"].mean())),
        index=target_index,
        name="理想辐照度_W_m2",
    )

    print("[2/7] 按儋州年累计辐照量P25/P75划分资源年...")
    classes = classify_years(history_all, ideal_all)
    print(classes.to_string(index=False))
    events, segments = extract_low_irradiance_events(history, ideal_history, classes)
    standards = build_event_standards(events, classes)
    print(f"[3/7] 识别到{len(events)}个晴空指数P10、连续至少3小时的低辐照事件")
    print("[4/7] 用理想辐照度与实际辐照度差值训练GPR，并模拟残差持续性...")
    base_loss, phi = generate_base_loss(history, ideal_history, target_ideal)
    base_ghi = (target_ideal - base_loss).clip(lower=0.0)

    scenarios: dict[str, pd.Series] = {}
    rows, ac_rows, plans = [], [], []
    print("[5/7] 分资源年生成场景、插入极端事件并进行温和分布和月能量校正...")
    for i, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(TRAIN_YEARS), "year"].astype(int).tolist()
        if not years:
            raise ValueError(f"儋州没有{case}训练年")
        reference = history.loc[history.index.year.isin(years)]
        calibrated = calibrate_daylight_quantiles(base_ghi.copy(), target_ideal, reference, QUANTILE_BLEND)
        loss = (target_ideal - calibrated).clip(lower=0.0)
        loss, plan = insert_event_loss(
            loss,
            target_ideal,
            events,
            standards,
            segments,
            case,
            np.random.default_rng(RANDOM_SEED + i + 100),
        )
        scene = (target_ideal - loss).clip(lower=0.0).where(target_ideal > DAYLIGHT_THRESHOLD, 0.0)
        scene, factors = monthly_energy_correction(scene, target_ideal, reference)
        scenarios[case] = scene
        plans.append(plan)
        case_dir = OUTPUT_DIR / case
        case_dir.mkdir(parents=True, exist_ok=True)
        output = pd.DataFrame(
            {
                "理想辐照度_W_m2": target_ideal,
                "辐照损失_W_m2": target_ideal - scene,
                "生成辐照度_W_m2": scene,
                "晴空指数": (scene / target_ideal.replace(0.0, np.nan)).clip(0.0, KT_UPPER).fillna(0.0),
            }
        )
        output.to_csv(case_dir / "01_儋州市8760辐照度场景.csv", index_label="时间", encoding=ENC, float_format="%.6f")
        plan.to_csv(case_dir / "02_低辐照事件插入方案.csv", index=False, encoding=ENC)
        factors.to_csv(case_dir / "03_月度能量修正系数.csv", encoding=ENC)
        stats = reference_stats(history, ideal_history, years)
        rows.append(metric_row(case, scene, target_ideal, stats, years, plan))
        generated_ac = autocorr_values(scene)
        for lag, hist_ac, gen_ac in zip(AUTOCORR_LAGS, stats["acf"], generated_ac):
            ac_rows.append({"年景类型": case, "滞后小时": lag, "历史同类年自相关": hist_ac, "生成场景自相关": gen_ac, "绝对误差": abs(gen_ac - hist_ac)})

    print("[6/7] 保存场景评估指标、分类和事件数据...")
    (OUTPUT_DIR / "01_儋州市严格边界辐照度_2000_2025_UTC校时.csv").write_text("", encoding="utf-8")
    history_all.rename(CITY_COLUMN).to_frame().to_csv(OUTPUT_DIR / "01_儋州市严格边界辐照度_2000_2025_UTC校时.csv", index_label="时间", encoding=ENC, float_format="%.6f")
    ideal_all.to_frame().to_csv(OUTPUT_DIR / "02_儋州市理想辐照度_2000_2025.csv", index_label="时间", encoding=ENC, float_format="%.6f")
    quality.to_csv(OUTPUT_DIR / "03_时间对齐与数据质量.csv", index=False, encoding=ENC)
    assignment.to_csv(OUTPUT_DIR / "04_儋州严格边界辐照度格点.csv", index=False, encoding=ENC)
    classes.to_csv(OUTPUT_DIR / "05_儋州市资源年分类_累计辐照量P25_P75.csv", index=False, encoding=ENC)
    events.to_csv(OUTPUT_DIR / "06_儋州市低辐照事件库_KtP10_3h.csv", index=False, encoding=ENC)
    standards.to_csv(OUTPUT_DIR / "07_儋州市分资源年分月低辐照事件标准.csv", index=False, encoding=ENC)
    metric_dir = OUTPUT_DIR / "评估指标"
    metric_dir.mkdir(parents=True, exist_ok=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(metric_dir / "儋州市三类辐照度场景量化评估指标.csv", index=False, encoding=ENC)
    pd.DataFrame(ac_rows).to_csv(metric_dir / "儋州市三类辐照度场景自相关明细.csv", index=False, encoding=ENC)
    pd.concat(plans, ignore_index=True).to_csv(metric_dir / "儋州市三类场景低辐照事件插入汇总.csv", index=False, encoding=ENC)

    figure1 = plot_evaluation(history, ideal_history, scenarios, classes)
    figure2 = plot_curves(history, ideal_history, scenarios, target_ideal, classes)
    info = {
        "地市": CITY_NAME,
        "格点口径": "仅平均严格落入儋州边界内的共同80格点；本次使用4个格点",
        "训练年份": "2000-2024",
        "目标场景年份": TARGET_YEAR,
        "时间对齐": "ERA5 UTC + 8 h转换为北京时间，跨年连续重排；闰年删除2月29日形成8760小时",
        "资源年划分": "按儋州市年累计水平面辐照量P25/P75划分",
        "昼夜口径": f"理想水平面辐照度>{DAYLIGHT_THRESHOLD} W/m2为有效白天，其他时段生成辐照度置零",
        "基础模型": "理想辐照度与实际辐照度之差作为辐照损失，GPR拟合季节/日变化，日内AR(1)残差抽样",
        "低辐照事件": f"仅在理想辐照度>{EVENT_IDEAL_THRESHOLD} W/m2的核心日照时段识别晴空指数Kt P10，持续至少3小时；按资源年-月份历史平均次数插入，边界3小时渐变",
        "分布校准": f"同类历史年按月份-小时进行{QUANTILE_BLEND:.0%}温和分位数混合校准，并做月累计辐照量校正，系数限制0.70-1.60",
        "GPR残差lag1系数": phi,
        "运行耗时_s": round(time.perf_counter() - started, 2),
    }
    (OUTPUT_DIR / "运行说明.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[7/7] 完成。量化指标：")
    print(metrics.to_string(index=False))
    print(f"评估图: {figure1}\n曲线图: {figure2}\n输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
