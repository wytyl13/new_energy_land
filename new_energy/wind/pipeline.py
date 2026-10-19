#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : pipeline.py

风电全流程组织：原始格点数据 → 三类风速场景 → 全省出力，中间结果全部在内存。

run_wind 是风电对外唯一入口：收统一 JSON（文件路径或 dict），在入口完成
校验与默认值填充（全项目唯一校验点），执行全流程后写出力 CSV 并返回结果
JSON（output_file/scenario/cities/total_capacity_mw/power_table）。
请求/数据类错误一律 ValueError 上抛并指明字段/文件/地市。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..common.city_weather import load_city_series, resolve_years
from ..common.request import (
    DEFAULT_ASSIGNMENT,
    DEFAULT_DATA_DIR,
    ENC,
    RESOURCE_YEAR_TYPES,
    assignment_keys,
    fail,
    load_payload,
    number,
    reject_unknown,
    string,
)
from ..common.time_axis import remap_to_year, year_index
from . import evaluate as evaluate_mod
from .classify import classify_years
from .events import build_event_standards, extract_events
from .generate import generate_scenarios
from .history import build_history
from .power import compute_power

# 历史年份不再写死：按数据目录实际内容解析（期望窗口见 common.request.DEFAULT_YEARS，
# 解析规则见 common.city_weather.resolve_years），训练年 = 除最后一年，场景轴年 = 最后一年。

# 服务端默认：全省机组配置（数据目录/归属表在 common.request）
DEFAULT_CITY_CONFIGS = Path(__file__).resolve().parent / "default_city_configs.json"

REQUEST_FIELDS = {
    "data_dir", "output_dir", "scenario", "city_configs", "assignment_file",
    "loss_factor", "output_city_power", "evaluate",
}
CITY_FIELDS = {"city", "capacity_mw", "turbines"}
TURBINE_FIELDS = {
    "model", "count", "rated_power_mw", "hub_height_m",
    "cut_in_m_s", "rated_m_s", "cut_out_m_s", "efficiency", "wind_shear_exponent",
}
TURBINE_REQUIRED = (
    "model", "count", "rated_power_mw", "hub_height_m",
    "cut_in_m_s", "rated_m_s", "cut_out_m_s",
)

logger = logging.getLogger(__name__)


@dataclass
class WindResult:
    """风电全流程输出结果：三类场景、全省出力表、资源年分类与评估指标。
"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 地市键 → 输出年时间轴的8760+场景
    power: dict[str, pd.DataFrame]                   # case → 全省出力表
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 地市键 → 资源年分类
    metrics: pd.DataFrame | None = None              # evaluate开启时的指标表


def _parse_turbine(raw: object, index: int) -> dict:
    """校验单个机型配置，返回 power 模块所需的机型字典。

    Args:
        raw (object): 请求中的机型对象。
        index (int): 在 turbines 数组中的下标（报错定位用）。

    Returns:
        dict: 校验通过的机型配置。
    """
    ctx = f"turbines[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, TURBINE_FIELDS, ctx)
    for key in TURBINE_REQUIRED:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    count = number(raw["count"], f"{ctx}.count")
    if count != int(count) or count <= 0:
        fail(f"{ctx}.count 必须是正整数")
    rated = number(raw["rated_power_mw"], f"{ctx}.rated_power_mw")
    if rated <= 0:
        fail(f"{ctx}.rated_power_mw 必须为正")
    return {
        "model": string(raw["model"], f"{ctx}.model"),
        "count": int(count),
        "rated_power_mw": rated,
        "hub_height_m": number(raw["hub_height_m"], f"{ctx}.hub_height_m"),
        "cut_in_m_s": number(raw["cut_in_m_s"], f"{ctx}.cut_in_m_s"),
        "rated_m_s": number(raw["rated_m_s"], f"{ctx}.rated_m_s"),
        "cut_out_m_s": number(raw["cut_out_m_s"], f"{ctx}.cut_out_m_s"),
        "efficiency": number(raw.get("efficiency", 1.0), f"{ctx}.efficiency"),
        "wind_shear_exponent": number(raw.get("wind_shear_exponent", 0.15), f"{ctx}.wind_shear_exponent"),
    }


def _parse_request(source: dict | str | Path) -> dict:
    """校验统一 JSON 并填充默认值，返回规范化配置（全项目唯一校验点）。

    Args:
        source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。

    Returns:
        dict: 规范化配置（data_dir/assignment_file/output_dir 为 Path，city_configs 已转为按键字典）。
    """
    payload = load_payload(source)
    reject_unknown(payload, REQUEST_FIELDS, "请求")

    if "output_dir" not in payload:
        fail("请求缺少必填字段 output_dir")
    output_dir = Path(string(payload["output_dir"], "output_dir"))

    if "data_dir" in payload:
        data_dir = Path(string(payload["data_dir"], "data_dir"))
    else:
        data_dir = DEFAULT_DATA_DIR
        logger.info("data_dir 未传，使用默认数据目录: %s", data_dir)
    if "assignment_file" in payload:
        assignment_file = Path(string(payload["assignment_file"], "assignment_file"))
    else:
        assignment_file = DEFAULT_ASSIGNMENT
    if not assignment_file.exists():
        fail(f"归属表不存在: {assignment_file}")
    for sub in ("wind", "temperature", "pressure"):
        if not (data_dir / sub).is_dir():
            fail(f"data_dir 下缺少子目录 {sub}: {data_dir / sub}")

    if "scenario" not in payload:
        fail("请求缺少必填字段 scenario")
    scenario = payload["scenario"]
    if not isinstance(scenario, dict):
        fail("scenario 必须是对象")
    reject_unknown(scenario, {"year", "resource_year_type"}, "scenario")
    for key in ("year", "resource_year_type"):
        if key not in scenario:
            fail(f"scenario 缺少必填字段 {key}")
    year = number(scenario["year"], "scenario.year")
    if year != int(year):
        fail("scenario.year 必须是整数")
    resource_year_type = string(scenario["resource_year_type"], "scenario.resource_year_type")
    if resource_year_type not in RESOURCE_YEAR_TYPES:
        fail(f"scenario.resource_year_type 必须是 {RESOURCE_YEAR_TYPES} 之一")

    if "city_configs" in payload:
        raw_cities = payload["city_configs"]
    else:
        raw_cities = json.loads(DEFAULT_CITY_CONFIGS.read_text(encoding="utf-8"))["city_configs"]
        logger.info("city_configs 未传，使用默认全省机组配置: %s (%d 地市)",
                    DEFAULT_CITY_CONFIGS.name, len(raw_cities))
    if not isinstance(raw_cities, list) or not raw_cities:
        fail("city_configs 必须是非空数组")
    valid_keys = assignment_keys(assignment_file)
    city_configs: dict[str, dict] = {}
    for i, raw in enumerate(raw_cities):
        ctx = f"city_configs[{i}]"
        if not isinstance(raw, dict):
            fail(f"{ctx} 必须是对象")
        reject_unknown(raw, CITY_FIELDS, ctx)
        for key in CITY_FIELDS:
            if key not in raw:
                fail(f"{ctx} 缺少必填字段 {key}")
        city = string(raw["city"], f"{ctx}.city")
        if city in city_configs:
            fail(f"city_configs 存在重复地市: {city}")
        if city not in valid_keys:
            fail(f"city_configs 含归属表之外的地市: {city}")
        turbines_raw = raw["turbines"]
        if not isinstance(turbines_raw, list) or not turbines_raw:
            fail(f"{ctx}.turbines 必须是非空数组")
        capacity = number(raw["capacity_mw"], f"{ctx}.capacity_mw")
        if capacity <= 0:
            fail(f"{ctx}.capacity_mw 必须为正")
        city_configs[city] = {
            "capacity_mw": capacity,
            "turbines": [_parse_turbine(t, j) for j, t in enumerate(turbines_raw)],
        }

    return {
        "data_dir": data_dir,
        "assignment_file": assignment_file,
        "output_dir": output_dir,
        "year": int(year),
        "resource_year_type": resource_year_type,
        "city_configs": city_configs,
        "loss_factor": number(payload.get("loss_factor", 1.0), "loss_factor"),
        "output_city_power": bool(payload.get("output_city_power", True)),
        "evaluate": bool(payload.get("evaluate", False)),
    }


def run_wind(source: dict | str | Path) -> dict:
    """风电对外唯一入口：统一 JSON → 全流程 → 写出力 CSV → 返回结果 JSON。

    Args:
        source (dict | str | Path): 统一 JSON 的 dict（服务层），或 JSON 文件路径（CLI）。

    Returns:
        dict: {"output_file", "elapsed_s", "scenario", "cities",
               "total_capacity_mw", "power_table"}，power_table 为列名→数组的字典，与 CSV 同列同值。
    """
    cfg = _parse_request(source)
    start = time.perf_counter()
    result = _compute(cfg)
    case = cfg["resource_year_type"]
    cfg["output_dir"].mkdir(parents=True, exist_ok=True)
    out_path = cfg["output_dir"] / f"onshore_wind_power_{cfg['year']}_{case}.csv"
    result.power[case].to_csv(out_path, index=False, encoding=ENC)
    logger.info("风电出力全流程完成: 耗时 %.1f 秒", time.perf_counter() - start)

    table = result.power[case].copy()
    table["time"] = table["time"].astype(str)
    return {
        "output_file": str(out_path),
        "elapsed_s": round(time.perf_counter() - start, 2),
        "scenario": {"year": cfg["year"], "resource_year_type": case},
        "cities": list(next(iter(result.scenarios.values()))),
        "total_capacity_mw": round(sum(c["capacity_mw"] for c in cfg["city_configs"].values()), 6),
        "power_table": table.to_dict("list"),
    }


def _compute(cfg: dict) -> WindResult:
    """执行风电全流程：格点风速 → 三类场景生成 → 全省出力。
"""
    grid_root = cfg["data_dir"]
    assignment_csv = cfg["assignment_file"]
    year = cfg["year"]
    loss_factor = cfg["loss_factor"]
    city_configs = cfg["city_configs"]
    output_city_power = cfg["output_city_power"]

    # 步1：格点 → 各地市风速历史（一次读入，全部地市同时聚合）
    # 历史年份按数据目录实际内容解析：期望窗口 ∩ 实际存在的年份（wind 单一类型）
    years = resolve_years(grid_root, ("wind",))
    source_years, train_years = years.source_years, years.train_years
    logger.info("[1/6] 读取风速格点并聚合到地市 (%d-%d年)...", source_years[0], source_years[-1])
    wind_by_city = load_city_series(grid_root, assignment_csv, "wind", source_years)
    logger.info("[1/6] 完成: 共 %d 个地市", len(wind_by_city))
    # 计算范围以 city_configs 的键为准（支持单市请求）：
    # 配置了但归属表/数据没有的地市 → 报错；数据有但配置没有 → 忽略。
    # 输出列顺序保持归属表顺序（与既有结果比对口径一致）。
    unknown = sorted(set(city_configs) - set(wind_by_city))
    if unknown:
        raise ValueError(f"city_configs 含数据范围之外的地市键: {unknown}")
    wind_by_city = {city: series for city, series in wind_by_city.items() if city in city_configs}

    output_index = year_index(year)
    scenarios: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, pd.DataFrame] = {}
    metrics_rows = []
    do_evaluate = cfg["evaluate"]

    total = len(wind_by_city)
    for i, (city, wind) in enumerate(wind_by_city.items(), 1):
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
            train_history, classes, events, standards, segments, years.axis_year, train_years
        )
        logger.info(
            "[2-5/6] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)",
            i, total, city, len(events), phi,
        )
        if do_evaluate:
            plan_by_case = {case: plans.loc[plans["年景类型"].eq(case)] for case in set(plans["年景类型"])}
            logger.info("[评估] 地市 %s: 指标计算完成", city)
            metrics_rows.append(
                evaluate_mod.evaluate_city(city, train_history, classes, city_scenarios, plan_by_case, train_years)
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
