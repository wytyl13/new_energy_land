#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : pipeline.py

光伏流程组织（算法层）：地市辐照度历史序列 → 三类辐照度场景 → 区域出力结果对象。

P6.1 起算法只做计算（D-44）：输入为内存序列（业务层 biz.runner 负责校验、
取数与写出），输出 SolarResult；power 为结果对象（已重排到输出年），表组装
在业务层（D-45）。旧部署的两步文件接力（场景生成写 CSV → 物理模型再读回）
已合并为一次内存调用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from ..utils.time_axis import no_feb29, remap_to_year, year_index
from .classify import classify_years
from .events import build_event_standards, extract_low_irradiance_events
from .generate import build_ideal, generate_scenarios
from .history import build_history
from .power import PowerResult, compute_power, weather_climatology

logger = logging.getLogger(__name__)


@dataclass
class SolarResult:
    """光伏全流程输出结果：三类场景、区域出力结果对象与资源年分类。
    """
    scenarios: dict[str, dict[str, pd.Series]]      # case → 地市键 → 输出年份时间轴的场景
    power: dict[str, PowerResult]                   # case → 区域出力结果对象（输出年轴）
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 地市键 → 资源年分类


def compute(
    solar_by_city: dict[str, pd.Series],
    temp_by_city: dict[str, pd.Series],
    source_years: tuple[int, ...],
    train_years: tuple[int, ...],
    axis_year: int,
    year: int,
    city_configs: dict,
    loss_factor: float = 1.0,
    albedo: float = 0.2,
) -> SolarResult:
    """执行光伏计算流程：地市辐照度历史 → 三类场景生成 → 区域出力。

    Args:
        solar_by_city (dict[str, pd.Series]): 地市键 → UTC 口径辐照度历史序列（已按 city_configs 过滤）。
        temp_by_city (dict[str, pd.Series]): 地市键 → UTC 口径训练年温度序列（气候态换算在本流程）。
        source_years (tuple[int, ...]): 实际使用的历史年份（两端含）。
        train_years (tuple[int, ...]): 训练统计用年份 = source_years 除最后一年。
        axis_year (int): 场景轴年份 = source_years 最后一年（只当日历模板）。
        year (int): 输出年份。
        city_configs (dict): 地市键 → 装机配置（capacity_mw/latitude/longitude/components）。
        loss_factor (float): 综合损耗系数。
        albedo (float): 地面反照率。

    Returns:
        SolarResult: 三类场景、出力结果对象（各地市出力恒在，表组装口径由业务层掌握）、资源年分类。
    """
    target_index = year_index(axis_year)[no_feb29(year_index(axis_year))]
    scenarios_target: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, pd.DataFrame] = {}

    total = len(solar_by_city)
    for i, (city, series) in enumerate(solar_by_city.items(), 1):
        logger.info("[2/4] 地市 %d/%d %s: 资源年分类→低辐照事件库→GPR场景生成开始", i, total, city)
        history = build_history(series, source_years)
        latitude, longitude = city_configs[city]["latitude"], city_configs[city]["longitude"]
        ideal_history = build_ideal(history, latitude, longitude)
        # 分类用全量历史（最后一年也参与归类），事件库与场景生成只用训练年
        train_history = history.loc[history.index.year.isin(train_years)]
        train_ideal = ideal_history.loc[ideal_history.index.year.isin(train_years)]
        classes = classify_years(history, train_years)
        classes_all[city] = classes
        events, segments = extract_low_irradiance_events(train_history, train_ideal, classes, train_years)
        standards = build_event_standards(events, classes, train_years)
        city_scenarios, _plans, phi = generate_scenarios(
            train_history, train_ideal, classes, events, standards, segments,
            latitude, longitude, axis_year, train_years,
        )
        logger.info("[2/4] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)", i, total, city, len(events), phi)
        for case, scenario in city_scenarios.items():
            scenarios_target.setdefault(case, {})[city] = scenario

    # 步3：物理出力（地面温度用训练年逐小时气候平均值，与旧模型口径一致）
    logger.info("[3/4] 物理出力计算: 训练年温度气候平均 (%d-%d)...", train_years[0], train_years[-1])
    temp_clim = {
        city: weather_climatology(series, train_years)
        for city, series in temp_by_city.items()
    }
    power_target = compute_power(
        scenarios_target, temp_clim, city_configs, target_index, loss_factor, albedo
    )
    logger.info("[3/4] 完成: %s", {case: f"{len(r.index)}小时" for case, r in power_target.items()})

    # 步4：重排到输出年份（闰年补 2 月 29 日），场景与出力同为输出年轴
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
            region_power_mw=remap_to_year(pd.Series(r.region_power_mw, index=r.index), year).to_numpy(float),
            region_power_pu=remap_to_year(pd.Series(r.region_power_pu, index=r.index), year).to_numpy(float),
        )
    return SolarResult(scenarios=scenarios, power=power, classes=classes_all)
