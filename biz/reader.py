#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : reader.py

取数编排（D-51，Runner 第 2 环）：请求对象 → 历史年份方案（years）、
格点序列读取（grids，含单元范围核对）、时轴换算，构造 WindConfig/SolarConfig，
返回 (算法配置, 模型输入序列元组)。

时间保持原始 UTC 口径，+8 转北京时间只在风电输出年温压处（算法侧
build_history 另按各自流程处理）。
"""
from __future__ import annotations

import logging

import pandas as pd

from new_energy.config import SolarConfig, WindConfig

from .grids import GridData
from .schema import RequestBase
from .years import YearsPlan

logger = logging.getLogger(__name__)


class Reader:
    """取数与算法配置构造：数据目录 → 内存序列 + WindConfig/SolarConfig。

    Attributes:
        kind (str): 算法类型（wind/solar）。
    """

    def __init__(self, kind: str):
        """记录算法类型。

        Args:
            kind (str): 算法类型（wind/solar）。
        """
        self.kind = kind

    def forward(self, req, direct_unit: str | None):
        """请求对象 → (算法配置, 模型输入序列元组)。

        Args:
            req (RequestBase): Schema 输出的请求对象（biz.schema）。
            direct_unit (str | None): 未命中归属表的区域单元名（D-29，由 Runner 匹配得出）。

        Returns:
            tuple: (WindConfig | SolarConfig, 序列元组)——风 3 项（风速历史、
            输出年温度、输出年气压，北京时间），光 2 项（辐照历史、训练年温度，UTC）。
        """
        return self._wind(req, direct_unit) if self.kind == "wind" else self._solar(req, direct_unit)

    __call__ = forward

    def _wind(self, req: RequestBase, direct_unit: str | None):
        """风电取数：风速历史序列 + 输出年温压（北京时间口径）。"""
        grids = GridData(req.data_dir, req.assignment_file, units=set(req.city_configs),
                         prefixes=req.variable_prefixes)

        # 1. 历史年份方案：期望窗口 ∩ 实际存在的年份（wind 单一类型）
        plan = YearsPlan(req.data_dir, ("wind",))
        logger.info("[1/6] 读取风速格点并聚合到地市 (%d-%d年)...", plan.source_years[0], plan.source_years[-1])
        history = grids("wind", plan.source_years, direct_unit)
        logger.info("[1/6] 完成: 共 %d 个地市", len(history))

        # 2. 输出年温压：+8h 转北京时间、对齐输出年整点、缺失处时间插值
        year = req.scenario.year
        logger.info("[6/6] 物理出力计算: 读取 %d 年温度/气压格点...", year)
        axis = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")
        temp_by_city = grids("temperature", [year], direct_unit)
        pressure_by_city = grids("pressure", [year], direct_unit)
        for by_city in (temp_by_city, pressure_by_city):
            for city, series in by_city.items():
                by_city[city] = series.shift(8, freq="h").reindex(axis).interpolate(
                    method="time", limit_direction="both")

        # 3. 算法配置
        config = WindConfig(year=year, source_years=plan.source_years, train_years=plan.train_years,
                            axis_year=plan.axis_year, loss_factor=req.loss_factor, evaluate=req.evaluate)
        return config, (history, temp_by_city, pressure_by_city)

    def _solar(self, req, direct_unit: str | None):
        """光伏取数：辐照历史序列 + 训练年温度（UTC 原始口径，气候态换算在算法侧）。"""
        grids = GridData(req.data_dir, req.assignment_file, units=set(req.city_configs),
                         prefixes=req.variable_prefixes)

        # 1. 历史年份方案：辐照度与温度取交集——温度按训练年求气候平均，两者必须同年份可用
        plan = YearsPlan(req.data_dir, ("solar", "temperature"))
        logger.info("[1/4] 读取辐照度格点并聚合到地市 (%d-%d年)...", plan.source_years[0], plan.source_years[-1])
        solar_by_city = grids("solar", plan.source_years, direct_unit)

        # 2. 地面温度用训练年逐小时气候平均值（UTC 原始序列，气候态换算在算法侧）
        logger.info("[3/4] 物理出力计算: 读取 %d-%d 年温度格点求气候平均...",
                    plan.train_years[0], plan.train_years[-1])
        temp_by_city = grids("temperature", plan.train_years, direct_unit)

        # 3. 算法配置
        config = SolarConfig(year=req.scenario.year, source_years=plan.source_years,
                             train_years=plan.train_years, axis_year=plan.axis_year,
                             loss_factor=req.loss_factor, albedo=req.albedo)
        return config, (solar_by_city, temp_by_city)
