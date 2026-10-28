#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : constants.py

业务层常量：CSV 编码、历史口径默认值、数据类型 → 格点列默认前缀。
"""
from __future__ import annotations

ENC = "utf-8-sig"  # 全部 CSV 读写编码

# 服务端历史口径默认值（扩历史范围时改这里）
DEFAULT_YEARS = (2000, 2025)  # 历史数据期望窗口，两端年份均包含；数据超出窗口不纳入
MIN_HISTORY_YEARS = 12  # 历史年份下限，低于此值报错（三类资源年各需足够样本）

# kind → 格点列默认前缀（kind 兼作子目录名与文件名中的类型名）
KIND_PREFIX = {
    "wind": "wind_speed_100m",
    "solar": "irradiance_w_m2",
    "temperature": "temperature_2m_k",
    "pressure": "surface_pressure_pa",
}
