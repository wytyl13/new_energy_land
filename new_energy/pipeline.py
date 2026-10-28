#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : pipeline.py

模型入口（D-46）：一个类 = 一个模型实例，__init__ 从配置组装各阶段模块，
forward 顺序执行（__call__ 同 forward），数据流转在函数体内一眼可见。

算法只做计算（D-44）：输入内存序列（业务层 biz.runner 负责校验、取数与写出），
输出结果对象；跨单元汇总在本层，表组装与写文件在业务层（D-45）。
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .classify import ResourceYearClassifier
from .config import SolarConfig, SolarUnit, WindConfig, WindUnit
from .events import SolarEventStage, WindEventStage
from .generate import SolarScenarioGenerator, WindScenarioGenerator, build_ideal
from .history import HistoryBuilder
from .power import PowerResult, SolarPowerStage, WindPowerStage, weather_climatology
from .utils.time_axis import remap_to_year, year_index
from .evaluate import evaluate_city

logger = logging.getLogger(__name__)


def _single_thread_blas() -> None:
    """进程池并行前把 BLAS 线程数定为 1（须在 spawn 前设，子进程继承）。

    N 个子进程 × 各自多线程 BLAS 会互抢核，实测 18 市 177s→121s；
    父进程自身 BLAS 已初始化，不受此设置影响。
    """
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"


def _worker_logging() -> None:
    """进程池子进程首次执行时配置日志（spawn 不继承父进程 handler）。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


def _wind_stages(config: WindConfig):
    """按配置组装风电路径各阶段模块（__init__ 与并行工作进程共用）。

    风电年际指标 = 年平均风速立方（m3/s3），表达式与原实现逐字一致。
    """
    return (
        HistoryBuilder(config.source_years),
        ResourceYearClassifier(
            lambda block: float(np.mean(np.power(block.to_numpy(float), 3))),
            "年平均风速立方_m3_s3",
            config.train_years,
        ),
        WindEventStage(config.train_years),
        WindScenarioGenerator(config),
    )


def _wind_city_job(args: tuple) -> tuple:
    """单地市：资源年分类→事件库→GPR 场景（进程池工作函数，入出参均可 pickle）。

    随机数逐市固定种子（generate.RANDOM_SEED），并行与串行结果一致。
    """
    city, wind, config = args
    _worker_logging()
    history, classify, events, generate = _wind_stages(config)
    hist = history(wind)
    train_history = hist.loc[hist.index.year.isin(config.train_years)]
    classes = classify(hist)
    library = events(train_history, classes)
    city_scenarios, plans, phi = generate(train_history, classes, library)
    metrics = None
    if config.evaluate:
        plan_by_case = {case: plans.loc[plans["年景类型"].eq(case)] for case in set(plans["年景类型"])}
        metrics = evaluate_city(city, train_history, classes, city_scenarios, plan_by_case, config.train_years)
    return city, classes, city_scenarios, len(library.events), phi, metrics


def _solar_stages(config: SolarConfig):
    """按配置组装光伏路径各阶段模块（__init__ 与并行工作进程共用）。

    光伏年际指标 = 年累计辐照量（Wh/m2 → kWh/m2），表达式与原实现逐字一致。
    """
    return (
        HistoryBuilder(config.source_years),
        ResourceYearClassifier(
            lambda block: float(block.sum() / 1000.0),
            "年累计辐照量_kWh_m2",
            config.train_years,
        ),
        SolarEventStage(config),
        SolarScenarioGenerator(config),
    )


def _solar_city_job(args: tuple) -> tuple:
    """单地市：资源年分类→低辐照事件库→GPR 场景（进程池工作函数）。"""
    city, series, config, latitude, longitude = args
    _worker_logging()
    history, classify, events, generate = _solar_stages(config)
    hist = history(series)
    ideal_history = build_ideal(hist, latitude, longitude)
    # 分类用全量历史（最后一年也参与归类），事件库与场景生成只用训练年
    train_history = hist.loc[hist.index.year.isin(config.train_years)]
    train_ideal = ideal_history.loc[ideal_history.index.year.isin(config.train_years)]
    classes = classify(hist)
    library = events(train_history, train_ideal, classes)
    city_scenarios, _plans, phi = generate(train_history, train_ideal, classes, library, latitude, longitude)
    return city, classes, city_scenarios, len(library.events), phi


@dataclass
class WindResult:
    """风电全流程输出结果：三类场景、总出力结果对象、资源年分类与评估指标。"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 单元键 → 输出年时间轴的场景
    power: dict[str, PowerResult]                   # case → 出力结果对象
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 单元键 → 资源年分类
    metrics: pd.DataFrame | None = None              # evaluate开启时的指标表


@dataclass
class SolarResult:
    """光伏全流程输出结果：三类场景、总出力结果对象与资源年分类。"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 单元键 → 输出年份时间轴的场景
    power: dict[str, PowerResult]                   # case → 出力结果对象（输出年轴）
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 单元键 → 资源年分类


class WindPowerModel:
    """风电出力模型：风速历史 → 资源年分类 → 事件库 → 三类场景 → 总出力。"""

    def __init__(self, config: WindConfig):
        """按配置组装各阶段模块。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.config = config
        self.history, self.classify, self.events, self.generate = _wind_stages(config)
        self.power = WindPowerStage(config)

    def forward(
        self,
        history_by_city: dict[str, pd.Series],
        temp_by_city: dict[str, pd.Series],
        pressure_by_city: dict[str, pd.Series],
        city_configs: dict[str, WindUnit],
    ) -> WindResult:
        """执行风电计算流程（各地市独立生成场景，再汇总出力）。

        Args:
            history_by_city (dict[str, pd.Series]): 单元键 → UTC 口径风速历史序列（已按 city_configs 过滤）。
            temp_by_city (dict[str, pd.Series]): 单元键 → 输出年温度序列 (K)，北京时间、对齐输出年轴。
            pressure_by_city (dict[str, pd.Series]): 单元键 → 输出年气压序列 (Pa)，同上。
            city_configs (dict[str, WindUnit]): 单元键 → 装机配置对象。

        Returns:
            WindResult: 三类场景、出力结果对象、资源年分类与评估指标。
        """
        year = self.config.year
        scenarios: dict[str, dict[str, pd.Series]] = {}
        classes_all: dict[str, pd.DataFrame] = {}
        metrics_rows = []

        total = len(history_by_city)
        jobs = [(city, wind, self.config) for city, wind in history_by_city.items()]
        logger.info("[2-5/6] %d 个地市: 资源年分类→事件库→GPR场景生成（多进程并行）...", total)
        if total == 1:  # 单市不起进程池，省去 spawn 开销；工作函数与并行同路
            results = [_wind_city_job(jobs[0])]
        else:
            _single_thread_blas()
            with ProcessPoolExecutor() as pool:
                results = list(pool.map(_wind_city_job, jobs))
        for i, (city, classes, city_scenarios, n_events, phi, metrics) in enumerate(results, 1):
            logger.info(
                "[2-5/6] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)",
                i, total, city, n_events, phi,
            )
            classes_all[city] = classes
            if metrics is not None:
                logger.info("[评估] 地市 %s: 指标计算完成", city)
                metrics_rows.append(metrics)
            for case, scenario in city_scenarios.items():
                scenarios.setdefault(case, {})[city] = remap_to_year(scenario, year)

        # 物理出力（温压已由业务层对齐输出年北京时轴）
        logger.info("[6/6] 物理出力计算: 输出年 %d, %d 个地市...", year, total)
        power = self.power(scenarios, temp_by_city, pressure_by_city, city_configs)
        logger.info(
            "[6/6] 完成: %s", {case: f"{len(r.index)}小时×{1 + len(r.city_power)}列" for case, r in power.items()}
        )
        metrics = pd.concat(metrics_rows, ignore_index=True) if metrics_rows else None
        return WindResult(scenarios=scenarios, power=power, classes=classes_all, metrics=metrics)

    __call__ = forward


class SolarPowerModel:
    """光伏出力模型：辐照度历史 → 资源年分类 → 低辐照事件库 → 三类场景 → 总出力。"""

    def __init__(self, config: SolarConfig):
        """按配置组装各阶段模块。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.config = config
        self.history, self.classify, self.events, self.generate = _solar_stages(config)
        self.power = SolarPowerStage(config)

    def forward(
        self,
        solar_by_city: dict[str, pd.Series],
        temp_by_city: dict[str, pd.Series],
        city_configs: dict[str, SolarUnit],
    ) -> SolarResult:
        """执行光伏计算流程（各地市独立生成场景，再汇总出力）。

        Args:
            solar_by_city (dict[str, pd.Series]): 单元键 → UTC 口径辐照度历史序列（已按 city_configs 过滤）。
            temp_by_city (dict[str, pd.Series]): 单元键 → UTC 口径训练年温度序列（气候态换算在本流程）。
            city_configs (dict[str, SolarUnit]): 单元键 → 装机与组件配置对象。

        Returns:
            SolarResult: 三类场景、出力结果对象（输出年轴）、资源年分类。
        """
        year = self.config.year
        scenarios_target: dict[str, dict[str, pd.Series]] = {}
        classes_all: dict[str, pd.DataFrame] = {}

        total = len(solar_by_city)
        jobs = [
            (city, series, self.config, city_configs[city].latitude, city_configs[city].longitude)
            for city, series in solar_by_city.items()
        ]
        logger.info("[2/4] %d 个地市: 资源年分类→低辐照事件库→GPR场景生成（多进程并行）...", total)
        if total == 1:  # 单市不起进程池，省去 spawn 开销；工作函数与并行同路
            results = [_solar_city_job(jobs[0])]
        else:
            _single_thread_blas()
            with ProcessPoolExecutor() as pool:
                results = list(pool.map(_solar_city_job, jobs))
        for i, (city, classes, city_scenarios, n_events, phi) in enumerate(results, 1):
            logger.info("[2/4] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)", i, total, city, n_events, phi)
            classes_all[city] = classes
            for case, scenario in city_scenarios.items():
                scenarios_target.setdefault(case, {})[city] = scenario

        # 物理出力（地面温度用训练年逐小时气候平均值，与旧模型口径一致）
        logger.info("[3/4] 物理出力计算: 训练年温度气候平均 (%d-%d)...",
                    self.config.train_years[0], self.config.train_years[-1])
        temp_clim = {
            city: weather_climatology(series, self.config.train_years)
            for city, series in temp_by_city.items()
        }
        power_target = self.power(scenarios_target, temp_clim, city_configs)
        logger.info("[3/4] 完成: %s", {case: f"{len(r.index)}小时" for case, r in power_target.items()})

        # 重排到输出年份（闰年补 2 月 29 日），场景与出力同为输出年轴
        scenarios = {
            case: {city: remap_to_year(series, year) for city, series in cities.items()}
            for case, cities in scenarios_target.items()
        }
        power: dict[str, PowerResult] = {}
        for case, r in power_target.items():
            power[case] = PowerResult(
                index=year_index(year),
                city_power={
                    city: remap_to_year(pd.Series(values, index=r.index), year).to_numpy(float)
                    for city, values in r.city_power.items()
                },
                total_power_mw=remap_to_year(pd.Series(r.total_power_mw, index=r.index), year).to_numpy(float),
                total_power_pu=remap_to_year(pd.Series(r.total_power_pu, index=r.index), year).to_numpy(float),
            )
        return SolarResult(scenarios=scenarios, power=power, classes=classes_all)

    __call__ = forward
