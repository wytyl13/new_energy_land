#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29 16:05
@Author  : weiyutao
@File    : wind.py

风电模型（torch 式模块化）：WindModel 在 __init__ 组装子模块，
forward 顺序执行、维度逐行流转；每个子模块以 __init__(config) + __call__(data)
为公开两方法，__call__ 即模块 forward 且置于类的最后——以"步骤N"注释分块、行尾标注维度，
逻辑直接写在 __call__ 中（Generator 的校准链各步为同类私有方法）；
步骤2 事件库为数据类（(E, 5) 明细长表 + 按需现取方法，风速值不复制）；
跨子模块共用的查表工具（MONTH_STARTS/month_of/装箱 year_grid）在 utils/time_axis（风电/光伏共用）。

时间轴约定：Align 步骤1~4 之后每年恒为删 2/29 的 8760 小时整点轴，
序列装箱为 (年数, 8760) ndarray，行=年、列=年内小时位置；月/时由位置查表得出，
pandas 只留在门口（Align）与出口（remap_to_year、结果封装）。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy.stats import norm, rankdata
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.preprocessing import StandardScaler

from .align import Align
from .config import WindConfig, WindUnit
from .constants import (
    GAMMA,
    GPR_CHUNK_SIZE,
    GRAVITY,
    PHI_MAX,
    RAMP_HOURS,
    R_D,
    RHO_REF,
    RANDOM_SEED,
    RESOURCE_YEAR_TYPES,
)
from .evaluate import evaluate_city
from .result import PowerResult, WindResult
from .utils.common import quantile_map, worker_logging
from .utils.time_axis import MONTH_STARTS, month_of, remap_to_year, year_index

logger = logging.getLogger(__name__)

EPS_STD = 1e-8           # 均值/标准差除零保护阈值
EDGE_PENALTY = 0.08      # 事件插入点边界跳变惩罚系数
CANDIDATE_STARTS = 80    # 每个事件抽样的候选起点上限


# ─────────────────────────────────────────────────────────────────────────────
# 模型子模块
# ─────────────────────────────────────────────────────────────────────────────

class EventDetails:
    """2. 极端事件库：训练年风速 → 事件明细 (E, 5)，一行一个事件（长表，风速值不复制）。

    列 = [year, type, start, dur, month]：来源年份、事件类型（0=低风 / 1=大风）、
    段起始小时、持续小时（≥3）、起始小时所在月（1~12）。行序 = 扫描顺序
    （逐年 → 先低风后大风 → 段先后），行号+1 即原全局事件编号。
    派生数据（年景/池子/段数值）不落列——pool / n_case_years / segment 现取。

    示例：
        [[2001, 0, 1035, 6, 2],  # 2001 年（偏小资源年）2 月低风，第 1035 小时起持续 6h
         [2007, 1, 950, 9, 2]]   # 2007 年 2 月大风，持续 9h
    """

    def __init__(self, train_grid: np.ndarray, train_years: np.ndarray, train_labels: np.ndarray):
        """扫描训练年风速产出明细矩阵，保存现取所需的三份输入与各事件年景。

        Args:
            train_grid (np.ndarray): 训练年风速 (年数, 8760)，行序与 train_years 一致（升序）。
            train_years (np.ndarray): 训练年年份数组 (年数, )，升序。
            train_labels (np.ndarray): 训练年资源年类型标签 (年数, )。
        """
        # 1. 全历史 P10/P90 阈值
        threshold_low, threshold_high = np.quantile(train_grid.ravel(), [0.10, 0.90])

        # 2. 逐年扫描低风/大风连续段（≥3h）→ 明细行
        rows = []
        for row, year in zip(train_grid, train_years): # row: (8760, )
            for type_idx, mask in ((0, row <= threshold_low), (1, row >= threshold_high)): # mask: (8760, )
                edges = np.diff(np.r_[False, mask, False].astype(int)) # True 段首 +1 / 段尾 -1
                for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                    if end - start < 3: # 持续不足 3h 的段不算事件
                        continue
                    rows.append((int(year), type_idx, int(start), int(end - start), int(month_of(start)) + 1))
        self.details = np.array(rows, dtype=int).reshape(-1, 5) # (E, 5) int；无事件 (0, 5)

        # 3. 保存现取所需输入 + 各事件来源年的年景（一次算好）
        self.grid, self.years, self.labels = train_grid, train_years, train_labels
        self.cases = train_labels[np.searchsorted(train_years, self.details[:, 0])] # (E, )

    def pool(self, case: str, month: int, type_idx: int) -> np.ndarray:
        """该 (年景, 月, 类型) 格子的历史事件行号。

        Args:
            case (str): 年景类型。
            month (int): 月份 1~12。
            type_idx (int): 事件类型（0=低风 / 1=大风）。

        Returns:
            np.ndarray: 明细行号 (该格事件数, )，升序 = 扫描顺序。
        """
        return np.flatnonzero(
            (self.cases == case) & (self.details[:, 4] == month) & (self.details[:, 1] == type_idx)
        )

    def n_case_years(self, case: str) -> int:
        """该年景训练年数（年均次数分母）。

        Args:
            case (str): 年景类型。

        Returns:
            int: 训练年数。
        """
        return int((self.labels == case).sum())

    def segment(self, row: int) -> np.ndarray:
        """第 row 个事件的段风速（train_grid 切片，不复制存储）。

        Args:
            row (int): 明细行号。

        Returns:
            np.ndarray: 段风速 (持续小时, )。
        """
        year, _, start, duration, _ = self.details[row]
        return self.grid[int(np.searchsorted(self.years, year)), start : start + duration]


class BaseScene:
    """4 GPR 基础场景：拟合典型年形态 + AR(1) 残差经验分位采样（三 case 共用）。"""

    def __init__(self, config: WindConfig):
        """保存 GPR 抽样规模。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.sample_size = config.gpr_sample_size

    def __call__(self, train_grid: np.ndarray) -> tuple[np.ndarray, float]:
        """训练年风速数据 (N, 8760) → (8760 小时基础场景, 残差 lag1 自相关系数 φ)。

        Args:
            train_grid (np.ndarray): 训练年 (年数, 8760) 风速数组。

        Returns:
            tuple[np.ndarray, float]: (8760 小时基础场景, φ)。
        """
        rng = np.random.default_rng(RANDOM_SEED)

        # 步骤1: (N, 8760) 铺平 → (N*8760, ) -> (N*8760, 7) 使用周期函数对构建时间（年小时，月，天小时）位置特征矩阵
        y_all = train_grid.ravel() # (N*8760, )
        position = np.arange(len(y_all)) # (N*8760, ): [0, 1, 2, ..., N*8760-1]
        hour_of_year = (position % 8760).astype(float) # (N*8760, ): [0, 1, 2, ..., 8759, 0, 1, 2, ..., 8759, ...]
        hour_of_day = (position % 24).astype(float) # (N*8760, ): [0, 1, 2, ..., 23, 0, 1, 2, ..., 23, ...]
        month = np.searchsorted(MONTH_STARTS, position % 8760, side="right").astype(float) # (N*8760, ): [1, 1, 1, ..., 2, ..., ..., 12]
        x_all = np.column_stack(
            [
                np.sin(2 * np.pi * hour_of_year / 8760.0), # sin配合cos提取一年的四季特征（sin在单位圆的上升段和下降段数值一样，所以配合cos可以做到特征唯一）
                np.cos(2 * np.pi * hour_of_year / 8760.0),
                np.sin(2 * np.pi * hour_of_day / 24.0), # 提取每天不同时段的特征
                np.cos(2 * np.pi * hour_of_day / 24.0),
                np.sin(2 * np.pi * month / 12.0), # 提取每月特征
                np.cos(2 * np.pi * month / 12.0),
                hour_of_year / 8760.0,
            ]
        ) # (N*8760, 7)，注意这7个特征都是时间特征（年，月，天）

        # 步骤2: 随机抽 sample_size 点训练 GPR（特征标准化 + 固定核，不做超参优化）
        chosen = np.sort(rng.choice(len(y_all), size=min(self.sample_size, len(y_all)), replace=False)) # (sample_size, ) 随机不返回抽取，返回索引号
        scaler = StandardScaler().fit(x_all[chosen]) # 统计 (sample_size, 7) 的均值和标准差 (2, 7)
        kernel = (
            ConstantKernel(1.0, constant_value_bounds="fixed")
            * RBF(length_scale=[1.2, 1.2, 0.9, 0.9, 1.3, 1.3, 1.5], length_scale_bounds="fixed")
            + WhiteKernel(noise_level=0.15, noise_level_bounds="fixed")
        ) # 核函数，输入两个7维特征，暑促会一个相似度数值
        model = GaussianProcessRegressor(
            kernel=kernel, optimizer=None, normalize_y=True, alpha=1e-6, random_state=RANDOM_SEED
        ) # 构建GPR对象
        model.fit(scaler.transform(x_all[chosen]), y_all[chosen]) # 训练，训练前对输入特征进行归一化处理

        # 步骤3: 全量逐点预测 → 残差（分块预测防核矩阵过大）
        x_std = scaler.transform(x_all) # 输入特征全量归一化处理 (N*8760, 7) -> (N*8760, 7)
        pred_hist = np.concatenate(
            [model.predict(x_std[i : i + GPR_CHUNK_SIZE]) for i in range(0, len(x_std), GPR_CHUNK_SIZE)]
        ) # (N*8760, ) 分块预测然后再拼接回去
        residual = y_all - pred_hist # (N*8760, ) 残差

        # 步骤4: 轴年预测（特征周期 8760：轴年特征 = 首年特征切片（因为x_all中每年的特征一样））
        x_target = scaler.transform(x_all[:8760]) # (8760, 7)
        pred_target = np.concatenate(
            [model.predict(x_target[i : i + GPR_CHUNK_SIZE]) for i in range(0, len(x_target), GPR_CHUNK_SIZE)]
        ) # (8760, )

        # 步骤5: 残差 lag1 自相关 → φ 标量（本小时 vs 下小时两列相关系数，截到 [0, PHI_MAX]）
        phi = float(np.clip(np.corrcoef(residual[:-1], residual[1:])[0, 1], 0.0, PHI_MAX))

        # 步骤6: AR(1) 一阶递推 → (8760, ) 隐变量
        innovations = rng.normal(size=8760) # (8760, ) 白噪声（标准正态分布，各自独立）
        latent = np.zeros(8760, dtype=float)
        latent[0] = innovations[0]
        innovation_scale = np.sqrt(max(1.0 - phi**2, 1e-4)) # Math: \sqrt{1-\phi^2}
        for i in range(1, len(latent)):
            latent[i] = phi * latent[i - 1] + innovation_scale * innovations[i] # Math: x_t = ϕ·x_{t-1} + √(1-φ²)·ε_t，ε_t 即 innovations[i]

        # 步骤7: 隐变量正态分位 → 残差经验分位取样
        ranks = rankdata(residual, method="average")
        residual_sorted = np.sort(residual)
        residual_probability = (ranks - 0.5) / len(ranks)
        quantile_probability = np.clip(norm.cdf(latent), residual_probability.min(), residual_probability.max())
        residual_sample = np.quantile(residual_sorted, quantile_probability) # (8760, )

        # 步骤8: 形态 + 残差采样
        scene = pred_target + residual_sample # (8760, )

        # 步骤9: 截负为 0 → 基础场景
        return np.clip(scene, 0.0, None), phi # (8760, ), 标量


class Generator:
    """5 场景生成器：基础场景 → 逐 case 校准链（水平校准→软分位→事件插入→全局映射→均值回拉）。

    校准链各步为本类私有方法、__call__ 顺序调用；基础场景由 BaseScene 在外先行算好传入。
    """

    def __init__(self, config: WindConfig):
        """保存校准链参数。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.config = config

    @property
    def target_index(self) -> pd.DatetimeIndex:
        """轴年日历删 2 月 29 日的场景时间轴 (8760, )，访问时现生成。"""
        return year_index(self.config.axis_year, drop_feb29=True)

    def _calibrate_levels(self, scene: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """逐月水平校准：月内均值/标准差仿射对齐参考矩阵，返回截负后的新数组。

        基础场景的相对起伏可以参考，但是绝对水平不行。所以要把基础场景的均值和标准差
        仿射对齐到历史数据。对齐的时候要区分两种场景：①没有相对起伏（恒为常数），
        直接使用历史的均值去赋值即可；②有相对起伏，标准化仿射操作。

        Args:
            scene (np.ndarray): 基础场景 (8760, )。
            reference (np.ndarray): 同类资源年参考矩阵 (同类年数, 8760)。

        Returns:
            np.ndarray: 校准后场景 (8760, )，已截负为 0。
        """
        calibrated = scene.copy() # 每个 case 从共用基础场景重新拷贝 (8760, )

        # 1. 逐月仿射对齐（零方差月份直接填参考均值）
        for ms, me in zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]):
            ref = reference[:, ms:me].ravel() # (同类年数*月小时数, )
            current = calibrated[ms:me] # (月小时数, )
            current_std = float(np.std(current))
            if current_std > EPS_STD:
                # 如果该月有波动，正常校准（也就是先对基础场景进行归一化然后再加回历史数据的标准差和方差）
                # Math: x_i' = (x_i - \mu_c)*\frac{\sigma_r}{\sigma_c} + \mu_r
                calibrated[ms:me] = (current - current.mean()) * (float(np.std(ref)) / current_std) + float(ref.mean())
            else:
                # 如果该月没有波动，说明基础场景没有起伏（恒为常数），直接使用历史数据的均值赋值即可。
                calibrated[ms:me] = float(ref.mean()) # Math: x_i' = \mu_r
        # 2. 截负
        return np.clip(calibrated, 0.0, None) # (8760, )

    def _score_candidate(self, generated: np.ndarray, event_values: np.ndarray, pos: int, event_type: str) -> float:
        """候选插入点打分：低风挑低段、大风挑高段，边界跳变惩罚（越小越好）。

        Args:
            generated (np.ndarray): 当前场景 (8760, )。
            event_values (np.ndarray): 待插入事件风速段 (持续小时, )。
            pos (int): 候选起点位置。
            event_type (str): 事件类型（"低风"/"大风"）。

        Returns:
            float: 候选点得分（越小越优）。
        """
        duration = len(event_values)
        baseline = generated[pos : pos + duration]
        # 1. 基础分：低风取段内均值、大风取负的段内均值
        score = float(np.mean(baseline)) if event_type == "低风" else -float(np.mean(baseline))
        # 2. 边界跳变惩罚：事件段两端与场景的衔接差
        before = abs(float(generated[pos - 1] - event_values[0])) if pos else 0.0
        after_pos = pos + duration
        after = abs(float(generated[after_pos] - event_values[-1])) if after_pos < len(generated) else 0.0
        return score + EDGE_PENALTY * (before + after)

    def _apply_event(self, generated: np.ndarray, occupied: np.ndarray, pos: int, event_values: np.ndarray) -> None:
        """写入事件段（截到序列末尾）并做前后坡度过渡，更新占用掩码。

        Args:
            generated (np.ndarray): 当前场景 (8760, )，就地修改。
            occupied (np.ndarray): 已占用掩码 (8760, )，就地修改。
            pos (int): 写入起点位置。
            event_values (np.ndarray): 事件风速段 (持续小时, )。

        Returns:
            None
        """
        duration = len(event_values)
        # 1. 覆盖主事件段
        stop = min(pos + duration, len(generated))
        values_seg = event_values[: stop - pos]
        generated[pos:stop] = values_seg
        # 2. 前后各 RAMP_HOURS 小时坡度过渡
        for step in range(1, RAMP_HOURS + 1):
            event_weight = (RAMP_HOURS + 1 - step) / (RAMP_HOURS + 1.0)
            ramp_pos = pos - step
            if ramp_pos >= 0:
                generated[ramp_pos] = (1.0 - event_weight) * generated[ramp_pos] + event_weight * values_seg[0]
            ramp_pos = stop - 1 + step
            if ramp_pos < len(generated):
                generated[ramp_pos] = event_weight * values_seg[-1] + (1.0 - event_weight) * generated[ramp_pos]
        # 3. 占用区间置 True（防后续插入重叠）
        occupied[max(0, pos - RAMP_HOURS) : min(len(generated), pos + duration + RAMP_HOURS)] = True

    def _pick_position(
        self,
        generated: np.ndarray,
        occupied: np.ndarray,
        starts: np.ndarray,
        event_values: np.ndarray,
        event_type: str,
        rng: np.random.Generator,
    ) -> int | None:
        """选插入起点：候选抽样逐点打分取最优（占用区间跳过），无可用点返回 None。

        Args:
            generated (np.ndarray): 当前场景 (8760, )。
            occupied (np.ndarray): 已占用掩码 (8760, )。
            starts (np.ndarray): 当月全部合法起点 (可选起点数, )。
            event_values (np.ndarray): 待插入事件风速段 (持续小时, )。
            event_type (str): 事件类型（"低风"/"大风"）。
            rng (np.random.Generator): 本 case 随机数发生器。

        Returns:
            int | None: 最优起点；事件比整月长放不进、或候选全被占用时 None。
        """
        if len(starts) == 0:
            return None
        # 1. 候选抽样（至多 CANDIDATE_STARTS 个，不重复）
        candidate_starts = rng.choice(starts, size=min(CANDIDATE_STARTS, len(starts)), replace=False)
        # 2. 逐点打分取最优（inf 初始化保证首个有效候选必入选）
        best_score, best_pos = np.inf, None
        for pos in candidate_starts:
            pos = int(pos)
            left, right = max(0, pos - RAMP_HOURS), min(len(generated), pos + len(event_values) + RAMP_HOURS)
            if occupied[left:right].any(): # 与已插入事件（含过渡带）重叠 → 跳过该候选
                continue
            score = self._score_candidate(generated, event_values, pos, event_type)
            if score < best_score:
                best_score, best_pos = score, pos
        return best_pos

    def _insert_events(
        self,
        generated: np.ndarray,
        case: str,
        case_index: int,
        events: EventDetails,
    ) -> list[dict]:
        """事件插入：按年均次数把历史事件段就地写入场景（打分选点、坡度过渡、防重叠），返回方案行。

        每个 case 每月低风/大风两类都插（频率与形态取自本类历史年）。事件库现取（EventDetails）：
        池子=布尔筛行、年均次数=行数÷年数、段=train_grid 切片，不预构建中间结构。

        Args:
            generated (np.ndarray): 软分位校准后场景 (8760, )，就地修改。
            case (str): 年景类型。
            case_index (int): 年景序号（随机种子偏移）。
            events (EventDetails): 事件明细（含池子/年数/段现取方法）。

        Returns:
            list[dict]: 插入方案行列表（键同输出表 8 列）。
        """
        occupied = np.zeros(len(generated), dtype=bool) # (8760, ) 已占用掩码（防重叠）
        insert_rows = []
        rng = np.random.default_rng(RANDOM_SEED + 100 + case_index)
        n_case_years = events.n_case_years(case) # 该年景训练年数（年均次数分母）
        for month, (ms, me) in enumerate(zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]), start=1):
            for event_type, type_idx in (("低风", 0), ("大风", 1)):
                # 1. 该 (年景, 月, 类型) 历史事件池 → 年均次数 → 伯努利取整定个数
                #（pool 空时 count 恒 0、循环空转，无需单独守卫）
                pool = events.pool(case, month, type_idx) # (该格事件数, )
                mean_count = len(pool) / max(n_case_years, 1) # 如 2.3 = 年均 2.3 次
                count = int(mean_count) + int(rng.random() < mean_count % 1) # 必插 2 个，30% 概率再插 1 个，期望不变
                for _ in range(count):
                    # 2. 池中随机挑一个历史事件 → 整段落在当月内的全部起点
                    row = int(pool[int(rng.integers(0, len(pool)))]) # 明细行号
                    year, _, _, duration, _ = events.details[row]
                    event_values = events.segment(row) # (持续小时, )
                    starts = np.arange(ms, me - duration + 1) # (可选起点数, )
                    # 3. 选点（候选抽样打分取最优；无可用点跳过该事件）
                    pos = self._pick_position(generated, occupied, starts, event_values, event_type, rng)
                    if pos is None:
                        continue
                    # 4. 写入事件 + 记录方案行
                    self._apply_event(generated, occupied, pos, event_values)
                    insert_rows.append(
                        {
                            "事件编号": f"SCENE_{case}_{len(insert_rows) + 1:04d}",
                            "年景类型": case,
                            "事件类型": event_type,
                            "月份": month,
                            "开始时间": self.target_index[pos],
                            "持续小时": int(duration),
                            "来源年份": int(year),
                            "来源事件编号": f"WIND_{event_type}_{row + 1:05d}",
                        }
                    )
        return insert_rows

    def __call__(
        self,
        grid: np.ndarray,
        years: np.ndarray,
        labels: np.ndarray,
        events: EventDetails,
        base: np.ndarray,
    ) -> tuple[np.ndarray, list[list[dict]]]:
        """风速数据 + 年份/类型标签 + 事件明细 + 基础场景 → (三年景场景矩阵, 逐 case 插入方案行)。

        Args:
            grid (np.ndarray): 风速数组 (N, 8760)。
            years (np.ndarray): 源年年份数组 (N, )。
            labels (np.ndarray): 各年资源年类型标签 (N, )。
            events (EventDetails): 事件明细（含池子/年数/段现取方法）。
            base (np.ndarray): 基础场景 (8760, )（BaseScene 输出，三 case 共用）。

        Returns:
            tuple[np.ndarray, list[list[dict]]]:
                (三年景场景 (3, 8760) 行序同 RESOURCE_YEAR_TYPES, 逐 case 插入方案行列表（键同输出表 8 列）)。
        """
        train_mask = np.isin(years, self.config.train_years) # (N, ) 训练年掩码（各 case 参考矩阵用）

        rows = []
        plans = []
        for i, case in enumerate(RESOURCE_YEAR_TYPES):
            # 步骤1: 同类资源年训练年 → 参考矩阵（该 case 校准与事件频率的"历史老师"）
            mask = (labels == case) & train_mask
            if not mask.any():
                raise ValueError(f"没有{case}训练年")
            reference = grid[mask] # (同类年数, 8760)

            # 步骤2: 逐月水平校准（月内均值/标准差仿射对齐参考，返回新数组）
            generated = self._calibrate_levels(base, reference)

            # 步骤3: 逐月软分位校准（就地更新）——仿射只对齐了均值/标准差两个数，分布形状
            # 仍是生成器的（近正态）；把月内排序值映射到参考分位数修形状 → 截负
            for ms, me in zip(MONTH_STARTS[:-1], MONTH_STARTS[1:]):
                generated[ms:me] = quantile_map(
                    generated[ms:me], reference[:, ms:me].ravel(), self.config.soft_quantile_strength
                )
            generated = np.clip(generated, 0.0, None) # (8760, )

            # 步骤4: 事件插入（历史低风/大风段就地写入，每月两类都插、频率取本类历史）
            plans.append(self._insert_events(generated, case, i, events))

            # 步骤5: 全年经验分位映射——插入极端事件破坏了分布，对全年分布做一次软校正
            #（边缘分布往参考拉回一半，保住刚插入的极端事件；保留场景时间排序）→ 截负
            generated = np.clip(
                quantile_map(generated, reference.ravel(), self.config.global_mapping_strength), 0.0, None
            ) # (8760, )

            # 步骤6: 均值回拉（事件插入可能抬高整体均值，向同类历史均值回拉避免资源水平漂移）
            g_mean = float(generated.mean())
            if g_mean > EPS_STD:
                correction = 1.0 + self.config.mean_pullback * (float(reference.mean()) / g_mean - 1.0)
                generated = np.clip(generated * correction, 0.0, None) # (8760, )

            # 步骤7: 收集该 case 成品场景
            rows.append(generated) # 逐 case 收 (8760, ) → 共 3 条
        return np.vstack(rows), plans # (3, 8760) 行序同 RESOURCE_YEAR_TYPES, [list[dict] × 3]


class Physics:
    """4 物理出力：单单元场景风速 + 输出年温压 → 逐 case 出力 MW（区域汇总在 run_wind）。"""

    def __init__(self, config: WindConfig):
        """保存损耗系数。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.loss_factor = config.loss_factor

    def __call__(
        self,
        city_scenarios: dict[str, pd.Series],
        temp: pd.Series,
        pressure: pd.Series,
        unit: WindUnit,
    ) -> dict[str, np.ndarray]:
        """单单元各 case 场景 + 输出年温压 → 逐 case 出力 MW（含损耗）。

        Args:
            city_scenarios (dict[str, pd.Series]): case → 输出年轴场景。
            temp (pd.Series): 输出年温度序列 (K)。
            pressure (pd.Series): 输出年气压序列 (Pa)。
            unit (WindUnit): 装机配置对象。

        Returns:
            dict[str, np.ndarray]: case → 出力 MW (轴长, )。
        """
        temp_k = temp.to_numpy(float) # (轴长, ) 温度 K
        pressure_pa = pressure.to_numpy(float) # (轴长, ) 气压 Pa
        configured = sum(t.count * t.rated_power_mw for t in unit.turbines) # 标量 机型配置总装机
        scale = unit.capacity_mw / max(configured, 1e-9) # 标量 容量等比缩放系数

        power: dict[str, np.ndarray] = {}
        for case, scenario in city_scenarios.items():
            # 1. 逐机型出力比例叠加——轮毂切变 + 空气密度修正 + 分段立方曲线 → (轴长, ) MW
            wind = scenario.to_numpy(float) # (轴长, ) 风速
            city_mw = np.zeros(len(wind)) # (轴长, )
            for turbine in unit.turbines:
                hub = turbine.hub_height_m
                vin = turbine.cut_in_m_s
                vr = turbine.rated_m_s
                vout = turbine.cut_out_m_s
                alpha = turbine.wind_shear_exponent
                vh = wind * (hub / 100.0) ** alpha # (轴长, ) 轮毂风速
                th = temp_k - GAMMA * (hub - 2.0) # (轴长, ) 轮毂温度 K
                ph = pressure_pa * np.exp(-GRAVITY * hub / (R_D * th)) # (轴长, ) 轮毂气压 Pa
                rho_factor = (ph / (R_D * th)) / RHO_REF # (轴长, ) 密度修正系数
                frac = np.zeros_like(vh)
                ramp = (vh >= vin) & (vh < vr) # 爬坡段掩码
                rated = (vh >= vr) & (vh <= vout) # 额定段掩码
                frac[ramp] = np.clip(rho_factor[ramp] * (vh[ramp] ** 3 - vin**3) / (vr**3 - vin**3), 0, 1)
                frac[rated] = 1.0
                fraction = frac * turbine.efficiency
                city_mw += turbine.count * turbine.rated_power_mw * scale * fraction
            power[case] = city_mw * self.loss_factor # (轴长, ) 单元 MW（含损耗）
        return power


class Evaluate:
    """5. 可选评估：训练年历史 vs 生成场景的统计指标（evaluate 关闭时返回 None）。"""

    def __init__(self, config: WindConfig):
        """保存评估开关与训练年。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.enabled = config.evaluate
        self.train_years = config.train_years

    def __call__(
        self,
        city: str,
        aligned: pd.Series,
        years: np.ndarray,
        labels: np.ndarray,
        train_mask: np.ndarray,
        plans: list[list[dict]],
        scenarios: dict[str, pd.Series],
    ) -> pd.DataFrame | None:
        """单单元评估：训练年历史 + 分类/方案/场景 → 指标表（关闭时 None）。

        Args:
            city (str): 单元键。
            aligned (pd.Series): 对齐序列（北京时, 全量年）。
            years (np.ndarray): 源年年份数组 (N, )。
            labels (np.ndarray): 各年资源年类型标签 (N, )。
            train_mask (np.ndarray): 训练年掩码 (N, )。
            plans (list[list[dict]]): 逐 case 插入方案行列表（键同输出表 8 列）。
            scenarios (dict[str, pd.Series]): case → 8760 小时场景。

        Returns:
            pd.DataFrame | None: 评估指标表；evaluate 关闭时 None。
        """
        if not self.enabled:
            return None
        # 1. 训练年历史切片 + 逐 case 方案行现转表（要用 pandas 的只有这里）
        train_history = aligned.loc[aligned.index.year.isin(self.train_years)]
        plan_by_case = {
            case: pd.DataFrame(rows) for case, rows in zip(RESOURCE_YEAR_TYPES, plans) if rows
        }
        # 2. 各年景训练年年份列表 → 指标表
        case_years = {
            case: years[(labels == case) & train_mask].astype(int).tolist()
            for case in RESOURCE_YEAR_TYPES
        }
        return evaluate_city(city, train_history, case_years, scenarios, plan_by_case)


# ─────────────────────────────────────────────────────────────────────────────
# 模型与入口
# ─────────────────────────────────────────────────────────────────────────────

class WindModel:
    """风电模型：__init__ 组装五步子模块，forward 顺序执行、维度逐行流转（分类为内联薄步骤）。"""

    def __init__(self, config: WindConfig):
        """组装平级子模块（事件库 EventDetails 在 forward 按数据构造）。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.config = config
        self.align = Align(config.source_years)  # 1 数据对齐
        self.base = BaseScene(config)       # 2 基础场景
        self.generate = Generator(config)   # 3 场景生成
        self.physics = Physics(config)      # 4 物理出力
        self.evaluate = Evaluate(config)    # 5 可选评估

    def forward(
        self,
        city: str,
        wind: pd.Series,
        temp: pd.Series,
        pressure: pd.Series,
        unit: WindUnit,
    ) -> tuple:
        """单单元全流程（joblib 工作函数，可 pickle）：对齐→分类→事件库→基础场景→生成→评估→重排→物理出力。

        Args:
            city (str): 单元键。
            wind (pd.Series): UTC 口径风速历史序列。
            temp (pd.Series): 输出年温度序列 (K)。
            pressure (pd.Series): 输出年气压序列 (Pa)。
            unit (WindUnit): 装机配置对象。

        Returns:
            tuple: (city, labels, case→场景Series(输出年), case→MW, 事件数, φ, 评估指标或 None)。
        """
        worker_logging()

        #步骤1 Series(UTC, 全量年) → (对齐序列, (N, 8760), 年标签) 
        aligned, grid, years = self.align(wind)

        #步骤2 (N, 8760) → (N, ) 资源年类型标签（逐年平均风速立方，训练年 P25/P75 分三类，最后一年也参与归类）
        metric = np.mean(np.power(grid, 3), axis=1) # (N, 8760) -> (N, )
        train_mask = np.isin(years, self.config.train_years) # (N, )
        p25, p75 = np.quantile(metric[train_mask], [0.25, 0.75]) # 标量, 标量
        labels = np.where(metric <= p25, "偏小资源年", np.where(metric >= p75, "偏大资源年", "正常资源年")) # (N, )

        #步骤3 训练年 (N, 8760) → (E, 5) 事件明细 EventDetails（低风/大风段长表，按需现取）
        events = EventDetails(grid[train_mask], years[train_mask], labels[train_mask])

        #步骤4 训练年 (N, 8760) → (8760, ) 基础场景 + 标量 φ（三 case 共用）
        base, phi = self.base(grid[train_mask])

        #步骤5 全量源年风速 (N, 8760) + 全量源年年份 (N, ) + 年景标签 (N, ) + 事件明细 (E, 5) + 基础场景 (8760, ) → 场景 (3, 8760) + 逐 case 插入方案行 list[dict]（键同输出表 8 列）
        scenes, plans = self.generate(grid, years, labels, events, base)
        scenarios = {
            case: pd.Series(row, index=self.generate.target_index, name=case)
            for case, row in zip(RESOURCE_YEAR_TYPES, scenes)
        } # {case: Series(轴年, 8760)}

        #步骤6 场景 + 分类 + 方案 + 历史 → 指标表或 None（评估用轴年场景，重排须在其后）
        metrics = self.evaluate(city, aligned, years, labels, train_mask, plans, scenarios)

        #步骤7 场景重排输出年（闰年 8784 小时轴）
        scenarios = {case: remap_to_year(scenario, self.config.year) for case, scenario in scenarios.items()}

        #步骤8 重排场景 + 输出年温压 + 机组配置 → 逐 case 出力 MW（区域汇总在 run_wind）
        power = self.physics(scenarios, temp, pressure, unit)
        return city, labels, scenarios, power, len(events.details), phi, metrics

    __call__ = forward


def run_wind(
    config: WindConfig,
    history_by_city: dict[str, pd.Series],
    temp_by_city: dict[str, pd.Series],
    pressure_by_city: dict[str, pd.Series],
    city_configs: dict[str, WindUnit],
) -> WindResult:
    """风电出力模型入口：进程池逐单元并行全流程（含物理出力），主进程区域汇总。

    Args:
        config (WindConfig): 风电算法配置。
        history_by_city (dict[str, pd.Series]): 单元键 → UTC 口径风速历史序列（已按 city_configs 过滤）。
        temp_by_city (dict[str, pd.Series]): 单元键 → 输出年温度序列 (K)（已对时间、按单元聚合好）。
        pressure_by_city (dict[str, pd.Series]): 单元键 → 输出年气压序列 (Pa)，同上。
        city_configs (dict[str, WindUnit]): 单元键 → 装机配置对象。

    Returns:
        WindResult: 三类场景、总出力结果、资源年分类、评估指标。
    """
    model = WindModel(config)
    year = config.year

    total = len(history_by_city)
    logger.info("[2-6/6] %d 个地市: 资源年分类→事件库→GPR场景生成→物理出力（多进程并行）...", total)
    results = Parallel(n_jobs=-1)(
        delayed(model.forward)(city, wind, temp_by_city[city], pressure_by_city[city], city_configs[city])
        for city, wind in history_by_city.items()
    )

    scenarios: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, np.ndarray] = {}
    power_by_city: dict[str, dict[str, np.ndarray]] = {}
    metrics_rows = []
    for i, (city, labels, city_scenarios, city_power, n_events, phi, metrics) in enumerate(results, 1):
        logger.info("[2-6/6] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)", i, total, city, n_events, phi)
        classes_all[city] = labels
        power_by_city[city] = city_power
        if metrics is not None:
            logger.info("[评估] 地市 %s: 指标计算完成", city)
            metrics_rows.append(metrics)
        for case, scenario in city_scenarios.items():
            scenarios.setdefault(case, {})[city] = scenario

    # 区域汇总（温压已由业务层对齐输出年北京时轴）：单元出力累加 + 场景风速容量加权
    logger.info("[6/6] 区域汇总: 输出年 %d, %d 个地市...", year, total)
    output_index = next(iter(next(iter(scenarios.values())).values())).index # 输出年轴（闰年 8784）
    capacities = {city: unit.capacity_mw for city, unit in city_configs.items()}
    total_capacity = sum(capacities.values()) # 标量 区域总装机
    power: dict[str, PowerResult] = {}
    for case, city_scenarios in scenarios.items():
        total_power = np.zeros(len(output_index)) # (轴长, ) 区域出力累加器
        weighted_wind = np.zeros(len(output_index)) # (轴长, ) 加权风速累加器
        city_power: dict[str, np.ndarray] = {}
        for city, scenario in city_scenarios.items():
            # 1. 单元出力累加 + 场景风速容量加权（城市顺序累加，汇总口径冻结）
            city_power[city] = power_by_city[city][case]
            total_power += city_power[city] # 单元求和 → 区域 MW
            weighted_wind += (capacities[city] / total_capacity) * scenario.to_numpy(float) # 容量加权风速
        # 2. 组装 PowerResult（MW / 装机占比 / 加权风速）
        power[case] = PowerResult(
            index=output_index,
            city_power=city_power,
            total_power_mw=total_power,
            total_power_pu=total_power / total_capacity,
            weighted_wind_speed_m_s=weighted_wind,
        )
    logger.info("[6/6] 完成: %s", {case: f"{len(r.index)}小时×{1 + len(r.city_power)}列" for case, r in power.items()})
    metrics = pd.concat(metrics_rows, ignore_index=True) if metrics_rows else None
    return WindResult(scenarios=scenarios, power=power, classes=classes_all, metrics=metrics)
