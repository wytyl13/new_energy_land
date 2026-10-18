# -*- coding: utf-8 -*-
"""步4+5：三类辐照度场景生成。

理想辐照度与实际辐照度之差作为辐照损失，GPR 拟合季节/日变化，日内 AR(1) 残差
抽样；再按资源年做温和分位数校准、按标准插入低辐照极端事件、月累计能量校正。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler

from ..common.time_axis import no_feb29, year_index
from .classify import CASE_NAMES
from .events import EVENT_IDEAL_THRESHOLD

DAYLIGHT_THRESHOLD = 50.0  # 理想辐照度超过该值视为有效白天，其他时段生成辐照度置零（W/m2）
QUANTILE_BLEND = 0.45      # 分位数校准混合强度
RANDOM_SEED = 20261008
GPR_SAMPLE_SIZE = 900      # GPR 训练抽样点数


def ideal_irradiance(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    """计算水平面理想（晴空）辐照度序列。

    Args:
        index (pd.DatetimeIndex): 北京时间整点时间轴。
        latitude (float): 纬度（度）。
        longitude (float): 经度（度）。

    Returns:
        np.ndarray: 逐小时理想辐照度（W/m2），非负。
    """
    day = index.dayofyear.to_numpy(float)
    hour = index.hour.to_numpy(float) + index.minute.to_numpy(float) / 60.0
    b = 2.0 * np.pi * (day - 81.0) / 364.0
    equation_minutes = 9.87 * np.sin(2.0 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)
    # 时间戳为北京时间，故采用东八区 120 度标准子午线
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


def build_ideal(history: pd.Series, latitude: float, longitude: float) -> pd.Series:
    """按历史序列时间轴生成理想辐照度序列。

    Args:
        history (pd.Series): 北京时间索引的辐照度历史序列（已删闰日）。
        latitude (float): 纬度（度）。
        longitude (float): 经度（度）。

    Returns:
        pd.Series: 与历史同时间轴的理想辐照度序列。
    """
    return pd.Series(ideal_irradiance(history.index, latitude, longitude), index=history.index, name="理想辐照度_W_m2")


def time_features(index: pd.DatetimeIndex, ideal: np.ndarray) -> np.ndarray:
    """构造 GPR 输入特征（年/日/月周期展开 + 年内位置 + 理想辐照度）。

    Args:
        index (pd.DatetimeIndex): 时间轴。
        ideal (np.ndarray): 对应的理想辐照度。

    Returns:
        np.ndarray: 形状 (len(index), 8) 的特征矩阵。
    """
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
    """分块（每块 3000 行）预测，避免一次性构造过大的特征矩阵。

    Args:
        model: 训练好的 GPR 模型。
        scaler: 与模型配套的 StandardScaler。
        index (pd.DatetimeIndex): 预测时间轴。
        ideal (np.ndarray): 对应的理想辐照度。

    Returns:
        np.ndarray: 逐点预测的辐照损失（W/m2）。
    """
    x = scaler.transform(time_features(index, ideal))
    return np.concatenate([model.predict(x[i : i + 3000]) for i in range(0, len(x), 3000)])


def generate_base_loss(history: pd.Series, ideal: pd.Series, target_ideal: pd.Series) -> tuple[pd.Series, float]:
    """用 GPR 拟合辐照损失并叠加日内 AR(1) 残差抽样，生成目标年基础损失序列。

    Args:
        history (pd.Series): 训练年辐照度历史序列。
        ideal (pd.Series): 同时间轴的理想辐照度序列。
        target_ideal (pd.Series): 目标年（8760 小时）理想辐照度序列。

    Returns:
        tuple[pd.Series, float]: (基础辐照损失序列, 残差 lag-1 系数 φ)。
    """
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

    # 仅在同一白天时段内估计残差 lag-1 持续性
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


def calibrate_daylight_quantiles(scene: pd.Series, ideal: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
    """按月份-小时把白天场景值向同类历史年分位数温和混合校准。

    Args:
        scene (pd.Series): 待校准的辐照度场景（目标年时间轴）。
        ideal (pd.Series): 目标年理想辐照度。
        reference (pd.Series): 同类资源年训练年历史序列。
        strength (float): 混合强度（0-1）。

    Returns:
        pd.Series: 校准后的场景序列（夜间置零、不超过理想辐照度）。
    """
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


def insert_event_loss(
    loss_scene: pd.Series,
    target_ideal: pd.Series,
    events: pd.DataFrame,
    standards: pd.DataFrame,
    segments: dict[str, np.ndarray],
    case: str,
    rng: np.random.Generator,
) -> tuple[pd.Series, pd.DataFrame]:
    """按分月年均次数标准把历史低辐照事件段插入损失场景（边界 3 小时渐变）。

    Args:
        loss_scene (pd.Series): 待插入的辐照损失场景。
        target_ideal (pd.Series): 目标年理想辐照度。
        events (pd.DataFrame): 事件库表。
        standards (pd.DataFrame): 分年景分月事件标准表。
        segments (dict[str, np.ndarray]): 事件编号 → 辐照损失段。
        case (str): 资源年类型。
        rng (np.random.Generator): 本场景专用随机数发生器。

    Returns:
        tuple[pd.Series, pd.DataFrame]: (插入事件后的损失场景, 插入方案表)。
    """
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
                    "事件编号": f"SCENE_{len(rows) + 1:04d}",
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
    """把各月累计辐照量校正到同类历史年年均水平（系数限制 0.70-1.60）。

    Args:
        scene (pd.Series): 待校正的辐照度场景。
        ideal (pd.Series): 目标年理想辐照度。
        reference (pd.Series): 同类资源年训练年历史序列。

    Returns:
        tuple[pd.Series, pd.Series]: (校正后场景, 各月修正系数)。
    """
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


def generate_scenarios(
    history: pd.Series,
    ideal_history: pd.Series,
    classes: pd.DataFrame,
    events: pd.DataFrame,
    standards: pd.DataFrame,
    segments: dict[str, np.ndarray],
    latitude: float,
    longitude: float,
    target_year: int = 2025,
    train_years: tuple[int, ...] = tuple(range(2000, 2025)),
) -> tuple[dict[str, pd.Series], pd.DataFrame, float]:
    """生成三类资源年的辐照度场景（目标年 8760 小时时间轴）。

    Args:
        history (pd.Series): 训练年辐照度历史序列。
        ideal_history (pd.Series): 同时间轴的理想辐照度序列。
        classes (pd.DataFrame): 逐年资源年分类结果。
        events (pd.DataFrame): 低辐照事件库表。
        standards (pd.DataFrame): 分年景分月事件标准表。
        segments (dict[str, np.ndarray]): 事件编号 → 辐照损失段。
        latitude (float): 地市代表性纬度（度）。
        longitude (float): 地市代表性经度（度）。
        target_year (int): 场景生成的目标年份。
        train_years (tuple[int, ...]): 训练年份范围。

    Returns:
        tuple[dict[str, pd.Series], pd.DataFrame, float]:
            (资源年类型 → 场景序列, 三类插入方案合并表, GPR 残差 lag-1 系数 φ)。
    """
    target_index = year_index(target_year)
    target_index = target_index[no_feb29(target_index)]
    target_ideal = pd.Series(ideal_irradiance(target_index, latitude, longitude), index=target_index, name="理想辐照度_W_m2")

    base_loss, phi = generate_base_loss(history, ideal_history, target_ideal)
    base_ghi = (target_ideal - base_loss).clip(lower=0.0)

    scenarios: dict[str, pd.Series] = {}
    plans = []
    for i, case in enumerate(CASE_NAMES):
        years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(train_years), "year"].astype(int).tolist()
        if not years:
            raise ValueError(f"没有{case}训练年")
        reference = history.loc[history.index.year.isin(years)]
        calibrated = calibrate_daylight_quantiles(base_ghi.copy(), target_ideal, reference, QUANTILE_BLEND)
        loss = (target_ideal - calibrated).clip(lower=0.0)
        loss, plan = insert_event_loss(loss, target_ideal, events, standards, segments, case, np.random.default_rng(RANDOM_SEED + i + 100))
        scene = (target_ideal - loss).clip(lower=0.0).where(target_ideal > DAYLIGHT_THRESHOLD, 0.0)
        scene, _factors = monthly_energy_correction(scene, target_ideal, reference)
        scenarios[case] = scene
        plans.append(plan)
    return scenarios, pd.concat(plans, ignore_index=True), phi
