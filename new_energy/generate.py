#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : generate.py

步4+5：场景生成（GPR 拟合 + AR(1) 残差抽样 + 逐级校准 + 极端事件插入）。

风/光的生成链结构相似但目标与采样不同（风直接生成风速、光生成辐照损失，
风有全局分位数映射与均值回拉、光有月累计能量校正），各写各的阶段类，
不做配置驱动的通用框架（D-46）。随机种子与抽样顺序冻结不变。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler

from .config import SolarConfig, WindConfig
from .constants import RANDOM_SEED, RESOURCE_YEAR_TYPES
from .events import EventLibrary
from .utils.time_axis import no_feb29, year_index

logger = logging.getLogger(__name__)


def wind_time_features(index: pd.DatetimeIndex) -> np.ndarray:
    """把时间轴编码为风的周期性特征矩阵（年/日/月三角函数 + 年内进度，7 列）。

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


def solar_time_features(index: pd.DatetimeIndex, ideal: np.ndarray) -> np.ndarray:
    """构造光的 GPR 输入特征（年/日/月周期展开 + 年内位置 + 理想辐照度，8 列）。

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
    x = scaler.transform(solar_time_features(index, ideal))
    return np.concatenate([model.predict(x[i : i + 3000]) for i in range(0, len(x), 3000)])


class WindScenarioGenerator:
    """风电场景生成：基础场景 → 逐级校准 → 事件插入 → 全局映射与均值回拉。"""

    def __init__(self, config: WindConfig):
        """记录配置（场景轴年份、训练年份与生成调参）。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.config = config
        self.axis_year = config.axis_year
        self.train_years = config.train_years

    def __call__(
        self,
        history: pd.Series,
        classes: pd.DataFrame,
        library: EventLibrary,
    ) -> tuple[dict[str, pd.Series], pd.DataFrame, float]:
        """生成三类资源年风速场景（场景轴年 8760 小时时间轴）。

        Args:
            history (pd.Series): 北京时间索引的风速历史序列（训练年）。
            classes (pd.DataFrame): 资源年分类结果。
            library (EventLibrary): 事件库三件套。

        Returns:
            tuple[dict[str, pd.Series], pd.DataFrame, float]:
                (三类场景字典, 事件插入方案汇总表, GPR 残差 lag1 系数 φ)。
        """
        target_index = year_index(self.axis_year)
        target_index = target_index[no_feb29(target_index)]
        logger.info("GPR 基础场景拟合: %s (训练样本 %d 小时, 采样 %d)...",
                    history.name, len(history), min(self.config.gpr_sample_size, len(history)))
        base_values, phi = self._fit_base(history, target_index)
        logger.info("GPR 基础场景拟合完成: 残差lag1 φ=%.3f", phi)
        base = pd.Series(base_values, index=target_index, name=history.name)

        scenarios: dict[str, pd.Series] = {}
        all_plans = []
        for i, case in enumerate(RESOURCE_YEAR_TYPES):
            years = classes.loc[
                classes["资源年类型"].eq(case) & classes.year.isin(self.train_years), "year"
            ].astype(int).tolist()
            if not years:
                raise ValueError(f"{history.name} 没有{case}训练年")
            reference_series = history.loc[history.index.year.isin(years)]
            calibrated = self._monthly_level_calibration(base.copy(), reference_series)
            calibrated = self._monthly_soft_quantile_calibration(calibrated, reference_series, self.config.soft_quantile_strength)
            generated, plan = self._make_event_plan(
                case, target_index, calibrated, library,
                np.random.default_rng(RANDOM_SEED + 100 + i),
            )
            generated = self._global_empirical_quantile_mapping(generated, reference_series, self.config.global_mapping_strength)
            # 事件插入可能抬高整体均值，向同类历史均值回拉，避免资源水平漂移。
            reference_mean = float(reference_series.mean())
            if generated.mean() > 1e-8:
                correction = 1.0 + self.config.mean_pullback * (reference_mean / float(generated.mean()) - 1.0)
                generated = (generated * correction).clip(lower=0.0)
            scenarios[case] = generated
            all_plans.append(plan)
            logger.debug("%s %s: 校准+事件插入完成 (插入 %d 个事件, 均值 %.2f m/s)",
                         history.name, case, len(plan), float(generated.mean()))
        return scenarios, pd.concat(all_plans, ignore_index=True), phi

    def _fit_base(self, history: pd.Series, target_index: pd.DatetimeIndex) -> tuple[np.ndarray, float]:
        """用 GPR 拟合历史风速均值并叠加 AR(1) 残差采样，生成基础场景。

        Args:
            history (pd.Series): 北京时间索引的风速历史序列。
            target_index (pd.DatetimeIndex): 目标年时间轴。

        Returns:
            tuple[np.ndarray, float]: (8760 小时基础场景, 残差 lag1 自相关系数 φ)。
        """
        rng = np.random.default_rng(RANDOM_SEED)
        x_all = wind_time_features(history.index)
        y_all = history.to_numpy(float)
        chosen = np.sort(rng.choice(len(y_all), size=min(self.config.gpr_sample_size, len(y_all)), replace=False))
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
            x = scaler.transform(wind_time_features(index))
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

    def _monthly_level_calibration(self, base: pd.Series, reference: pd.Series) -> pd.Series:
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

    def _monthly_soft_quantile_calibration(self, scene: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
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

    def _make_event_plan(
        self,
        case: str,
        target_index: pd.DatetimeIndex,
        scene: pd.Series,
        library: EventLibrary,
        rng: np.random.Generator,
    ) -> tuple[pd.Series, pd.DataFrame]:
        """按事件标准把低风/大风事件插入场景，并返回插入方案。

        Args:
            case (str): 资源年类型名称。
            target_index (pd.DatetimeIndex): 目标年时间轴。
            scene (pd.Series): 待插入事件的场景序列。
            library (EventLibrary): 事件库三件套。
            rng (np.random.Generator): 随机数发生器（事件次数与位置的随机来源）。

        Returns:
            tuple[pd.Series, pd.DataFrame]: (插入事件后的场景, 插入方案表)。
        """
        events, standards, segments = library.events, library.standards, library.segments
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
                    self._insert_with_ramp_blend(values, pos, event_values)
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

    @staticmethod
    def _insert_with_ramp_blend(scene: np.ndarray, start: int, segment: np.ndarray) -> None:
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

    def _global_empirical_quantile_mapping(self, scene: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
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


class SolarScenarioGenerator:
    """光伏场景生成：辐照损失 GPR + AR(1) → 分位数校准 → 低辐照事件插入 → 月能量校正。"""

    def __init__(self, config: SolarConfig):
        """记录配置（场景轴年份、训练年份与生成调参）。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config
        self.axis_year = config.axis_year
        self.train_years = config.train_years

    def __call__(
        self,
        history: pd.Series,
        ideal_history: pd.Series,
        classes: pd.DataFrame,
        library: EventLibrary,
        latitude: float,
        longitude: float,
    ) -> tuple[dict[str, pd.Series], pd.DataFrame, float]:
        """生成三类资源年辐照度场景（场景轴年 8760 小时时间轴）。

        Args:
            history (pd.Series): 训练年辐照度历史序列。
            ideal_history (pd.Series): 同时间轴的理想辐照度序列。
            classes (pd.DataFrame): 逐年资源年分类结果。
            library (EventLibrary): 事件库三件套。
            latitude (float): 地市代表性纬度（度）。
            longitude (float): 地市代表性经度（度）。

        Returns:
            tuple[dict[str, pd.Series], pd.DataFrame, float]:
                (资源年类型 → 场景序列, 三类插入方案合并表, GPR 残差 lag-1 系数 φ)。
        """
        target_index = year_index(self.axis_year)
        target_index = target_index[no_feb29(target_index)]
        target_ideal = pd.Series(ideal_irradiance(target_index, latitude, longitude), index=target_index, name="理想辐照度_W_m2")

        base_loss, phi = self._generate_base_loss(history, ideal_history, target_ideal)
        base_ghi = (target_ideal - base_loss).clip(lower=0.0)

        scenarios: dict[str, pd.Series] = {}
        plans = []
        for i, case in enumerate(RESOURCE_YEAR_TYPES):
            years = classes.loc[classes["资源年类型"].eq(case) & classes.year.isin(self.train_years), "year"].astype(int).tolist()
            if not years:
                raise ValueError(f"没有{case}训练年")
            reference = history.loc[history.index.year.isin(years)]
            calibrated = self._calibrate_daylight_quantiles(base_ghi.copy(), target_ideal, reference, self.config.quantile_blend)
            loss = (target_ideal - calibrated).clip(lower=0.0)
            loss, plan = self._insert_event_loss(
                loss, target_ideal, library, case, np.random.default_rng(RANDOM_SEED + i + 100)
            )
            scene = (target_ideal - loss).clip(lower=0.0).where(target_ideal > self.config.daylight_threshold, 0.0)
            scene, _factors = self._monthly_energy_correction(scene, target_ideal, reference)
            scenarios[case] = scene
            plans.append(plan)
        return scenarios, pd.concat(plans, ignore_index=True), phi

    def _generate_base_loss(self, history: pd.Series, ideal: pd.Series, target_ideal: pd.Series) -> tuple[pd.Series, float]:
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
        daylight = (ideal > self.config.daylight_threshold).to_numpy()
        valid = np.flatnonzero(daylight & np.isfinite(loss.to_numpy(float)))
        chosen = np.sort(rng.choice(valid, size=min(self.config.gpr_sample_size, len(valid)), replace=False))
        x = solar_time_features(history.index, ideal.to_numpy(float))
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

        target_day = (target_ideal.to_numpy(float) > self.config.daylight_threshold)
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

    def _calibrate_daylight_quantiles(self, scene: pd.Series, ideal: pd.Series, reference: pd.Series, strength: float) -> pd.Series:
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
        daylight = ideal > self.config.daylight_threshold
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

    def _insert_event_loss(
        self,
        loss_scene: pd.Series,
        target_ideal: pd.Series,
        library: EventLibrary,
        case: str,
        rng: np.random.Generator,
    ) -> tuple[pd.Series, pd.DataFrame]:
        """按分月年均次数标准把历史低辐照事件段插入损失场景（边界 3 小时渐变）。

        Args:
            loss_scene (pd.Series): 待插入的辐照损失场景。
            target_ideal (pd.Series): 目标年理想辐照度。
            library (EventLibrary): 事件库三件套。
            case (str): 资源年类型。
            rng (np.random.Generator): 本场景专用随机数发生器。

        Returns:
            tuple[pd.Series, pd.DataFrame]: (插入事件后的损失场景, 插入方案表)。
        """
        events, standards, segments = library.events, library.standards, library.segments
        values = loss_scene.to_numpy(float).copy()
        occupied = np.zeros(len(values), dtype=bool)
        target_index = loss_scene.index
        core = (target_ideal > self.config.event_ideal_threshold).to_numpy()
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

    def _monthly_energy_correction(self, scene: pd.Series, ideal: pd.Series, reference: pd.Series) -> tuple[pd.Series, pd.Series]:
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
        daylight = ideal > self.config.daylight_threshold
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
