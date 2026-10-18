# -*- coding: utf-8 -*-
"""风电全流程编排：原始格点数据 → 三类风速场景 → 全省出力，中间结果全部在内存。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..common.city_weather import load_city_series
from . import evaluate as evaluate_mod
from .classify import classify_years
from .events import build_event_standards, extract_events
from .generate import generate_scenarios
from .history import build_history, no_feb29, year_index
from .power import compute_power

SOURCE_YEARS = tuple(range(2000, 2026))
TRAIN_YEARS = tuple(range(2000, 2025))
TARGET_YEAR = 2025  # 场景生成固定在2025年8760小时时间轴上

logger = logging.getLogger(__name__)


@dataclass
class WindResult:
    scenarios: dict[str, dict[str, pd.Series]]      # case → 地市键 → 输出年时间轴的8760+场景
    power: dict[str, pd.DataFrame]                   # case → 全省出力表
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 地市键 → 资源年分类
    metrics: pd.DataFrame | None = None              # evaluate开启时的指标表


def remap_to_year(series: pd.Series, year: int) -> pd.Series:
    """把2025年时间轴的场景重排到输出年份（闰年补2月29日，时间插值）。"""
    target = year_index(year)
    nonleap = target[no_feb29(target)]
    return pd.Series(series.to_numpy(float), index=nonleap, name=series.name).reindex(target).interpolate(
        method="time", limit_direction="both"
    )


def run_wind(config: dict) -> WindResult:
    cfg_in = config["input"]
    root = Path(cfg_in["project_root"])
    assignment_csv = root / cfg_in["assignment_file"]
    grid_root = root / cfg_in["weather_grid_dir"]
    year = int(config["scenario"]["year"])
    loss_factor = float(config.get("loss_factor", 1.0))
    city_configs = config["city_configs"]
    output_city_power = bool(config.get("output_city_power", True))

    # 步1：格点 → 各地市风速历史（一次读入，全部地市同时聚合）
    logger.info("[1/6] 读取风速格点并聚合到地市 (%d-%d年)...", SOURCE_YEARS[0], SOURCE_YEARS[-1])
    wind_by_city = load_city_series(grid_root, assignment_csv, "wind", SOURCE_YEARS)
    logger.info("[1/6] 完成: 共 %d 个地市", len(wind_by_city))
    missing = sorted(set(wind_by_city) - set(city_configs))
    if missing:
        raise ValueError(f"配置文件缺少地市多机型配置: {missing}")

    output_index = year_index(year)
    scenarios: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, pd.DataFrame] = {}
    metrics_rows = []
    do_evaluate = bool(config.get("evaluate", False))

    total = len(wind_by_city)
    for i, (city, wind) in enumerate(wind_by_city.items(), 1):
        logger.info("[2-5/6] 地市 %d/%d %s: 资源年分类→事件库→GPR场景生成开始", i, total, city)
        history = build_history(wind, SOURCE_YEARS)
        train_history = history.series.loc[history.series.index.year.isin(TRAIN_YEARS)]

        # 步2：资源年分类
        classes = classify_years(history.series, TRAIN_YEARS)
        classes_all[city] = classes

        # 步3：极端事件库与标准
        events, segments = extract_events(train_history, classes, TRAIN_YEARS)
        standards = build_event_standards(events, classes, TRAIN_YEARS)

        # 步4+5：GPR基础场景 + 逐级校准与事件插入
        city_scenarios, plans, phi = generate_scenarios(
            train_history, classes, events, standards, segments, TARGET_YEAR, TRAIN_YEARS
        )
        logger.info(
            "[2-5/6] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)",
            i, total, city, len(events), phi,
        )
        if do_evaluate:
            plan_by_case = {case: plans.loc[plans["年景类型"].eq(case)] for case in set(plans["年景类型"])}
            logger.info("[评估] 地市 %s: 指标计算完成", city)
            metrics_rows.append(
                evaluate_mod.evaluate_city(city, train_history, classes, city_scenarios, plan_by_case, TRAIN_YEARS)
            )
        for case, scenario in city_scenarios.items():
            scenarios.setdefault(case, {})[city] = remap_to_year(scenario, year)

    # 步6：物理出力（温度、气压按输出年份读取）
    logger.info("[6/6] 物理出力计算: 读取 %d 年温度/气压格点...", year)
    temp_by_city = load_city_series(grid_root, assignment_csv, "temperature", [year])
    pressure_by_city = load_city_series(grid_root, assignment_csv, "pressure", [year])
    target_index = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")

    def to_beijing_year(series: pd.Series) -> pd.Series:
        s = pd.Series(series.to_numpy(float), index=series.index + pd.Timedelta(hours=8), name=series.name)
        return s.reindex(target_index).interpolate(method="time", limit_direction="both")

    temp_by_city = {city: to_beijing_year(s) for city, s in temp_by_city.items()}
    pressure_by_city = {city: to_beijing_year(s) for city, s in pressure_by_city.items()}

    power = compute_power(
        scenarios, temp_by_city, pressure_by_city, city_configs, target_index,
        loss_factor, output_city_power,
    )
    logger.info(
        "[6/6] 完成: %s", {case: f"{df.shape[0]}行×{df.shape[1]}列" for case, df in power.items()}
    )
    metrics = pd.concat(metrics_rows, ignore_index=True) if metrics_rows else None
    return WindResult(scenarios=scenarios, power=power, classes=classes_all, metrics=metrics)
