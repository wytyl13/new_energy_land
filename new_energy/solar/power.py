#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : power.py

步6：物理出力模型（水平面辐照度 → 组件倾斜面辐照度 → 组件温度 → 出力）。

多地市、多组件：每个地市可配多种组件（倾角/方位角/温度特性不同），按容量占比
叠加后得到地市总出力，再汇总区域总出力。温度和气压用训练年逐小时气候平均值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.time_axis import no_feb29, year_index

CLIMATOLOGY_INDEX = year_index(2025)[no_feb29(year_index(2025))]  # 8760 小时非闰年轴


def weather_climatology(series: pd.Series, years: tuple[int, ...]) -> pd.Series:
    """把逐年温度/气压序列按位置平均成 8760 小时气候态（删闰日、UTC+8）。

    Args:
        series (pd.Series): common.city_weather 返回的地市序列（UTC 时间索引，多年拼接）。
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
    return pd.Series(np.nanmean(np.vstack(blocks), axis=0), index=CLIMATOLOGY_INDEX, name=series.name)


def poa(ghi: np.ndarray, index: pd.DatetimeIndex, params: dict) -> np.ndarray:
    """水平面辐照度 → 组件倾斜面辐照度（POA）。

    Args:
        ghi (np.ndarray): 水平面辐照度（W/m2）。
        index (pd.DatetimeIndex): 与 ghi 对齐的北京时间轴。
        params (dict): latitude/longitude/tilt_deg/azimuth_deg/albedo。

    Returns:
        np.ndarray: 倾斜面辐照度（W/m2），非负。
    """
    n = index.dayofyear.to_numpy()
    h = index.hour.to_numpy() + index.minute.to_numpy() / 60
    dec = np.deg2rad(23.45 * np.sin(np.deg2rad(360 * (284 + n) / 365)))
    ha = np.deg2rad(15 * (h - 12 + (120 - params["longitude"]) / 15))
    la = np.deg2rad(params["latitude"])
    cz = np.clip(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha), 0, 1)
    z = np.arccos(cz)
    beta = np.deg2rad(params["tilt_deg"])
    az = np.deg2rad(params["azimuth_deg"])
    safe = np.maximum(cz, 0.02)
    dni = np.divide(ghi, safe, out=np.zeros_like(ghi), where=cz > 0)
    kd = np.where(ghi > 20, np.clip(1 - 1.13 * np.clip(ghi / (1367 * safe), 0, 1), 0.05, 1), 1)
    dhi = kd * ghi
    ci = np.cos(z) * np.cos(beta) + np.sin(z) * np.sin(beta) * np.cos(az - np.pi)
    return np.maximum(
        0,
        dni * np.maximum(ci, 0) + dhi * (1 + np.cos(beta)) / 2 + params["albedo"] * ghi * (1 - np.cos(beta)) / 2,
    )


def compute_power(
    scenarios: dict[str, dict[str, pd.Series]],
    temp_by_city: dict[str, pd.Series],
    city_configs: dict[str, dict],
    target_index: pd.DatetimeIndex,
    loss_factor: float = 1.0,
    albedo: float = 0.2,
    output_city_power: bool = True,
) -> dict[str, pd.DataFrame]:
    """把三类辐照度场景转为区域光伏出力表。

    Args:
        scenarios (dict[str, dict[str, pd.Series]]): 资源年类型 → 地市键 → 辐照度场景。
        temp_by_city (dict[str, pd.Series]): 地市键 → 地面温度气候态（K）。
        city_configs (dict[str, dict]): 地市键 → 装机与组件配置（capacity_mw/latitude/longitude/components）。
        target_index (pd.DatetimeIndex): 与场景对齐的时间轴（8760 小时）。
        loss_factor (float): 汇集、逆变及线路等综合损耗系数。
        albedo (float): 地面反照率。
        output_city_power (bool): 出力表是否附带各地市出力列。

    Returns:
        dict[str, pd.DataFrame]: 资源年类型 → 出力表（时间 + 各地市总出力 + 区域总出力 MW/pu）。
    """
    total_cap = sum(float(cfg["capacity_mw"]) for cfg in city_configs.values())
    power: dict[str, pd.DataFrame] = {}
    for case, city_scenarios in scenarios.items():
        table = pd.DataFrame(index=target_index)
        region_total = np.zeros(len(target_index))
        for city, cfg in city_configs.items():
            ghi = city_scenarios[city].to_numpy(float)
            temp_c = temp_by_city[city].to_numpy(float) - 273.15
            city_total = np.zeros(len(target_index))
            for comp in cfg["components"]:
                params = {
                    "latitude": cfg["latitude"],
                    "longitude": cfg["longitude"],
                    "albedo": albedo,
                    "tilt_deg": comp["tilt_deg"],
                    "azimuth_deg": comp["azimuth_deg"],
                }
                g = poa(ghi, target_index, params)
                tc = temp_c + (comp["noct_c"] - 20) / 800 * g
                p = (
                    float(cfg["capacity_mw"]) * float(comp["capacity_share"]) * loss_factor
                    * float(comp.get("efficiency", 1))
                    * np.clip(g / 1000 * (1 + comp["gamma_p_per_c"] * (tc - 25)), 0, 1.2)
                )
                city_total += p
            if output_city_power:
                table[f"{city}_总出力_MW"] = city_total
            region_total += city_total
        table["区域总出力_MW"] = region_total
        table["区域总出力_pu"] = region_total / total_cap
        table.insert(0, "时间", target_index)
        power[case] = table
    return power
