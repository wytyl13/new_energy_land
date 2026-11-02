#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : common.py

风/光共用的零散工具：进程池子进程日志配置、经验分位映射。
"""
from __future__ import annotations

import logging

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# 共用工具（进程池日志 / 分位映射）
# ─────────────────────────────────────────────────────────────────────────────

def worker_logging() -> None:
    """进程池子进程首次执行时配置日志（spawn 不继承父进程 handler）。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


def quantile_map(values: np.ndarray, ref: np.ndarray, strength: float) -> np.ndarray:
    """经验分位映射：排序位替换为参考分位数后按强度混合（时间位置不动）。

    Args:
        values (np.ndarray): 待映射序列 (m, )。
        ref (np.ndarray): 参考分布样本。
        strength (float): 映射混合强度（0~1）。

    Returns:
        np.ndarray: 映射后的序列 (m, )。
    """
    # 1. 排序位次 → 各位置经验概率
    order = np.argsort(values, kind="mergesort") # (M, ): [1, 3, 0, 2, ...] 从小到大座次，M位当月小时数
    probabilities = (np.arange(len(values)) + 0.5) / len(values) # (M, ): [0.125, 0.375, 0.625, 0.875, ...] 从小到大座次分位数
    # 2. 按概率取参考分位，写回原时间位置
    mapped = np.empty(len(values), dtype=float)
    mapped[order] = np.quantile(ref, probabilities) # (M, ): 左侧从小到大座次赋值，右侧从小分位数到大分位数座次取值
    # 3. 与原值按强度混合
    return (1.0 - strength) * values + strength * mapped
