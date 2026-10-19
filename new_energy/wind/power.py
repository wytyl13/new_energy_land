#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : power.py

步6：风电物理出力（轮毂高度切变、空气密度、分段立方功率曲线、省市汇总）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RHO_REF = 1.225
GAMMA = 0.0065
GRAVITY = 9.80665
R_D = 287.05


def turbine_fraction(
    wind: np.ndarray, temp_k: np.ndarray, pressure_pa: np.ndarray, turbine: dict, alpha: float
) -> np.ndarray:
    """计算单机型出力占额定出力的比例（0~1），含轮毂高度切变与空气密度修正。

    Args:
        wind (np.ndarray): 100 米高度风速序列 (m/s)。
        temp_k (np.ndarray): 地面 2 米温度序列 (K)。
        pressure_pa (np.ndarray): 地面气压序列 (Pa)。
        turbine (dict): 单机型配置（轮毂高度、切入/额定/切出风速等）。
        alpha (float): 风切变指数。

    Returns:
        np.ndarray: 逐小时出力比例（0~1）。
    """
    hub = float(turbine["hub_height_m"])
    vin = float(turbine["cut_in_m_s"])
    vr = float(turbine["rated_m_s"])
    vout = float(turbine["cut_out_m_s"])
    vh = wind * (hub / 100.0) ** alpha
    th = temp_k - GAMMA * (hub - 2.0)
    ph = pressure_pa * np.exp(-GRAVITY * hub / (R_D * th))
    rho_factor = (ph / (R_D * th)) / RHO_REF
    frac = np.zeros_like(vh)
    ramp = (vh >= vin) & (vh < vr)
    rated = (vh >= vr) & (vh <= vout)
    frac[ramp] = np.clip(rho_factor[ramp] * (vh[ramp] ** 3 - vin**3) / (vr**3 - vin**3), 0, 1)
    frac[rated] = 1.0
    return frac


def compute_city_power(
    wind: pd.Series,
    temp_k: pd.Series,
    pressure_pa: pd.Series,
    city_config: dict,
    loss_factor: float = 1.0,
) -> np.ndarray:
    """计算一个地市的逐小时总出力 MW（机型出力按配置容量等比缩放后叠加）。

    Args:
        wind (pd.Series): 地市风速场景序列。
        temp_k (pd.Series): 地面 2 米温度序列 (K)。
        pressure_pa (pd.Series): 地面气压序列 (Pa)。
        city_config (dict): 地市配置（capacity_mw 与 turbines 列表）。
        loss_factor (float): 综合损耗系数。

    Returns:
        np.ndarray: 逐小时地市总出力 MW。
    """
    turbines = city_config["turbines"]
    if not turbines:
        raise ValueError("未配置turbines")
    capacity = float(city_config["capacity_mw"])
    configured = sum(float(t["count"]) * float(t["rated_power_mw"]) for t in turbines)
    scale = capacity / max(configured, 1e-9)
    w = wind.to_numpy(float)
    t = temp_k.to_numpy(float)
    p = pressure_pa.to_numpy(float)
    power = np.zeros(len(w))
    for turbine in turbines:
        fraction = turbine_fraction(w, t, p, turbine, float(turbine.get("wind_shear_exponent", 0.15)))
        fraction = fraction * float(turbine.get("efficiency", 1.0))
        power += float(turbine["count"]) * float(turbine["rated_power_mw"]) * scale * fraction
    return power * float(loss_factor)


def compute_power(
    scenarios: dict[str, dict[str, pd.Series]],
    temp_by_city: dict[str, pd.Series],
    pressure_by_city: dict[str, pd.Series],
    city_configs: dict,
    target_index: pd.DatetimeIndex,
    loss_factor: float = 1.0,
    output_city_power: bool = True,
) -> dict[str, pd.DataFrame]:
    """逐资源年汇总全省出力表，列结构与旧版 onshore_wind_power 输出一致。

    Args:
        scenarios (dict[str, dict[str, pd.Series]]): 资源年类型 → 地市键 → 风速场景。
        temp_by_city (dict[str, pd.Series]): 地市键 → 温度序列 (K)。
        pressure_by_city (dict[str, pd.Series]): 地市键 → 气压序列 (Pa)。
        city_configs (dict): 全部地市配置。
        target_index (pd.DatetimeIndex): 输出年时间轴。
        loss_factor (float): 综合损耗系数。
        output_city_power (bool): 是否在输出表中附带各地市出力列。

    Returns:
        dict[str, pd.DataFrame]: 资源年类型 → 全省出力表。
    """
    capacities = {city: float(cfg["capacity_mw"]) for city, cfg in city_configs.items()}
    total_capacity = sum(capacities.values())
    weights = pd.Series(capacities, dtype=float) / total_capacity

    result = {}
    for case, city_scenarios in scenarios.items():
        power = np.zeros(len(target_index))
        wind_weighted = np.zeros(len(target_index))
        city_power: dict[str, np.ndarray] = {}
        for city, scenario in city_scenarios.items():
            city_power[city] = compute_city_power(
                scenario, temp_by_city[city], pressure_by_city[city], city_configs[city], loss_factor
            )
            power += city_power[city]
            wind_weighted += float(weights.get(city, 0.0)) * scenario.to_numpy(float)
        out = pd.DataFrame(
            {
                "time": target_index,
                "province_wind_speed_m_s": wind_weighted,
                "province_power_mw": power,
                "province_power_pu": power / total_capacity,
            }
        )
        if output_city_power:
            for city, values in city_power.items():
                out[f"{city}_power_mw"] = values
        result[case] = out
    return result
