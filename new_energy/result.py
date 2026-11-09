#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : result.py

出力结果对象（风/光各自全流程结果 + 共用物理出力结果，D-45）：
物理模型本体分别内联在 wind.py / solar.py 的 Physics 子模块；
province/region 统一称 total（D-29 单元口径）。
输出表组装（列集、列名、行序）在业务层 biz.export。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# 出力结果对象
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PowerResult:
    """出力结果对象（风/光共用）：各地市出力、跨单元总出力 MW/pu。"""

    index: pd.DatetimeIndex                 # 时间轴（输出年轴，闰年 8784 小时）
    city_power: dict[str, np.ndarray]       # 单元键 → 逐小时出力 MW
    total_power_mw: np.ndarray              # 所配置单元出力合计（MW）
    total_power_pu: np.ndarray              # 所配置单元出力合计（pu）
    weighted_wind_speed_m_s: np.ndarray | None = None   # 容量加权平均风速（m/s，仅风电）
    city_dni: dict[str, np.ndarray] | None = None # 20261109新增：光伏DNI
    total_dni: np.ndarray | None = None # 20261109新增：光伏DNI


@dataclass
class WindResult:
    """风电全流程输出结果：三类场景、总出力结果对象、资源年分类与评估指标。"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 单元键 → 输出年时间轴的场景
    power: dict[str, PowerResult]                   # case → 出力结果对象
    classes: dict[str, np.ndarray] = field(default_factory=dict)     # 单元键 → 各年资源年类型标签 (年数, )
    metrics: pd.DataFrame | None = None              # evaluate开启时的指标表


@dataclass
class SolarResult:
    """光伏全流程输出结果：三类场景、总出力结果对象与资源年分类标签。"""

    scenarios: dict[str, dict[str, pd.Series]]      # case → 单元键 → 输出年份时间轴的场景
    power: dict[str, PowerResult]                   # case → 出力结果对象（输出年轴）
    classes: dict[str, np.ndarray] = field(default_factory=dict)     # 单元键 → 各年资源年类型标签 (年数, )
