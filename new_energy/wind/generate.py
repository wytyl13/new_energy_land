#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : generate.py

步4+5：GPR 基础场景生成与逐级校准、极端事件插入，得到三类 8760 小时风速场景。

算法与旧版 generate_city_wind_scenarios_template.py 逐行等价，参数与随机种子不变，
仅去掉了文件读写。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler

from ..constants import RANDOM_SEED, RESOURCE_YEAR_TYPES
from ..utils.time_axis import no_feb29, year_index
from .events import contiguous_runs

GPR_SAMPLE_SIZE = 800
SOFT_QUANTILE_STRENGTH = 1.0     # 事件插入前逐月分位数校准强度
GLOBAL_MAPPING_STRENGTH = 0.50   # 事件插入后全局经验分位数映射强度
MEAN_PULLBACK = 0.30             # 事件插入后向同类历史均值回拉比例

logger = logging.getLogger(__name__)


def time_features(index: pd.DatetimeIndex) -> np.ndarray:
    """把时间轴编码为周期性特征矩阵（年/日/月三角函数 + 年内进度）。

    Args:
        index (pd.DatetimeIndex): 时间轴。

    Returns:
        np.ndarray: 形状 (len(index), 7) 的特征矩阵。
    """
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


def fit_base(history: pd.Series, target_index: pd.DatetimeIndex) -> tuple[np.ndarray, float]:
    """用 GPR 拟合历史风速均值并叠加 AR(1) 残差采样，生成基础场景。

    Args:
        history (pd.Series): 北京时间索引的风速历史序列。
        target_index (pd.DatetimeIndex): 目标年时间轴。

    Returns:
        tuple[np.ndarray, float]: (8760 小时基础场景, 残差 lag1 自相关系数 φ)。
    """
    rng = np.random.default_rng(RANDOM_SEED)
    x_all = time_features(history.index)
    y_all = history.to_numpy(float)
    chosen = np.sort(rng.choice(len(y_all), size=min(GPR_SAMPLE_SIZE, len(y_all)), replace=False))
    scaler = StandardScaler().fit(x_all[chosen])
    kernel = (
        ConstantKernel(1.0, constant_value_bounds="fixed")
        * RBF(length_scale=[1.2, 1.2, 0.9, 0.9, 1.3, 1.3, 1.5], length_scale_bounds="fixed")
        + WhiteKernel(noise_level=0.15, noise_level_bounds="fixed")
    )
    model = GaussianProcessRegressor(
        kernel=kernel, optimizer=None, normalize_y=True, alpha=1e-6, random_state=RANDOM_SEED
    )
    model.fit(scaler.transform(x_all[chosen]), y_all[chosen])

    def predict(index: pd.DatetimeIndex) -> np.ndarray:
        """对给定时间轴分段预测（每段 3000 点防内存峰值）。

        Args:
            index (pd.DatetimeIndex): 时间轴。

        Returns:
            np.ndarray: 预测值数组。
        """
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
    return base, phi


def monthly_level_calibration(base: pd.Series, reference: pd.Series) -> pd.Series:
    """逐月把基础场景的均值和标准差对齐到同类历史参考序列。

    Args:
        base (pd.Series): 基础场景序列。
        reference (pd.Series): 同类资源年的历史参考序列。

    Returns:
        pd.Series: 逐月水平校准后的场景序列。
    """
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


def monthly_soft_quantile_calibration(scene: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
    """按月份温和调整边缘分布，保留部分 GPR 残差生成结果。

    Args:
        scene (pd.Series): 待校准的场景序列。
        reference (pd.Series): 同类资源年的历史参考序列。
        strength (float): 校准强度（0~1，越大越贴近参考分位数）。

    Returns:
        pd.Series: 逐月软分位数校准后的场景序列。
    """
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


def insert_with_ramp_blend(scene: np.ndarray, start: int, segment: np.ndarray) -> None:
    """把事件序列写入场景，并在事件前后各 3 小时做坡度过渡。

    Args:
        scene (np.ndarray): 待修改的场景数组（就地写入）。
        start (int): 插入起始下标。
        segment (np.ndarray): 事件风速序列。

    Returns:
        None
    """
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
    """按事件标准把低风/大风事件插入场景，并返回插入方案。

    Args:
        case (str): 资源年类型名称。
        target_index (pd.DatetimeIndex): 目标年时间轴。
        scene (pd.Series): 待插入事件的场景序列。
        events (pd.DataFrame): extract_events 输出的事件表。
        standards (pd.DataFrame): build_event_standards 输出的年均次数标准。
        segments (dict[str, np.ndarray]): 事件序列字典。
        rng (np.random.Generator): 随机数发生器（事件次数与位置的随机来源）。

    Returns:
        tuple[pd.Series, pd.DataFrame]: (插入事件后的场景, 插入方案表)。
    """
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
                    score = float(np.mean(baseline)) if event_type == "低风" else -float(np.mean(baseline))
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
    output = pd.Series(np.clip(values, 0.0, None), index=target_index, name=scene.name)
    return output, pd.DataFrame(rows)


def global_empirical_quantile_mapping(scene: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
    """全年经验分位数映射，直接约束边缘分布并保留场景时间排序。

    Args:
        scene (pd.Series): 待映射的场景序列。
        reference (pd.Series): 同类资源年的历史参考序列。
        strength (float): 映射强度（0~1，越大越贴近参考边缘分布）。

    Returns:
        pd.Series: 全局分位数映射后的场景序列。
    """
    values = scene.to_numpy(float)
    ref = reference.dropna().to_numpy(float)
    if len(values) < 20 or len(ref) < 20:
        return scene.clip(lower=0.0)
    order = np.argsort(values, kind="mergesort")
    probabilities = (np.arange(len(values)) + 0.5) / len(values)
    mapped = np.empty(len(values), dtype=float)
    mapped[order] = np.quantile(ref, probabilities)
    return pd.Series(
        (1.0 - strength) * values + strength * mapped, index=scene.index, name=scene.name
    ).clip(lower=0.0)


def generate_scenarios(
    history: pd.Series,
    classes: pd.DataFrame,
    events: pd.DataFrame,
    standards: pd.DataFrame,
    segments: dict[str, np.ndarray],
    scenario_axis_year: int,
    train_years: tuple[int, ...],
) -> tuple[dict[str, pd.Series], pd.DataFrame, float]:
    """生成三类资源年场景：基础场景 → 逐级校准 → 事件插入 → 全局映射。

    Args:
        history (pd.Series): 北京时间索引的风速历史序列。
        classes (pd.DataFrame): classify_years 的输出。
        events (pd.DataFrame): extract_events 输出的事件表。
        standards (pd.DataFrame): build_event_standards 输出的年均次数标准。
        segments (dict[str, np.ndarray]): 事件序列字典。
        scenario_axis_year (int): 场景时间轴年份（只当日历模板，借 8760 小时轴用）。
        train_years (tuple[int, ...]): 训练年份。

    Returns:
        tuple[dict[str, pd.Series], pd.DataFrame, float]:
            (三类场景字典, 事件插入方案汇总表, GPR 残差 lag1 系数 φ)。
    """
    target_index = year_index(scenario_axis_year)
    target_index = target_index[no_feb29(target_index)]
    logger.info("GPR 基础场景拟合: %s (训练样本 %d 小时, 采样 %d)...",
                history.name, len(history), min(GPR_SAMPLE_SIZE, len(history)))
    base_values, phi = fit_base(history, target_index)
    logger.info("GPR 基础场景拟合完成: 残差lag1 φ=%.3f", phi)
    base = pd.Series(base_values, index=target_index, name=history.name)

    scenarios: dict[str, pd.Series] = {}
    all_plans = []
    for i, case in enumerate(RESOURCE_YEAR_TYPES):
        years = classes.loc[
            classes["资源年类型"].eq(case) & classes.year.isin(train_years), "year"
        ].astype(int).tolist()
        if not years:
            raise ValueError(f"{history.name} 没有{case}训练年")
        reference_series = history.loc[history.index.year.isin(years)]
        calibrated = monthly_level_calibration(base.copy(), reference_series)
        calibrated = monthly_soft_quantile_calibration(calibrated, reference_series, SOFT_QUANTILE_STRENGTH)
        generated, plan = make_event_plan(
            case, target_index, calibrated, events, standards, segments,
            np.random.default_rng(RANDOM_SEED + 100 + i),
        )
        generated = global_empirical_quantile_mapping(generated, reference_series, GLOBAL_MAPPING_STRENGTH)
        # 事件插入可能抬高整体均值，向同类历史均值回拉，避免资源水平漂移。
        reference_mean = float(reference_series.mean())
        if generated.mean() > 1e-8:
            correction = 1.0 + MEAN_PULLBACK * (reference_mean / float(generated.mean()) - 1.0)
            generated = (generated * correction).clip(lower=0.0)
        scenarios[case] = generated
        all_plans.append(plan)
        logger.debug("%s %s: 校准+事件插入完成 (插入 %d 个事件, 均值 %.2f m/s)",
                     history.name, case, len(plan), float(generated.mean()))
    return scenarios, pd.concat(all_plans, ignore_index=True), phi
