#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : constants.py

算法冻结常数。
"""
from __future__ import annotations

RANDOM_SEED = 20261008  # 场景生成随机种子（风电/光伏共用同一复现口径）
RESOURCE_YEAR_TYPES = ("偏小资源年", "正常资源年", "偏大资源年")

GPR_CHUNK_SIZE = 3000    # GPR 预测分块行数，防一次性构造过大核矩阵
PHI_MAX = 0.97           # AR(1) 自相关系数上限
RAMP_HOURS = 3           # 事件插入前后坡度过渡小时数

RHO_REF = 1.225     # 参考空气密度（kg/m3）
GAMMA = 0.0065      # 温度直减率（K/m）
GRAVITY = 9.80665   # 重力加速度（m/s2）
R_D = 287.05        # 干空气气体常数（J/(kg·K)）
