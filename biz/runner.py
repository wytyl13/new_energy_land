#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : runner.py

业务层组装入口：校验 → 取数 → 调算法 → 组装冻结列集输出表 → 写 CSV → 结果 JSON。

run 是对外唯一入口（CLI 与 HTTP 同路）。算法侧只收内存序列、只返回结果对象
（D-44）；输出表的列集、列名、行序在此组装（D-45）。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from new_energy.solar.pipeline import compute as compute_solar
from new_energy.solar.power import PowerResult as SolarPowerResult
from new_energy.wind.pipeline import compute as compute_wind
from new_energy.wind.power import PowerResult as WindPowerResult

from .assignment import ENC, load_city_series, resolve_years
from .request import parse_request

logger = logging.getLogger(__name__)


def run(kind: str, source: dict | str | Path) -> dict:
    """业务层对外唯一入口：统一 JSON → 全流程 → 写出力 CSV → 返回结果 JSON。

    Args:
        kind (str): 算法类型（wind/solar）。
        source (dict | str | Path): 统一 JSON 的 dict（服务层），或 JSON 文件路径（CLI）。

    Returns:
        dict: {"output_file", "elapsed_s", "scenario", "cities",
               "total_capacity_mw", "power_table"}，power_table 为列名→数组的字典，与 CSV 同列同值。
    """
    cfg = parse_request(source, kind)
    start = time.perf_counter()
    case = cfg["resource_year_type"]
    result = compute(kind, cfg)

    if kind == "wind":
        table = wind_table(result.power[case], cfg["output_city_power"])
        out_path = cfg["output_dir"] / f"onshore_wind_power_{cfg['year']}_{case}.csv"
    else:
        table = solar_table(result.power[case], cfg["output_city_power"])
        out_path = cfg["output_dir"] / f"pv_power_{cfg['year']}_{case}.csv"
    cfg["output_dir"].mkdir(parents=True, exist_ok=True)
    table.to_csv(out_path, index=False, encoding=ENC)
    logger.info("%s出力全流程完成: 耗时 %.1f 秒", "风电" if kind == "wind" else "光伏",
                time.perf_counter() - start)

    time_col = "time" if kind == "wind" else "时间"
    table = table.copy()
    table[time_col] = table[time_col].astype(str)
    return {
        "output_file": str(out_path),
        "elapsed_s": round(time.perf_counter() - start, 2),
        "scenario": {"year": cfg["year"], "resource_year_type": case},
        "cities": list(next(iter(result.scenarios.values()))),
        "total_capacity_mw": round(sum(c["capacity_mw"] for c in cfg["city_configs"].values()), 6),
        "power_table": table.to_dict("list"),
    }


def compute(kind: str, cfg: dict):
    """校验后的配置 → 取数 → 算法全流程（不写文件），供回归自测取场景与出力。

    Args:
        kind (str): 算法类型（wind/solar）。
        cfg (dict): parse_request 的输出。

    Returns:
        WindResult | SolarResult: 算法侧结果对象（scenarios/power/classes[/metrics]）。
    """
    if kind == "wind":
        return _compute_wind(cfg)
    if kind == "solar":
        return _compute_solar(cfg)
    raise ValueError(f"未知的算法类型: {kind}")


def _restrict(series_by_city: dict[str, pd.Series], city_configs: dict) -> dict[str, pd.Series]:
    """计算范围以 city_configs 的键为准（支持单市请求）。

    配置了但归属表/数据没有的地市 → 报错；数据有但配置没有 → 忽略。
    输出列顺序保持归属表顺序（与既有结果比对口径一致）。
    """
    unknown = sorted(set(city_configs) - set(series_by_city))
    if unknown:
        raise ValueError(f"city_configs 含数据范围之外的地市键: {unknown}")
    return {city: series for city, series in series_by_city.items() if city in city_configs}


def _to_beijing_year(series: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """UTC 序列转北京时间并对齐输出年时间轴（缺失处时间插值）。

    Args:
        series (pd.Series): UTC 时间索引的序列。
        target_index (pd.DatetimeIndex): 输出年整点小时轴。

    Returns:
        pd.Series: 北京时间索引、对齐 target_index 的序列。
    """
    s = pd.Series(series.to_numpy(float), index=series.index + pd.Timedelta(hours=8), name=series.name)
    return s.reindex(target_index).interpolate(method="time", limit_direction="both")


def _compute_wind(cfg: dict):
    """风电取数 + 算法调用：格点风速/温压 → 内存序列 → compute_wind。"""
    grid_root, assignment_csv = cfg["data_dir"], cfg["assignment_file"]
    year, direct_unit = cfg["year"], cfg["direct_unit"]
    units, prefixes = set(cfg["city_configs"]), cfg["variable_prefixes"]

    # 历史年份按数据目录实际内容解析：期望窗口 ∩ 实际存在的年份（wind 单一类型）
    years = resolve_years(grid_root, ("wind",))
    logger.info("[1/6] 读取风速格点并聚合到地市 (%d-%d年)...", years.source_years[0], years.source_years[-1])
    history_by_city = load_city_series(
        grid_root, assignment_csv, "wind", years.source_years,
        single_unit=direct_unit, units=units, prefixes=prefixes,
    )
    logger.info("[1/6] 完成: 共 %d 个地市", len(history_by_city))
    history_by_city = _restrict(history_by_city, cfg["city_configs"])

    # 输出年温压（物理出力用，北京时间口径）
    logger.info("[6/6] 物理出力计算: 读取 %d 年温度/气压格点...", year)
    temp_by_city = load_city_series(
        grid_root, assignment_csv, "temperature", [year],
        single_unit=direct_unit, units=units, prefixes=prefixes,
    )
    pressure_by_city = load_city_series(
        grid_root, assignment_csv, "pressure", [year],
        single_unit=direct_unit, units=units, prefixes=prefixes,
    )
    target_index = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")
    temp_by_city = {city: _to_beijing_year(s, target_index) for city, s in temp_by_city.items()}
    pressure_by_city = {city: _to_beijing_year(s, target_index) for city, s in pressure_by_city.items()}

    return compute_wind(
        history_by_city, temp_by_city, pressure_by_city,
        source_years=years.source_years, train_years=years.train_years, axis_year=years.axis_year,
        year=year, city_configs=cfg["city_configs"],
        loss_factor=cfg["loss_factor"], evaluate=cfg["evaluate"],
    )


def _compute_solar(cfg: dict):
    """光伏取数 + 算法调用：格点辐照/温度 → 内存序列 → compute_solar。"""
    grid_root, assignment_csv = cfg["data_dir"], cfg["assignment_file"]
    direct_unit = cfg["direct_unit"]
    units, prefixes = set(cfg["city_configs"]), cfg["variable_prefixes"]

    # 历史年份按数据目录实际内容解析：期望窗口 ∩ 实际存在的年份
    # （辐照度与温度取交集——温度按训练年求气候平均，两者必须同年份可用）
    years = resolve_years(grid_root, ("solar", "temperature"))
    logger.info("[1/4] 读取辐照度格点并聚合到地市 (%d-%d年)...", years.source_years[0], years.source_years[-1])
    solar_by_city = load_city_series(
        grid_root, assignment_csv, "solar", years.source_years,
        single_unit=direct_unit, units=units, prefixes=prefixes,
    )
    solar_by_city = _restrict(solar_by_city, cfg["city_configs"])

    # 地面温度用训练年逐小时气候平均值（UTC 原始序列，气候态换算在算法侧）
    logger.info("[3/4] 物理出力计算: 读取 %d-%d 年温度格点求气候平均...",
                years.train_years[0], years.train_years[-1])
    temp_by_city = _restrict(
        load_city_series(
            grid_root, assignment_csv, "temperature", years.train_years,
            single_unit=direct_unit, units=units, prefixes=prefixes,
        ),
        cfg["city_configs"],
    )

    return compute_solar(
        solar_by_city, temp_by_city,
        source_years=years.source_years, train_years=years.train_years, axis_year=years.axis_year,
        year=cfg["year"], city_configs=cfg["city_configs"],
        loss_factor=cfg["loss_factor"], albedo=cfg["albedo"],
    )


def wind_table(result: WindPowerResult, output_city_power: bool) -> pd.DataFrame:
    """风电结果对象 → 冻结列集出力表（time、省风速、省出力 MW/pu、{地市}_power_mw）。

    Args:
        result (WindPowerResult): 算法侧风电出力结果对象。
        output_city_power (bool): 是否附带各地市出力列。

    Returns:
        pd.DataFrame: 出力表（列集、列序为冻结对外约定）。
    """
    out = pd.DataFrame(
        {
            "time": result.index,
            "province_wind_speed_m_s": result.province_wind_speed_m_s,
            "province_power_mw": result.province_power_mw,
            "province_power_pu": result.province_power_pu,
        }
    )
    if output_city_power:
        for city, values in result.city_power.items():
            out[f"{city}_power_mw"] = values
    return out


def solar_table(result: SolarPowerResult, output_city_power: bool) -> pd.DataFrame:
    """光伏结果对象 → 冻结列集出力表（时间、{地市}_总出力_MW、区域总出力 MW/pu）。

    Args:
        result (SolarPowerResult): 算法侧光伏出力结果对象。
        output_city_power (bool): 是否附带各地市出力列。

    Returns:
        pd.DataFrame: 出力表（列集、列序为冻结对外约定）。
    """
    data: dict = {"时间": result.index}
    if output_city_power:
        for city, values in result.city_power.items():
            data[f"{city}_总出力_MW"] = values
    data["区域总出力_MW"] = result.region_power_mw
    data["区域总出力_pu"] = result.region_power_pu
    return pd.DataFrame(data)
