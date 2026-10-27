#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : constants.py

算法冻结常数（原 utils/request.py 中算法侧的部分；业务侧默认路径与校验件已迁
biz/request.py，格点读取已迁 biz/assignment.py）。
"""
from __future__ import annotations

RANDOM_SEED = 20261008  # 场景生成随机种子（风电/光伏共用同一复现口径）
RESOURCE_YEAR_TYPES = ("偏小资源年", "正常资源年", "偏大资源年")
