#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/01
@Author  : weiyutao
@File    : solar.py

光伏模型（镜像 wind.py 结构，从旧 pipeline.SolarPowerModel 规格重写，行为冻结）：
SolarModel 在 __init__ 组装子模块，forward 顺序执行、维度逐行流转；
每个子模块以 __init__(config) + __call__(data) 为公开两方法，__call__ 即模块
forward 且置于类的最后——以"步骤N"注释分块、行尾标注维度，逻辑直接写在
__call__ 中（Generator 校准链各步为同类私有方法）；步骤4 事件库为数据类
（(E, 4) 明细长表 + 按需现取方法，损失段值不复制）；与风电的差异：
损失域生成（GHI = 理想辐照 − 损失）、白天/核心日照两级掩码、月能量校正、
POA 物理出力在轴年算完再重排输出年（辐照随日历走，重排后算会变结果）。

时间轴约定：Align 步骤1~4 之后每年恒为删 2/29 的 8760 小时整点轴，
序列装箱为 (年数, 8760) ndarray，行=年、列=年内小时位置；月由位置查表得出，
pandas 只留在门口（Align/特征索引）与出口（装箱 Series、重排输出年）。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler

from .align import Align
from .config import SolarConfig, SolarUnit
from .constants import GPR_CHUNK_SIZE, PHI_MAX, RAMP_HOURS, RANDOM_SEED, RESOURCE_YEAR_TYPES
from .result import PowerResult, SolarResult
from .utils.common import quantile_map, worker_logging
from .utils.time_axis import MONTH_STARTS, month_of, no_feb29, remap_to_year, year_index

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 模块级函数（温度气候态 / 理想辐照度）
# ─────────────────────────────────────────────────────────────────────────────

def weather_climatology(series: pd.Series, years: tuple[int, ...]) -> pd.Series:
    """把逐年温度序列按位置平均成 8760 小时气候态（删闰日、UTC+8）。

    Args:
        series (pd.Series): biz.assignment 返回的地市序列（UTC 时间索引，多年拼接）。
        years (tuple[int, ...]): 参与平均的年份。

    Returns:
        pd.Series: 8760 小时逐小时气候平均值（非闰年时间轴索引）。
    """
    blocks = []
    for year in years:
        block = series.loc[series.index.year == year]
        if block.empty:
            continue
        shifted = pd.Series(block.to_numpy(float), index=block.index + pd.Timedelta(hours=8))
        shifted = shifted.loc[~((shifted.index.month == 2) & (shifted.index.day == 29))]
        if len(shifted) == 8760:
            blocks.append(shifted.to_numpy())
    if not blocks:
        raise ValueError(f"{series.name} 没有可用的历史数据 ({years[0]}-{years[-1]})")
    return pd.Series(
        np.nanmean(np.vstack(blocks), axis=0),
        index=year_index(2025)[no_feb29(year_index(2025))],  # 8760 小时非闰年气候态轴
        name=series.name,
    )


def ideal_irradiance(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    """北京时间轴 → 水平面理想（晴空）辐照度（W/m2，非负）。

    Args:
        index (pd.DatetimeIndex): 北京时间整点时间轴。
        latitude (float): 纬度（度）。
        longitude (float): 经度（度）。

    Returns:
        np.ndarray: 逐小时理想辐照度 (len(index), )。
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


# ─────────────────────────────────────────────────────────────────────────────
# 模型子模块
# ─────────────────────────────────────────────────────────────────────────────

class EventDetails:
    """2. 低辐照事件库：训练年辐照度 + 理想辐照度 → 事件明细 (E, 4)，一行一个事件（长表，损失段值不复制）。

    列 = [year, start, dur, month]：来源年份、段起始小时、持续小时（≥3）、
    起始小时所在月（1~12）。行序 = 扫描顺序（逐年 → 段先后），行号+1 即原全局
    事件编号（LOW_KT_00001 起编）。派生数据（年景/池子/段数值）不落列——
    pool / n_case_years / segment 现取。
    """

    def __init__(
        self,
        config: SolarConfig,
        train_grid: np.ndarray,
        ideal_grid: np.ndarray,
        train_years: np.ndarray,
        train_labels: np.ndarray,
    ):
        """扫描训练年数据，构建事件明细长表。

        Args:
            config (SolarConfig): 光伏算法配置（事件识别阈值）。
            train_grid (np.ndarray): 训练年辐照度 (n, 8760)。
            ideal_grid (np.ndarray): 训练年理想辐照度 (n, 8760)。
            train_years (np.ndarray): 训练年年份数组 (n, )。
            train_labels (np.ndarray): 训练年资源年类型标签 (n, )。
        """
        # 1. 晴空指数 Kt = 辐照/理想（理想为 0 处置 NaN 防除零），截到 [0, kt_upper]；核心日照掩码
        kt = np.divide(train_grid, ideal_grid, out=np.full(train_grid.shape, np.nan), where=ideal_grid > 0)
        kt = np.clip(kt, 0.0, config.kt_upper) # (n, 8760)
        core = ideal_grid > config.event_ideal_threshold # (n, 8760)

        # 2. 全训练年核心时段 Kt 的 P 分位 → 低辐照事件阈值（单一阈值，全体共用）
        threshold = float(np.quantile(kt[core & np.isfinite(kt)], config.kt_quantile)) # 标量

        # 3. 逐训练年扫描连续低辐照段（≥ min_duration）→ 明细行 [year, start, dur, month]
        rows = []
        for row, year in enumerate(train_years):
            mask = (kt[row] <= threshold) & core[row] # (8760, ) Kt 低于阈值且处核心日照
            edges = np.diff(np.r_[False, mask, False].astype(int)) # True 段首 +1 / 段尾 -1
            for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                if end - start < config.min_duration: # 持续不足 3h 的段不算事件
                    continue
                rows.append((int(year), int(start), int(end - start), int(month_of(start)) + 1))
        self.details = np.array(rows, dtype=int).reshape(-1, 4) # (E, 4) int；无事件 (0, 4)

        # 4. 保存现取所需输入 + 各事件来源年的年景（一次算好）
        self.grid, self.ideal = train_grid, ideal_grid
        self.years, self.labels = train_years, train_labels
        self.cases = train_labels[np.searchsorted(train_years, self.details[:, 0])] # (E, )

    def pool(self, case: str, month: int | None = None) -> np.ndarray:
        """该 (年景[, 月]) 格子的历史事件行号（month=None 退回本年景全年池）。

        Args:
            case (str): 年景类型。
            month (int | None): 月份 1~12；None 不过滤月份。

        Returns:
            np.ndarray: 明细行号 (该格事件数, )，升序 = 扫描顺序。
        """
        mask = self.cases == case
        if month is not None:
            mask &= self.details[:, 3] == month
        return np.flatnonzero(mask)

    def n_case_years(self, case: str) -> int:
        """该年景训练年数（年均次数分母）。

        Args:
            case (str): 年景类型。

        Returns:
            int: 训练年数。
        """
        return int((self.labels == case).sum())

    def segment(self, row: int) -> np.ndarray:
        """第 row 个事件的损失段 = (理想 − 辐照) 切片截负（不复制存储）。

        Args:
            row (int): 明细行号。

        Returns:
            np.ndarray: 损失段 (持续小时, )。
        """
        year, start, duration, _ = self.details[row]
        r = int(np.searchsorted(self.years, year))
        return np.clip(self.ideal[r, start : start + duration] - self.grid[r, start : start + duration], 0.0, None)


class BaseLoss:
    """3 GPR 基础损失：拟合典型损失形态 + 按日 AR(1) 残差分位采样（三 case 共用）。"""

    def __init__(self, config: SolarConfig):
        """保存白天阈值与 GPR 抽样规模。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config

    @staticmethod
    def _features(index: pd.DatetimeIndex, ideal: np.ndarray) -> np.ndarray:
        """时间轴 + 理想辐照度 → GPR 输入特征（年/日/月周期展开 + 年内位置 + 理想/1000）。

        Args:
            index (pd.DatetimeIndex): 时间轴（北京时间）。
            ideal (np.ndarray): 对应理想辐照度 (len(index), )。

        Returns:
            np.ndarray: 特征矩阵 (len(index), 8)。
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
                ideal / 1000.0,
            ]
        )

    def __call__(
        self,
        history: pd.Series,
        ideal_grid: np.ndarray,
        target_index: pd.DatetimeIndex,
        target_ideal: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        """训练年辐照/理想数据 → (8760 小时基础损失, 白天残差 lag1 自相关系数 φ)。

        Args:
            history (pd.Series): 训练年对齐序列（北京时, 行序同 ideal_grid）。
            ideal_grid (np.ndarray): 训练年理想辐照度 (n, 8760)。
            target_index (pd.DatetimeIndex): 轴年时间轴 (8760, )。
            target_ideal (np.ndarray): 轴年理想辐照度 (8760, )。

        Returns:
            tuple[np.ndarray, float]: (8760 小时基础损失, φ)。
        """
        rng = np.random.default_rng(RANDOM_SEED)
        ideal = ideal_grid.ravel() # (n*8760, )
        loss = np.clip(ideal - history.to_numpy(float), 0.0, None) # (n*8760, ) 损失 = 理想 − 实际
        daylight = ideal > self.config.daylight_threshold # (n*8760, ) 白天掩码

        # 步骤1: 白天有效点随机抽样 → 特征标准化 + 固定核 GPR 拟合（不做超参优化）
        valid = np.flatnonzero(daylight & np.isfinite(loss)) # (有效点数, )
        chosen = np.sort(rng.choice(valid, size=min(self.config.gpr_sample_size, len(valid)), replace=False))
        x = self._features(history.index, ideal) # (n*8760, 8)
        scaler = StandardScaler().fit(x[chosen])
        kernel = (
            ConstantKernel(1.0, constant_value_bounds="fixed")
            * RBF(length_scale=[1.1, 1.1, 0.9, 0.9, 1.3, 1.3, 1.8, 1.2], length_scale_bounds="fixed")
            + WhiteKernel(noise_level=0.25, noise_level_bounds="fixed")
        )
        model = GaussianProcessRegressor(
            kernel=kernel, optimizer=None, normalize_y=True, alpha=1e-6, random_state=RANDOM_SEED
        )
        model.fit(scaler.transform(x[chosen]), loss[chosen])

        # 步骤2: 分块预测训练轴（残差用）与轴年（基础形态用）
        x_std = scaler.transform(x) # (n*8760, 8)
        hist_pred = np.concatenate(
            [model.predict(x_std[i : i + GPR_CHUNK_SIZE]) for i in range(0, len(x_std), GPR_CHUNK_SIZE)]
        ) # (n*8760, )
        x_target = scaler.transform(self._features(target_index, target_ideal)) # (8760, 8)
        target_pred = np.concatenate(
            [model.predict(x_target[i : i + GPR_CHUNK_SIZE]) for i in range(0, len(x_target), GPR_CHUNK_SIZE)]
        ) # (8760, )

        # 步骤3: 训练轴残差（夜间置 NaN）→ 白天相邻对相关系数 → φ（截 [0, PHI_MAX]）
        residual = loss - hist_pred # (n*8760, )
        residual[~daylight] = np.nan
        valid_pair = daylight[1:] & daylight[:-1] # 同日相邻白天对掩码
        a, b = residual[:-1][valid_pair], residual[1:][valid_pair]
        if len(a) > 10 and np.std(a) > 1e-8 and np.std(b) > 1e-8:
            phi = float(np.clip(np.corrcoef(a, b)[0, 1], 0.0, PHI_MAX))
        else:
            phi = 0.0

        # 步骤4: 逐月逐日 AR(1) 残差采样——z 链 → 正态分位 → 训练残差经验分位取样
        target_day = target_ideal > self.config.daylight_threshold # (8760, )
        sampled = np.zeros(len(target_ideal)) # (8760, )
        hist_month = history.index.month.to_numpy() # (n*8760, )
        for month, (ms, me) in enumerate(zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]), start=1):
            pool = residual[daylight & (hist_month == month) & np.isfinite(residual)] # (月白天残差数, )
            if len(pool) < 20: # 该月样本太少 → 退回全部有限残差
                pool = residual[np.isfinite(residual)]
            residual_sorted = np.sort(pool)
            for day_start in range(ms, me, 24):
                positions = np.flatnonzero(target_day[day_start : day_start + 24]) + day_start # (当日白天小时数, )
                if len(positions) == 0:
                    continue
                z = np.empty(len(positions), dtype=float)
                z[0] = rng.normal()
                innovations = rng.normal(size=max(0, len(positions) - 1)) # (当日白天小时数-1, )
                scale = np.sqrt(max(1.0 - phi**2, 1e-4)) # Math: \sqrt{1-\phi^2}
                for i in range(1, len(positions)):
                    z[i] = phi * z[i - 1] + scale * innovations[i - 1] # Math: z_t = ϕ·z_{t-1} + √(1-φ²)·ε_t
                probabilities = np.clip(norm.cdf(z), 0.001, 0.999)
                sampled[positions] = np.quantile(residual_sorted, probabilities)

        # 步骤5: 基础损失 = clip(形态预测 + 采样残差, 0, 理想)；夜间 = 理想（对应 GHI=0）
        base_loss = np.clip(target_pred + sampled, 0.0, target_ideal)
        base_loss[~target_day] = target_ideal[~target_day]
        return base_loss, phi # (8760, ), 标量


class Generator:
    """4 场景生成器：基础 GHI → 逐 case 校准链（白天分位→损失域事件插入→月能量校正）。

    校准链各步为本类私有方法、__call__ 顺序调用；基础损失由 BaseLoss 在外先行算好传入。
    """

    def __init__(self, config: SolarConfig):
        """保存校准链参数。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config

    def _calibrate_daylight(
        self, generated: np.ndarray, target_ideal: np.ndarray, reference: np.ndarray
    ) -> np.ndarray:
        """白天分位校准：月×时格子值向同类历史分位混合 → 夜间置 0、封顶理想。

        Args:
            generated (np.ndarray): 基础 GHI 场景 (8760, )，白天格就地更新。
            target_ideal (np.ndarray): 轴年理想辐照度 (8760, )。
            reference (np.ndarray): 同类资源年参考矩阵 (同类年数, 8760)。

        Returns:
            np.ndarray: 校准后 GHI (8760, )（非负且不超过理想）。
        """
        daylight = target_ideal > self.config.daylight_threshold # (8760, )
        for month, (ms, me) in enumerate(zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]), start=1):
            for hour in range(24):
                positions = np.arange(ms + hour, me, 24) # (月内该时次小时数, )
                positions = positions[daylight[positions]] # 只取白天
                current = generated[positions] # (白天时次数, )
                ref = reference[:, ms + hour : me : 24].ravel() # (同类年数*月内该时次小时数, )
                ref = ref[ref > 1.0] # 剔除夜间/近零值
                if len(current) < 4 or len(ref) < 20: # 样本不足跳过该格
                    continue
                generated[positions] = quantile_map(current, ref, self.config.quantile_blend)
        generated[~daylight] = 0.0
        return np.minimum(np.clip(generated, 0.0, None), target_ideal) # (8760, )

    def _apply_event(self, loss: np.ndarray, occupied: np.ndarray, pos: int, segment: np.ndarray) -> None:
        """写入损失段并做前后坡度过渡，更新占用掩码。

        Args:
            loss (np.ndarray): 损失场景 (8760, )，就地修改。
            occupied (np.ndarray): 已占用掩码 (8760, )，就地修改。
            pos (int): 写入起点位置。
            segment (np.ndarray): 历史损失段 (持续小时, )。

        Returns:
            None
        """
        duration = len(segment)
        # 1. 覆盖主事件段
        loss[pos : pos + duration] = segment
        # 2. 前后各 RAMP_HOURS 小时坡度过渡
        for step in range(1, RAMP_HOURS + 1):
            event_weight = (RAMP_HOURS + 1 - step) / (RAMP_HOURS + 1.0)
            before = pos - step
            if before >= 0:
                loss[before] = (1.0 - event_weight) * loss[before] + event_weight * segment[0]
            after = pos + duration - 1 + step
            if after < len(loss):
                loss[after] = event_weight * segment[-1] + (1.0 - event_weight) * loss[after]
        # 3. 占用区间置 True（防后续插入重叠）
        occupied[max(0, pos - RAMP_HOURS) : min(len(loss), pos + duration + RAMP_HOURS)] = True

    def _insert_events(
        self,
        loss: np.ndarray,
        target_ideal: np.ndarray,
        events: EventDetails,
        case: str,
        case_index: int,
    ) -> None:
        """损失域事件插入：月均次数伯努利取整、池中均匀挑事件、均匀随机选点，就地写入。

        每月次数 = 本 (年景, 月) 历史事件数 ÷ 年景年数；月池空时退回本年景全年池。
        合法起点须整段同月、全程核心日照、不与已插入事件重叠；选点为均匀随机
        （不打分，与风电不同）。

        Args:
            loss (np.ndarray): 损失场景 (8760, )，就地修改。
            target_ideal (np.ndarray): 轴年理想辐照度 (8760, )。
            events (EventDetails): 事件明细（含池子/年数/段现取方法）。
            case (str): 年景类型。
            case_index (int): 年景序号（随机种子偏移）。

        Returns:
            None
        """
        occupied = np.zeros(len(loss), dtype=bool) # (8760, ) 已占用掩码（防重叠）
        rng = np.random.default_rng(RANDOM_SEED + case_index + 100)
        core = target_ideal > self.config.event_ideal_threshold # (8760, ) 核心日照掩码
        n_case_years = events.n_case_years(case) # 该年景训练年数（年均次数分母）
        for month, (ms, me) in enumerate(zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]), start=1):
            # 1. 该 (年景, 月) 历史事件池 → 年均次数 → 伯努利取整定个数
            pool = events.pool(case, month) # (该格事件数, )
            mean_count = len(pool) / max(n_case_years, 1) # 如 2.3 = 年均 2.3 次
            count = int(mean_count) + int(rng.random() < mean_count % 1) # 必插 2 个，30% 概率再插 1 个，期望不变
            if len(pool) == 0: # 月池空 → 退回本年景全年池
                pool = events.pool(case)
            for _ in range(count):
                if len(pool) == 0: # 全年池也空（该年景无历史事件）→ 跳过
                    continue
                # 2. 池中均匀随机挑一个历史事件
                row = int(pool[int(rng.integers(0, len(pool)))]) # 明细行号
                segment = events.segment(row) # (持续小时, )
                duration = len(segment)
                # 3. 合法起点：整段同月 + 全程核心日照 + 未占用
                starts = np.arange(ms, me - duration + 1) # (可选起点数, )
                starts = starts[np.array([core[p : p + duration].all() for p in starts], dtype=bool)]
                starts = starts[
                    np.array(
                        [
                            not occupied[max(0, p - RAMP_HOURS) : min(len(loss), p + duration + RAMP_HOURS)].any()
                            for p in starts
                        ],
                        dtype=bool,
                    )
                ]
                if len(starts) == 0:
                    continue
                # 4. 均匀随机选点（无打分）→ 写入损失段 + 坡度 + 占用
                pos = int(rng.choice(starts))
                self._apply_event(loss, occupied, pos, segment)

    def _monthly_energy(self, generated: np.ndarray, target_ideal: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """月能量校正：各月白天累计辐照 → 同类历史年均（系数截 [0.70, 1.60]、封顶理想）→ 夜间置 0。

        Args:
            generated (np.ndarray): 待校正 GHI 场景 (8760, )，白天位就地更新。
            target_ideal (np.ndarray): 轴年理想辐照度 (8760, )。
            reference (np.ndarray): 同类资源年参考矩阵 (同类年数, 8760)。

        Returns:
            np.ndarray: 校正后 GHI (8760, )。
        """
        daylight = target_ideal > self.config.daylight_threshold # (8760, )
        for ms, me in zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]):
            positions = np.arange(ms, me)[daylight[ms:me]] # (月内白天小时数, )
            ref = reference[:, ms:me].ravel() # (同类年数*月小时数, )
            target_total = float(ref.sum() / reference.shape[0]) # 标量 同类历史月均累计
            current = float(generated[positions].sum()) # 标量 当前月累计
            factor = target_total / current if current > 1e-9 else 1.0
            factor = float(np.clip(factor, 0.70, 1.60))
            generated[positions] = np.minimum(generated[positions] * factor, target_ideal[positions])
        generated[~daylight] = 0.0
        return np.clip(generated, 0.0, None) # (8760, )

    def __call__(
        self,
        grid: np.ndarray,
        years: np.ndarray,
        labels: np.ndarray,
        events: EventDetails,
        base_loss: np.ndarray,
        target_ideal: np.ndarray,
    ) -> np.ndarray:
        """辐照度数据 + 年份/类型标签 + 事件明细 + 基础损失 → 三年景场景矩阵。

        Args:
            grid (np.ndarray): 辐照度数组 (N, 8760)。
            years (np.ndarray): 源年年份数组 (N, )。
            labels (np.ndarray): 各年资源年类型标签 (N, )。
            events (EventDetails): 事件明细（含池子/年数/段现取方法）。
            base_loss (np.ndarray): 基础损失 (8760, )（BaseLoss 输出，三 case 共用）。
            target_ideal (np.ndarray): 轴年理想辐照度 (8760, )。

        Returns:
            np.ndarray: 三年景场景 (3, 8760)，行序同 RESOURCE_YEAR_TYPES。
        """
        train_mask = np.isin(years, self.config.train_years) # (N, ) 训练年掩码（各 case 参考矩阵用）
        base_ghi = np.clip(target_ideal - base_loss, 0.0, None) # (8760, ) 基础 GHI，三 case 共用起点

        rows = []
        for i, case in enumerate(RESOURCE_YEAR_TYPES):
            # 步骤1: 同类资源年训练年 → 参考矩阵（该 case 校准的"历史老师"）
            mask = (labels == case) & train_mask
            if not mask.any():
                raise ValueError(f"没有{case}训练年")
            reference = grid[mask] # (同类年数, 8760)

            # 步骤2: 白天分位校准（月×时格子 → 参考分位混合；夜间置 0、封顶理想）
            generated = self._calibrate_daylight(base_ghi.copy(), target_ideal, reference)

            # 步骤3: 损失域插入低辐照事件（均匀随机选点、坡度过渡、防重叠）
            loss = np.clip(target_ideal - generated, 0.0, None) # (8760, )
            self._insert_events(loss, target_ideal, events, case, i)

            # 步骤4: 回 GHI 域 → 夜间置 0
            generated = np.clip(target_ideal - loss, 0.0, None) # (8760, )
            generated[target_ideal <= self.config.daylight_threshold] = 0.0

            # 步骤5: 月能量校正（各月白天累计 → 同类历史年均）→ 收集该 case 成品场景
            rows.append(self._monthly_energy(generated, target_ideal, reference)) # (8760, )
        return np.vstack(rows) # (3, 8760) 行序同 RESOURCE_YEAR_TYPES


class Physics:
    """5 物理出力：单单元场景辐照度 + 训练年温度气候态 + 组件配置 → 逐 case 出力 MW（轴年轴；区域汇总/重排在 run_solar）。"""

    def __init__(self, config: SolarConfig):
        """保存轴年时间轴、损耗系数与反照率。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.target_index = year_index(config.axis_year, drop_feb29=True) # (8760, ) 轴年日历
        self.loss_factor = config.loss_factor
        self.albedo = config.albedo

    def __call__(
        self,
        city_scenes: dict[str, np.ndarray],
        temp_clim: np.ndarray,
        unit: SolarUnit,
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        # 20261109修改↑
        """单单元各 case GHI + 温度气候态 + 组件配置 → 逐 case 出力 MW（轴年轴，含损耗）。

        Args:
            city_scenes (dict[str, np.ndarray]): case → GHI 场景 (8760, )。
            temp_clim (np.ndarray): 训练年温度气候态 (K) (8760, )。
            unit (SolarUnit): 装机与组件配置对象。

        Returns:
            tuple[dict[str, np.ndarray], dict[str, np.ndarray]]: (case → 出力 MW (8760, ),
            case → DNI (W/m2, 8760, )，与组件倾角/方位角无关)。
        """
        temp_c = temp_clim - 273.15 # (8760, ) 摄氏温度
        configured = sum(c.count * c.rated_power_mw for c in unit.components) # 标量 组件配置总装机

        power: dict[str, np.ndarray] = {}
        dni_by_case = {}  # 20261109新增
        for case, ghi in city_scenes.items():
            # 1. 逐组件出力叠加——POA 转置 + NOCT 组件温度 + 功率温度系数 → (8760, ) MW
            city_mw = np.zeros(len(ghi)) # (8760, )
            case_dni = None# 20261109新增
            for comp in unit.components:
                g, dni = self._poa(ghi, unit.latitude, unit.longitude, comp.tilt_deg, comp.azimuth_deg) # (8760, )# 20261109修改新增dni
                if case_dni is None: case_dni = dni.copy()# # 20261109修改新增dni DNI与组件倾角、方位角无关，每个地市每个年景保存一次
                tc = temp_c + (comp.noct_c - 20) / 800 * g # (8760, ) 组件温度 °C
                scale = unit.capacity_mw * comp.dc_ac_ratio / max(configured, 1e-9) # 标量 该组件容量等比缩放系数（交流份额×各自容配比÷全部组件额定合计）# 20261110改组件级容配比
                city_mw += (
                    comp.count * comp.rated_power_mw * scale * self.loss_factor
                    * comp.efficiency
                    * np.clip(g / 1000 * (1 + comp.gamma_p_per_c * (tc - 25)), 0, 1.2)
                )
            power[case] = np.minimum(np.maximum(city_mw, 0.0),unit.capacity_mw) # (8760, ) 单元 MW（含损耗）# 20261109修改，交流侧功率封顶
            dni_by_case[case] = case_dni# 20261109修改新增dni
        return power, dni_by_case# 20261109修改新增dni

    def _poa(
        self,
        ghi: np.ndarray,
        latitude: float,
        longitude: float,
        tilt_deg: float,
        azimuth_deg: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        # 20261109修改新增↑
        """水平面辐照度 → 组件倾斜面辐照度（POA）：直射 + 散射 + 地面反照。

        Args:
            ghi (np.ndarray): 水平面辐照度 (8760, )（W/m2）。
            latitude (float): 纬度（度）。
            longitude (float): 经度（度）。
            tilt_deg (float): 组件倾角（度）。
            azimuth_deg (float): 组件方位角（度）。

        Returns:
            tuple[np.ndarray, np.ndarray]: (倾斜面辐照度 (8760, )（W/m2，非负）,
            法面直接辐射 DNI (8760, )（W/m2）)。
        """
        n = self.target_index.dayofyear.to_numpy()
        h = self.target_index.hour.to_numpy() + self.target_index.minute.to_numpy() / 60
        dec = np.deg2rad(23.45 * np.sin(np.deg2rad(360 * (284 + n) / 365))) # 太阳赤纬
        ha = np.deg2rad(15 * (h - 12 + (120 - longitude) / 15)) # 时角（北京时间 → 当地太阳时）
        la = np.deg2rad(latitude)
        cz = np.clip(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha), 0, 1) # 太阳高度角余弦
        z = np.arccos(cz) # 天顶角
        beta = np.deg2rad(tilt_deg)
        az = np.deg2rad(azimuth_deg)
        # 1. 直射分量：GHI ÷ 高度角余弦（低角度截 0.02 防放大）
        safe = np.maximum(cz, 0.02)

        # 2. 散射分量：晴空指数经验式估计散射比例
        kd = np.where(ghi > 20, np.clip(1 - 1.13 * np.clip(ghi / (1367 * safe), 0, 1), 0.05, 1), 1)
        dhi = kd * ghi
        dni = np.divide(np.maximum(ghi - dhi, 0.0),safe,out=np.zeros_like(ghi),where=cz > 0)
        # 3. 入射角 + 直射/散射/反照三分量合成
        ci = np.cos(z) * np.cos(beta) + np.sin(z) * np.sin(beta) * np.cos(az - np.pi)
        poa = np.maximum(
            0,
            dni * np.maximum(ci, 0)
            + dhi * (1 + np.cos(beta)) / 2
            + self.albedo * ghi * (1 - np.cos(beta)) / 2,
        )# 20261109修改 新增dni输出
        return poa, dni# 20261109修改 新增dni输出





# ─────────────────────────────────────────────────────────────────────────────
# 模型与入口
# ─────────────────────────────────────────────────────────────────────────────

class SolarModel:
    """光伏模型：__init__ 组装四步子模块，forward 顺序执行、维度逐行流转（分类为内联薄步骤）。"""

    def __init__(self, config: SolarConfig):
        """组装平级子模块（事件库 EventDetails 在 forward 按数据构造）。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config
        self.align = Align(config.source_years)  # 1 数据对齐
        self.base = BaseLoss(config)        # 2 基础损失
        self.generate = Generator(config)   # 3 场景生成
        self.physics = Physics(config)      # 4 物理出力

    def forward(
        self,
        city: str,
        solar: pd.Series,
        temp: pd.Series,
        unit: SolarUnit,
    ) -> tuple:
        """单单元全流程（joblib 工作函数，可 pickle）：对齐→分类→理想辐照度→事件库→基础损失→生成→物理出力（轴年）。

        Args:
            city (str): 单元键。
            solar (pd.Series): UTC 口径辐照度历史序列。
            temp (pd.Series): UTC 口径训练年温度序列 (K)。
            unit (SolarUnit): 装机与组件配置对象（含经纬度）。

        Returns:
            tuple: (city, labels, case→场景Series(轴年), case→MW(轴年), 事件数, φ)。
        """
        worker_logging()

        #步骤1 Series(UTC, 全量年) → (对齐序列, (N, 8760), 年标签)
        aligned, grid, years = self.align(solar)

        #步骤2 (N, 8760) → (N, ) 资源年类型标签（逐年累计辐照量，训练年 P25/P75 分三类，最后一年也参与归类）
        metric = grid.sum(axis=1) / 1000.0 # (N, ) 年累计辐照量 kWh/m2
        train_mask = np.isin(years, self.config.train_years) # (N, )
        p25, p75 = np.quantile(metric[train_mask], [0.25, 0.75]) # 标量, 标量
        labels = np.where(metric <= p25, "偏小资源年", np.where(metric >= p75, "偏大资源年", "正常资源年")) # (N, )

        #步骤3 理想辐照度：历史轴（逐行装箱）+ 轴年（事件库/基础损失/生成/POA 共用）
        ideal = ideal_irradiance(aligned.index, unit.latitude, unit.longitude).reshape(len(years), 8760) # (N, 8760)
        target_index = self.physics.target_index # (8760, )
        target_ideal = ideal_irradiance(target_index, unit.latitude, unit.longitude) # (8760, )

        #步骤4 训练年 (N, 8760) → (E, 4) 低辐照事件明细 EventDetails（损失段长表，按需现取）
        events = EventDetails(self.config, grid[train_mask], ideal[train_mask], years[train_mask], labels[train_mask])

        #步骤5 训练年 (N, 8760) → (8760, ) 基础损失 + 标量 φ（三 case 共用）
        train_history = aligned.loc[aligned.index.year.isin(self.config.train_years)]
        base_loss, phi = self.base(train_history, ideal[train_mask], target_index, target_ideal)

        #步骤6 全量源年辐照 (N, 8760) + 年景标签 (N, ) + 事件明细 + 基础损失 + 轴年理想 → 场景 (3, 8760) → 装箱 {case: Series(轴年)}
        scenes = self.generate(grid, years, labels, events, base_loss, target_ideal)
        scenarios = {
            case: pd.Series(row, index=target_index, name=case)
            for case, row in zip(RESOURCE_YEAR_TYPES, scenes)
        } # {case: Series(轴年, 8760)}

        #步骤7 温度气候态：训练年 UTC 温度 → +8h 删闰日 → 8760 小时多年均值 → 轴年物理出力（重排输出年在 run_solar）
        temp_clim = weather_climatology(temp, self.config.train_years).to_numpy(float) # (8760, )
        power, dni = self.physics(dict(zip(RESOURCE_YEAR_TYPES, scenes)), temp_clim, unit)# 20261109新增全省DNI
        return city, labels, scenarios, power, dni, len(events.details), phi# 20261109新增全省DNI

    __call__ = forward


def run_solar(
    config: SolarConfig,
    solar_by_city: dict[str, pd.Series],
    temp_by_city: dict[str, pd.Series],
    city_configs: dict[str, SolarUnit],
) -> SolarResult:
    """光伏出力模型入口：进程池逐单元并行全流程（轴年物理出力），主进程区域汇总后重排输出年。

    Args:
        config (SolarConfig): 光伏算法配置。
        solar_by_city (dict[str, pd.Series]): 单元键 → UTC 口径辐照度历史序列（已按 city_configs 过滤）。
        temp_by_city (dict[str, pd.Series]): 单元键 → UTC 口径训练年温度序列 (K)。
        city_configs (dict[str, SolarUnit]): 单元键 → 装机与组件配置对象（含经纬度）。

    Returns:
        SolarResult: 三类场景、出力结果对象（输出年轴）、资源年分类标签。
    """
    model = SolarModel(config)
    year = config.year

    total = len(solar_by_city)
    logger.info("[2-4/4] %d 个地市: 资源年分类→低辐照事件库→GPR场景生成→物理出力（多进程并行）...", total)
    results = Parallel(n_jobs=-1)(
        delayed(model.forward)(city, series, temp_by_city[city], city_configs[city])
        for city, series in solar_by_city.items()
    )

    # 轴年装箱（单元顺序 = 提交顺序，区域累加顺序冻结）
    scenarios_axis: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, np.ndarray] = {}
    power_by_city: dict[str, dict[str, np.ndarray]] = {}
    
    # 20261109新增全省DNI
    dni_by_city = {}
    for i, (city, labels, city_scenarios, city_power, city_dni, n_events, phi) in enumerate(results, 1):
        logger.info("[2-4/4] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)", i, total, city, n_events, phi)
        classes_all[city] = labels
        power_by_city[city] = city_power
        dni_by_city[city] = city_dni # 20261109 新增全省DNI
        for case, scenario in city_scenarios.items():
            scenarios_axis.setdefault(case, {})[city] = scenario

    # 区域汇总（轴年口径）：单元出力累加 → MW / 装机占比
    logger.info("[3/4] 区域汇总: 轴年 %d, %d 个地市...", config.axis_year, total)
    axis_index = next(iter(next(iter(scenarios_axis.values())).values())).index # 轴年 8760 小时
    total_capacity = sum(unit.capacity_mw for unit in city_configs.values()) # 标量 区域总装机
    power_axis: dict[str, PowerResult] = {}
    for case, city_scenarios in scenarios_axis.items():
        region_total = np.zeros(len(axis_index)) # (8760, ) 区域出力累加器
        city_power: dict[str, np.ndarray] = {}
        city_dni = {}
        for city in city_scenarios:
            city_power[city] = power_by_city[city][case]
            region_total += city_power[city] # 单元求和 → 区域 MW
            city_dni[city] = dni_by_city[city][case]

        values = np.stack(list(city_dni.values()), axis=0) # 20261109新增全省DNI：各地市逐小时算术平均
        region_dni = np.mean(values, axis=0) # 20261109新增全省DNI：各地市逐小时算术平均
        power_axis[case] = PowerResult(
            index=axis_index,
            city_power=city_power,
            total_power_mw=region_total,
            total_power_pu=region_total / total_capacity,
            city_dni=city_dni,
            total_dni=region_dni,
        ) # 20261109新增DNI

    # 重排输出年（闰年补 2 月 29 日）：场景与出力同为输出年轴
    logger.info("[4/4] 重排输出年 %d...", year)
    scenarios = {
        case: {city: remap_to_year(scenario, year) for city, scenario in cities.items()}
        for case, cities in scenarios_axis.items()
    }
    power = {
        case: PowerResult(
            index=year_index(year),
            city_power={
                city: remap_to_year(pd.Series(values, index=r.index), year).to_numpy(float)
                for city, values in r.city_power.items()
            },
            total_power_mw=remap_to_year(pd.Series(r.total_power_mw, index=r.index), year).to_numpy(float),
            total_power_pu=remap_to_year(pd.Series(r.total_power_pu, index=r.index), year).to_numpy(float),
            city_dni={city: remap_to_year(pd.Series(values, index=r.index), year).to_numpy(float)for city, values in r.city_dni.items()},
            total_dni=remap_to_year(pd.Series(r.total_dni, index=r.index), year).to_numpy(float),
        ) # 20261109 新增 DNI
        for case, r in power_axis.items()
    }

    logger.info("[4/4] 完成: %s", {case: f"{len(r.index)}小时×{1 + len(r.city_power)}列" for case, r in power.items()})
    return SolarResult(scenarios=scenarios, power=power, classes=classes_all)
