#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : config.py

算法侧配置类（D-46/D-50）：数值范围校验在各配置类 __post_init__，
构造失败即校验失败；业务层 biz.schema 直接用 JSON 键构造这些类
（未知/缺字段由 __init__ 抛 TypeError），算法包不知道请求 JSON 长什么样。
本文件集中"可调超参"（带冻结默认值，改默认值须过基准回归）；
固定口径（随机种子、物理常数）在 constants.py。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindConfig:
    """风电算法配置：年份方案、装机损耗、评估开关与场景生成调参。"""

    year: int                          # 输出年份（场景重排到该年，闰年 8784 小时）
    source_years: tuple[int, ...]      # 实际使用的历史年份（两端含）
    train_years: tuple[int, ...]       # 训练统计用年份 = source_years 除最后一年
    axis_year: int                     # 场景轴年份 = source_years 最后一年（只当日历模板）
    loss_factor: float = 1.0           # 综合损耗系数
    evaluate: bool = False             # 是否计算场景评估指标
    gpr_sample_size: int = 800             # GPR 训练抽样点数
    soft_quantile_strength: float = 1.0    # 事件插入前逐月分位数校准强度（0~1）
    global_mapping_strength: float = 0.50  # 事件插入后全局经验分位数映射强度（0~1）
    mean_pullback: float = 0.30            # 事件插入后向同类历史均值回拉比例

    def __post_init__(self):
        if not isinstance(self.evaluate, bool):
            raise ValueError(f"evaluate 必须是布尔值")
        if isinstance(self.loss_factor, bool) or not isinstance(self.loss_factor, (int, float)):
            raise ValueError(f"loss_factor 必须是数字")


@dataclass(frozen=True)
class SolarConfig:
    """光伏算法配置：年份方案、损耗系数、反照率与场景生成/事件调参。"""

    year: int                          # 输出年份
    source_years: tuple[int, ...]      # 实际使用的历史年份（两端含）
    train_years: tuple[int, ...]       # 训练统计用年份 = source_years 除最后一年
    axis_year: int                     # 场景轴年份 = source_years 最后一年（只当日历模板）
    loss_factor: float = 1.0           # 汇集、逆变及线路等综合损耗系数
    albedo: float = 0.2                # 地面反照率
    gpr_sample_size: int = 900             # GPR 训练抽样点数
    daylight_threshold: float = 50.0       # 理想辐照度超过该值视为有效白天（W/m2）
    quantile_blend: float = 0.45           # 分位数校准混合强度（0~1）
    event_ideal_threshold: float = 200.0   # 核心日照时段阈值：仅理想辐照度超过该值时识别事件（W/m2）
    kt_quantile: float = 0.10              # 晴空指数分位阈值
    kt_upper: float = 1.2                  # 晴空指数上限（数据噪声截断）
    min_duration: int = 3                  # 事件最短持续小时数

    
    def __post_init__(self) -> None:
        # bool 是 int 的子类，须先单独排除
        if isinstance(self.loss_factor, bool) or not isinstance(self.loss_factor, (int, float)):
            raise ValueError("loss_factor 必须是数字")
        if isinstance(self.albedo, bool) or not isinstance(self.albedo, (int, float)):
            raise ValueError(f"albedo 必须是数字")
        


@dataclass(frozen=True)
class Turbine:
    """风机机型物理参数（功率曲线输入，D-50）。"""

    model: str
    count: int
    rated_power_mw: float
    hub_height_m: float
    cut_in_m_s: float
    rated_m_s: float
    cut_out_m_s: float
    efficiency: float = 1.0
    wind_shear_exponent: float = 0.15

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("turbines.count 必须是正整数")
        if self.rated_power_mw <= 0:
            raise ValueError("turbines.rated_power_mw 必须为正")


@dataclass(frozen=True)
class SolarComponent:
    """光伏组件物理参数（POA 与组件温度模型输入，D-50）。"""

    name: str
    count: int
    rated_power_mw: float
    tilt_deg: float
    azimuth_deg: float
    noct_c: float
    gamma_p_per_c: float
    efficiency: float = 1.0

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("components.count 必须是正整数")
        if self.rated_power_mw <= 0:
            raise ValueError("components.rated_power_mw 必须为正")


@dataclass(frozen=True)
class WindUnit:
    """风电单元装机配置（键 = 单元名，不含 city 本身）。"""

    capacity_mw: float
    turbines: tuple[Turbine, ...]

    def __post_init__(self) -> None:
        if self.capacity_mw <= 0:
            raise ValueError("capacity_mw 必须为正")
        if not self.turbines:
            raise ValueError("turbines 必须是非空数组")


@dataclass(frozen=True)
class SolarUnit:
    """光伏单元装机配置（键 = 单元名；经纬度可缺省，由业务层按归属表/数据补全）。"""

    capacity_mw: float
    latitude: float | None = None
    longitude: float | None = None
    components: tuple[SolarComponent, ...] = ()

    def __post_init__(self) -> None:
        if self.capacity_mw <= 0:
            raise ValueError("capacity_mw 必须为正")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude/longitude 须同时提供或同时缺省")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("latitude 必须在 [-90, 90]")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("longitude 必须在 [-180, 180]")
        if not self.components:
            raise ValueError("components 必须是非空数组")
