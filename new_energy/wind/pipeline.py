#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : pipeline.py

风电流程组织（算法层）：地市风速历史序列 → 三类风速场景 → 全省出力结果对象。

P6.1 起算法只做计算（D-44）：输入为内存序列（业务层 biz.runner 负责校验、
取数与写出），输出 WindResult；power 为结果对象，表组装在业务层（D-45）。
中间结果全部在内存，历史年份方案由业务层解析后传入。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from ..utils.time_axis import remap_to_year, year_index
from . import evaluate as evaluate_mod
from .classify import classify_years
from .events import build_event_standards, extract_events
from .generate import generate_scenarios
from .history import build_history
from .power import PowerResult, compute_power

logger = logging.getLogger(__name__)


@dataclass
class WindResult:
    """风电全流程输出结果：三类场景、全省出力结果对象、资源年分类与评估指标。
    """

    scenarios: dict[str, dict[str, pd.Series]]      # case → 地市键 → 输出年时间轴的场景
    power: dict[str, PowerResult]                   # case → 全省出力结果对象
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 地市键 → 资源年分类
    metrics: pd.DataFrame | None = None              # evaluate开启时的指标表


def compute(
    history_by_city: dict[str, pd.Series],
    temp_by_city: dict[str, pd.Series],
    pressure_by_city: dict[str, pd.Series],
    source_years: tuple[int, ...],
    train_years: tuple[int, ...],
    axis_year: int,
    year: int,
    city_configs: dict,
    loss_factor: float = 1.0,
    evaluate: bool = False,
) -> WindResult:
    """执行风电计算流程：地市风速历史 → 三类场景生成 → 全省出力。

    Args:
        history_by_city (dict[str, pd.Series]): 地市键 → UTC 口径风速历史序列（已按 city_configs 过滤）。
        temp_by_city (dict[str, pd.Series]): 地市键 → 输出年温度序列 (K)，北京时间、对齐输出年轴。
        pressure_by_city (dict[str, pd.Series]): 地市键 → 输出年气压序列 (Pa)，同上。
        source_years (tuple[int, ...]): 实际使用的历史年份（两端含）。
        train_years (tuple[int, ...]): 训练统计用年份 = source_years 除最后一年。
        axis_year (int): 场景轴年份 = source_years 最后一年（只当日历模板）。
        year (int): 输出年份。
        city_configs (dict): 地市键 → 装机配置（capacity_mw/turbines）。
        loss_factor (float): 综合损耗系数。
        evaluate (bool): 是否计算评估指标。

    Returns:
        WindResult: 三类场景、出力结果对象（各地市出力恒在，表组装口径由业务层掌握）、资源年分类与评估指标。
    """
    output_index = year_index(year)
    scenarios: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, pd.DataFrame] = {}
    metrics_rows = []

    total = len(history_by_city)
    for i, (city, wind) in enumerate(history_by_city.items(), 1):
        logger.info("[2-5/6] 地市 %d/%d %s: 资源年分类→事件库→GPR场景生成开始", i, total, city)
        history = build_history(wind, source_years)
        train_history = history.loc[history.index.year.isin(train_years)]

        # 步2：资源年分类
        classes = classify_years(history, train_years)
        classes_all[city] = classes

        # 步3：极端事件库与标准
        events, segments = extract_events(train_history, classes, train_years)
        standards = build_event_standards(events, classes, train_years)

        # 步4+5：GPR基础场景 + 逐级校准与事件插入
        city_scenarios, plans, phi = generate_scenarios(
            train_history, classes, events, standards, segments, axis_year, train_years
        )
        logger.info(
            "[2-5/6] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)",
            i, total, city, len(events), phi,
        )
        if evaluate:
            plan_by_case = {case: plans.loc[plans["年景类型"].eq(case)] for case in set(plans["年景类型"])}
            logger.info("[评估] 地市 %s: 指标计算完成", city)
            metrics_rows.append(
                evaluate_mod.evaluate_city(city, train_history, classes, city_scenarios, plan_by_case, train_years)
            )
        for case, scenario in city_scenarios.items():
            scenarios.setdefault(case, {})[city] = remap_to_year(scenario, year)

    # 步6：物理出力（温压已由业务层对齐输出年北京时轴）
    logger.info("[6/6] 物理出力计算: 输出年 %d, %d 个地市...", year, total)
    power = compute_power(
        scenarios, temp_by_city, pressure_by_city, city_configs, output_index, loss_factor
    )
    logger.info(
        "[6/6] 完成: %s", {case: f"{len(r.index)}小时×{1 + len(r.city_power)}列" for case, r in power.items()}
    )
    metrics = pd.concat(metrics_rows, ignore_index=True) if metrics_rows else None
    return WindResult(scenarios=scenarios, power=power, classes=classes_all, metrics=metrics)
