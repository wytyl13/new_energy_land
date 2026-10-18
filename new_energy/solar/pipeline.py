# -*- coding: utf-8 -*-
"""光伏全流程组织：原始格点数据 → 三类辐照度场景 → 区域出力，中间结果全部在内存。

run_solar 是光伏对外唯一入口：收统一 JSON（文件路径或 dict），在入口完成
校验与默认值填充（与风电同构，公共解析件在 common.request），执行全流程后
写出力 CSV 并返回结果 JSON。旧部署的两步文件接力（场景生成写 CSV → 物理
模型再读回）已合并为一次调用。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..common.city_weather import load_city_series
from ..common.request import (
    DEFAULT_ASSIGNMENT,
    DEFAULT_DATA_DIR,
    RESOURCE_YEAR_TYPES,
    assignment_keys,
    fail,
    load_payload,
    number,
    reject_unknown,
    string,
)
from ..common.time_axis import no_feb29, remap_to_year, year_index
from .classify import classify_years
from .events import build_event_standards, extract_low_irradiance_events
from .generate import build_ideal, generate_scenarios
from .history import build_history, city_centers
from .power import compute_power, weather_climatology

SOURCE_YEARS = tuple(range(2000, 2026))
TRAIN_YEARS = tuple(range(2000, 2025))
TARGET_YEAR = 2025  # 场景生成固定在2025年8760小时时间轴上

# 服务端默认：儋州区域机组配置（全省光伏装机数据到位后升级）
DEFAULT_CITY_CONFIGS = Path(__file__).resolve().parent / "default_city_configs.json"

REQUEST_FIELDS = {
    "data_dir", "output_dir", "scenario", "city_configs", "assignment_file",
    "loss_factor", "albedo", "output_city_power",
}
CITY_FIELDS = {"city", "capacity_mw", "latitude", "longitude", "components"}
COMPONENT_FIELDS = {
    "name", "capacity_share", "tilt_deg", "azimuth_deg", "noct_c", "gamma_p_per_c", "efficiency",
}
COMPONENT_REQUIRED = ("name", "capacity_share", "tilt_deg", "azimuth_deg", "noct_c", "gamma_p_per_c")
ENC = "utf-8-sig"

logger = logging.getLogger(__name__)


@dataclass
class SolarResult:
    """光伏全流程输出结果：三类场景、区域出力表与资源年分类。"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 地市键 → 输出年份时间轴的场景
    power: dict[str, pd.DataFrame]                   # case → 区域出力表
    classes: dict[str, pd.DataFrame] = field(default_factory=dict)   # 地市键 → 资源年分类


def _parse_component(raw: object, index: int) -> dict:
    """校验单个组件配置，返回 power 模块所需的组件字典。

    Args:
        raw (object): 请求中的组件对象。
        index (int): 在 components 数组中的下标（报错定位用）。

    Returns:
        dict: 校验通过的组件配置。
    """
    ctx = f"components[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, COMPONENT_FIELDS, ctx)
    for key in COMPONENT_REQUIRED:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    share = number(raw["capacity_share"], f"{ctx}.capacity_share")
    if not 0 < share <= 1:
        fail(f"{ctx}.capacity_share 必须在 (0, 1]")
    return {
        "name": string(raw["name"], f"{ctx}.name"),
        "capacity_share": share,
        "tilt_deg": number(raw["tilt_deg"], f"{ctx}.tilt_deg"),
        "azimuth_deg": number(raw["azimuth_deg"], f"{ctx}.azimuth_deg"),
        "noct_c": number(raw["noct_c"], f"{ctx}.noct_c"),
        "gamma_p_per_c": number(raw["gamma_p_per_c"], f"{ctx}.gamma_p_per_c"),
        "efficiency": number(raw.get("efficiency", 1.0), f"{ctx}.efficiency"),
    }


def _parse_city(raw: object, index: int, valid_keys: set[str]) -> tuple[str, dict]:
    """校验单个地市配置，返回 (地市键, 规范化配置)。

    Args:
        raw (object): 请求中的地市对象。
        index (int): 在 city_configs 数组中的下标（报错定位用）。
        valid_keys (set[str]): 归属表全部合法地市键。

    Returns:
        tuple[str, dict]: 地市键与规范化配置（capacity_mw/latitude/longitude/components）。
    """
    ctx = f"city_configs[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, CITY_FIELDS, ctx)
    for key in CITY_FIELDS:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    city = string(raw["city"], f"{ctx}.city")
    if city not in valid_keys:
        fail(f"city_configs 含归属表之外的地市: {city}")
    capacity = number(raw["capacity_mw"], f"{ctx}.capacity_mw")
    if capacity <= 0:
        fail(f"{ctx}.capacity_mw 必须为正")
    latitude = number(raw["latitude"], f"{ctx}.latitude")
    longitude = number(raw["longitude"], f"{ctx}.longitude")
    if not -90 <= latitude <= 90:
        fail(f"{ctx}.latitude 必须在 [-90, 90]")
    if not -180 <= longitude <= 180:
        fail(f"{ctx}.longitude 必须在 [-180, 180]")
    components_raw = raw["components"]
    if not isinstance(components_raw, list) or not components_raw:
        fail(f"{ctx}.components 必须是非空数组")
    components = [_parse_component(c, j) for j, c in enumerate(components_raw)]
    share_sum = sum(c["capacity_share"] for c in components)
    if abs(share_sum - 1.0) > 1e-6:
        fail(f"{ctx}.components 的 capacity_share 合计应为 1，实际 {share_sum:g}")
    return city, {
        "capacity_mw": capacity,
        "latitude": latitude,
        "longitude": longitude,
        "components": components,
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
    for sub in ("solar", "temperature"):
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
        logger.info("city_configs 未传，使用默认机组配置（儋州区域）: %s (%d 地市)",
                    DEFAULT_CITY_CONFIGS.name, len(raw_cities))
    if not isinstance(raw_cities, list) or not raw_cities:
        fail("city_configs 必须是非空数组")
    valid_keys = assignment_keys(assignment_file)
    city_configs: dict[str, dict] = {}
    for i, raw in enumerate(raw_cities):
        city, cfg = _parse_city(raw, i, valid_keys)
        if city in city_configs:
            fail(f"city_configs 存在重复地市: {city}")
        city_configs[city] = cfg

    return {
        "data_dir": data_dir,
        "assignment_file": assignment_file,
        "output_dir": output_dir,
        "year": int(year),
        "resource_year_type": resource_year_type,
        "city_configs": city_configs,
        "loss_factor": number(payload.get("loss_factor", 1.0), "loss_factor"),
        "albedo": number(payload.get("albedo", 0.2), "albedo"),
        "output_city_power": bool(payload.get("output_city_power", True)),
    }


def run_solar(source: dict | str | Path) -> dict:
    """光伏对外唯一入口：统一 JSON → 全流程 → 写出力 CSV → 返回结果 JSON。

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
    out_path = cfg["output_dir"] / f"pv_power_{cfg['year']}_{case}.csv"
    result.power[case].to_csv(out_path, index=False, encoding=ENC)
    logger.info("光伏出力全流程完成: 耗时 %.1f 秒", time.perf_counter() - start)

    table = result.power[case].copy()
    table["时间"] = table["时间"].astype(str)
    return {
        "output_file": str(out_path),
        "elapsed_s": round(time.perf_counter() - start, 2),
        "scenario": {"year": cfg["year"], "resource_year_type": case},
        "cities": list(next(iter(result.scenarios.values()))),
        "total_capacity_mw": round(sum(c["capacity_mw"] for c in cfg["city_configs"].values()), 6),
        "power_table": table.to_dict("list"),
    }


def _compute(cfg: dict) -> SolarResult:
    """执行光伏全流程：格点辐照度 → 三类场景生成 → 区域出力。"""
    grid_root = cfg["data_dir"]
    assignment_csv = cfg["assignment_file"]
    year = cfg["year"]
    loss_factor = cfg["loss_factor"]
    albedo = cfg["albedo"]
    city_configs = cfg["city_configs"]
    output_city_power = cfg["output_city_power"]

    # 步1：格点 → 各地市辐照度历史（一次读入，全部地市同时聚合）
    logger.info("[1/4] 读取辐照度格点并聚合到地市 (%d-%d年)...", SOURCE_YEARS[0], SOURCE_YEARS[-1])
    solar_by_city = load_city_series(grid_root, assignment_csv, "solar", SOURCE_YEARS)
    # 计算范围以 city_configs 的键为准（支持单市请求），口径与风电一致
    unknown = sorted(set(city_configs) - set(solar_by_city))
    if unknown:
        raise ValueError(f"city_configs 含数据范围之外的地市键: {unknown}")
    solar_by_city = {city: series for city, series in solar_by_city.items() if city in city_configs}
    centers = city_centers(assignment_csv)

    target_index = year_index(TARGET_YEAR)[no_feb29(year_index(TARGET_YEAR))]
    scenarios_target: dict[str, dict[str, pd.Series]] = {}
    classes_all: dict[str, pd.DataFrame] = {}

    total = len(solar_by_city)
    for i, (city, series) in enumerate(solar_by_city.items(), 1):
        logger.info("[2/4] 地市 %d/%d %s: 资源年分类→低辐照事件库→GPR场景生成开始", i, total, city)
        history = build_history(series, SOURCE_YEARS)
        latitude, longitude = centers[city]
        ideal_history = build_ideal(history, latitude, longitude)
        # 分类用全量历史（2025 也参与归类），事件库与场景生成只用训练年
        train_history = history.loc[history.index.year.isin(TRAIN_YEARS)]
        train_ideal = ideal_history.loc[ideal_history.index.year.isin(TRAIN_YEARS)]
        classes = classify_years(history, TRAIN_YEARS)
        classes_all[city] = classes
        events, segments = extract_low_irradiance_events(train_history, train_ideal, classes, TRAIN_YEARS)
        standards = build_event_standards(events, classes, TRAIN_YEARS)
        city_scenarios, _plans, phi = generate_scenarios(
            train_history, train_ideal, classes, events, standards, segments,
            latitude, longitude, TARGET_YEAR, TRAIN_YEARS,
        )
        logger.info("[2/4] 地市 %d/%d %s: 完成 (事件库 %d 个, GPR残差lag1 φ=%.2f)", i, total, city, len(events), phi)
        for case, scenario in city_scenarios.items():
            scenarios_target.setdefault(case, {})[city] = scenario

    # 步3：物理出力（地面温度用训练年逐小时气候平均值，与旧模型口径一致）
    logger.info("[3/4] 物理出力计算: 读取 %d-%d 年温度格点求气候平均...", TRAIN_YEARS[0], TRAIN_YEARS[-1])
    temp_by_city = load_city_series(grid_root, assignment_csv, "temperature", TRAIN_YEARS)
    temp_by_city = {
        city: weather_climatology(series, TRAIN_YEARS)
        for city, series in temp_by_city.items()
        if city in city_configs
    }
    power_target = compute_power(
        scenarios_target, temp_by_city, city_configs, target_index,
        loss_factor, albedo, output_city_power,
    )
    logger.info("[3/4] 完成: %s", {case: f"{df.shape[0]}行×{df.shape[1]}列" for case, df in power_target.items()})

    # 步4：重排到输出年份（闰年补 2 月 29 日）
    scenarios = {
        case: {city: remap_to_year(series, year) for city, series in cities.items()}
        for case, cities in scenarios_target.items()
    }
    power = {
        case: pd.DataFrame(
            {col: remap_to_year(table[col], year) for col in table.columns if col != "时间"},
            index=year_index(year),
        )
        for case, table in power_target.items()
    }
    for table in power.values():
        table.insert(0, "时间", year_index(year))
    return SolarResult(scenarios=scenarios, power=power, classes=classes_all)
