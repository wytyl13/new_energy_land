#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : power.py

步6：物理出力模型。

风：轮毂高度切变外推、温度气压修正空气密度、分段立方功率曲线。
光：水平面 → 组件倾斜面辐照度（POA）、NOCT 组件温度、功率温度系数。
物理模型风/光不同，各写各的阶段类；只共用结果对象 PowerResult
（跨单元汇总属计算留算法，D-45；province/region 统一称 total，D-29 单元口径）。
输出表组装（列集、列名、行序）在业务层 biz.runner。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SolarConfig, SolarUnit, Turbine, WindConfig, WindUnit
from .constants import GAMMA, GRAVITY, R_D, RHO_REF
from .utils.time_axis import no_feb29, year_index

CLIMATOLOGY_INDEX = year_index(2025)[no_feb29(year_index(2025))]  # 8760 小时非闰年轴


@dataclass
class PowerResult:
    """出力结果对象（风/光共用）：各地市出力、跨单元总出力 MW/pu。"""

    index: pd.DatetimeIndex                 # 时间轴（风=输出年轴；光=场景轴年，由 pipeline 重排）
    city_power: dict[str, np.ndarray]       # 单元键 → 逐小时出力 MW
    total_power_mw: np.ndarray              # 所配置单元出力合计（MW）
    total_power_pu: np.ndarray              # 所配置单元出力合计（pu）
    weighted_wind_speed_m_s: np.ndarray | None = None   # 容量加权平均风速（m/s，仅风电）


def weather_climatology(series: pd.Series, years: tuple[int, ...]) -> pd.Series:
    """把逐年温度/气压序列按位置平均成 8760 小时气候态（删闰日、UTC+8）。

    Args:
        series (pd.Series): biz.assignment 返回的地市序列（UTC 时间索引，多年拼接）。
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


class WindPowerStage:
    """风电物理出力：场景风速 + 输出年温压 + 机组配置 → 各地市与总出力。"""

    def __init__(self, config: WindConfig):
        """记录输出年时间轴与损耗系数。

        Args:
            config (WindConfig): 风电算法配置。
        """
        self.target_index = year_index(config.year)
        self.loss_factor = config.loss_factor

    def __call__(
        self,
        scenarios: dict[str, dict[str, pd.Series]],
        temp_by_city: dict[str, pd.Series],
        pressure_by_city: dict[str, pd.Series],
        city_configs: dict[str, WindUnit],
    ) -> dict[str, PowerResult]:
        """逐资源年计算总出力结果对象（表组装在业务层）。

        Args:
            scenarios (dict[str, dict[str, pd.Series]]): 资源年类型 → 单元键 → 风速场景（输出年轴）。
            temp_by_city (dict[str, pd.Series]): 单元键 → 输出年温度序列 (K)。
            pressure_by_city (dict[str, pd.Series]): 单元键 → 输出年气压序列 (Pa)。
            city_configs (dict[str, WindUnit]): 单元键 → 装机配置对象。

        Returns:
            dict[str, PowerResult]: 资源年类型 → 出力结果对象。
        """
        capacities = {city: unit.capacity_mw for city, unit in city_configs.items()}
        total_capacity = sum(capacities.values())
        weights = pd.Series(capacities, dtype=float) / total_capacity

        result = {}
        for case, city_scenarios in scenarios.items():
            power = np.zeros(len(self.target_index))
            wind_weighted = np.zeros(len(self.target_index))
            city_power: dict[str, np.ndarray] = {}
            for city, scenario in city_scenarios.items():
                city_power[city] = self._compute_city_power(
                    scenario, temp_by_city[city], pressure_by_city[city], city_configs[city]
                )
                power += city_power[city]
                wind_weighted += float(weights.get(city, 0.0)) * scenario.to_numpy(float)
            result[case] = PowerResult(
                index=self.target_index,
                city_power=city_power,
                total_power_mw=power,
                total_power_pu=power / total_capacity,
                weighted_wind_speed_m_s=wind_weighted,
            )
        return result

    def _turbine_fraction(
        self, wind: np.ndarray, temp_k: np.ndarray, pressure_pa: np.ndarray, turbine: Turbine
    ) -> np.ndarray:
        """计算单机型出力占额定出力的比例（0~1），含轮毂高度切变与空气密度修正。

        Args:
            wind (np.ndarray): 100 米高度风速序列 (m/s)。
            temp_k (np.ndarray): 地面 2 米温度序列 (K)。
            pressure_pa (np.ndarray): 地面气压序列 (Pa)。
            turbine (Turbine): 单机型配置（轮毂高度、切入/额定/切出风速、切变指数等）。

        Returns:
            np.ndarray: 逐小时出力比例（0~1）。
        """
        hub = turbine.hub_height_m
        vin = turbine.cut_in_m_s
        vr = turbine.rated_m_s
        vout = turbine.cut_out_m_s
        alpha = turbine.wind_shear_exponent
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

    def _compute_city_power(
        self,
        wind: pd.Series,
        temp_k: pd.Series,
        pressure_pa: pd.Series,
        unit: WindUnit,
    ) -> np.ndarray:
        """计算一个单元的逐小时总出力 MW（机型出力按配置容量等比缩放后叠加）。

        Args:
            wind (pd.Series): 单元风速场景序列。
            temp_k (pd.Series): 地面 2 米温度序列 (K)。
            pressure_pa (pd.Series): 地面气压序列 (Pa)。
            unit (WindUnit): 单元装机配置对象。

        Returns:
            np.ndarray: 逐小时单元总出力 MW。
        """
        configured = sum(t.count * t.rated_power_mw for t in unit.turbines)
        scale = unit.capacity_mw / max(configured, 1e-9)
        w = wind.to_numpy(float)
        t = temp_k.to_numpy(float)
        p = pressure_pa.to_numpy(float)
        power = np.zeros(len(w))
        for turbine in unit.turbines:
            fraction = self._turbine_fraction(w, t, p, turbine) * turbine.efficiency
            power += turbine.count * turbine.rated_power_mw * scale * fraction
        return power * self.loss_factor


class SolarPowerStage:
    """光伏物理出力：辐照度场景 + 训练年温度气候态 + 组件配置 → 各地市与总出力。"""

    def __init__(self, config: SolarConfig):
        """记录场景轴年时间轴、损耗系数与反照率。

        Args:
            config (SolarConfig): 光伏算法配置。
        """
        self.target_index = year_index(config.axis_year)[no_feb29(year_index(config.axis_year))]
        self.loss_factor = config.loss_factor
        self.albedo = config.albedo

    def __call__(
        self,
        scenarios: dict[str, dict[str, pd.Series]],
        temp_by_city: dict[str, pd.Series],
        city_configs: dict[str, SolarUnit],
    ) -> dict[str, PowerResult]:
        """把三类辐照度场景转为总出力结果对象（表组装在业务层）。

        Args:
            scenarios (dict[str, dict[str, pd.Series]]): 资源年类型 → 单元键 → 辐照度场景（场景轴年）。
            temp_by_city (dict[str, pd.Series]): 单元键 → 地面温度气候态（K）。
            city_configs (dict[str, SolarUnit]): 单元键 → 装机与组件配置对象。

        Returns:
            dict[str, PowerResult]: 资源年类型 → 出力结果对象（场景轴年时间轴）。
        """
        total_cap = sum(unit.capacity_mw for unit in city_configs.values())
        power: dict[str, PowerResult] = {}
        for case, city_scenarios in scenarios.items():
            region_total = np.zeros(len(self.target_index))
            city_power: dict[str, np.ndarray] = {}
            for city, unit in city_configs.items():
                ghi = city_scenarios[city].to_numpy(float)
                temp_c = temp_by_city[city].to_numpy(float) - 273.15
                configured = sum(c.count * c.rated_power_mw for c in unit.components)
                scale = unit.capacity_mw / max(configured, 1e-9)
                city_total = np.zeros(len(self.target_index))
                for comp in unit.components:
                    params = {
                        "latitude": unit.latitude,
                        "longitude": unit.longitude,
                        "albedo": self.albedo,
                        "tilt_deg": comp.tilt_deg,
                        "azimuth_deg": comp.azimuth_deg,
                    }
                    g = self._poa(ghi, self.target_index, params)
                    tc = temp_c + (comp.noct_c - 20) / 800 * g
                    p = (
                        comp.count * comp.rated_power_mw * scale * self.loss_factor
                        * comp.efficiency
                        * np.clip(g / 1000 * (1 + comp.gamma_p_per_c * (tc - 25)), 0, 1.2)
                    )
                    city_total += p
                city_power[city] = city_total
                region_total += city_total
            power[case] = PowerResult(
                index=self.target_index,
                city_power=city_power,
                total_power_mw=region_total,
                total_power_pu=region_total / total_cap,
            )
        return power

    def _poa(self, ghi: np.ndarray, index: pd.DatetimeIndex, params: dict) -> np.ndarray:
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
