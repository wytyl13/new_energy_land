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
import time
from pathlib import Path

import pandas as pd

from new_energy.config import SolarConfig, WindConfig

from .grids import load_city_series
from .years import resolve_years

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
            输出年温度、输出年气压，北京时间），光 2 项（辐照历史、训练年
            温度，UTC）。
        """
        return self._wind(req, direct_unit) if self.kind == "wind" else self._solar(req, direct_unit)

    __call__ = forward

    def _load(self, req, kind: str, years, direct_unit: str | None):
        """一次格点读取（风速/温/压/辐照共用的参数墙）。"""
        return load_city_series(
            req.data_dir, req.assignment_file, kind, years,
            single_unit=direct_unit, units=set(req.city_configs), prefixes=req.variable_prefixes,
        )

    def _wind(self, req, direct_unit: str | None):
        """风电取数：风速历史序列 + 输出年温压（北京时间口径）。"""
        # 历史年份按数据目录实际内容解析：期望窗口 ∩ 实际存在的年份（wind 单一类型）
        plan = resolve_years(req.data_dir, ("wind",))
        logger.info("[1/6] 读取风速格点并聚合到地市 (%d-%d年)...", plan.source_years[0], plan.source_years[-1])
        history = self._load(req, "wind", plan.source_years, direct_unit)
        logger.info("[1/6] 完成: 共 %d 个地市", len(history))

        # 输出年温压（物理出力用，+8h 转北京时间并对齐输出年整点时轴，缺失处时间插值）
        year = req.scenario.year
        logger.info("[6/6] 物理出力计算: 读取 %d 年温度/气压格点...", year)
        axis = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00:00", freq="h")
        beijing = {
            kind: {
                city: series.shift(8, freq="h").reindex(axis).interpolate(method="time", limit_direction="both")
                for city, series in self._load(req, kind, [year], direct_unit).items()
            }
            for kind in ("temperature", "pressure")
        }

        config = WindConfig(year=year, source_years=plan.source_years, train_years=plan.train_years,
                            axis_year=plan.axis_year, loss_factor=req.loss_factor, evaluate=req.evaluate)
        return config, (history, beijing["temperature"], beijing["pressure"])

    def _solar(self, req, direct_unit: str | None):
        """光伏取数：辐照历史序列 + 训练年温度（UTC 原始口径，气候态换算在算法侧）。"""
        # 辐照度与温度取交集——温度按训练年求气候平均，两者必须同年份可用
        plan = resolve_years(req.data_dir, ("solar", "temperature"))
        logger.info("[1/4] 读取辐照度格点并聚合到地市 (%d-%d年)...", plan.source_years[0], plan.source_years[-1])
        solar_by_city = self._load(req, "solar", plan.source_years, direct_unit)

        # 地面温度用训练年逐小时气候平均值（UTC 原始序列，气候态换算在算法侧）
        logger.info("[3/4] 物理出力计算: 读取 %d-%d 年温度格点求气候平均...",
                    plan.train_years[0], plan.train_years[-1])
        temp_by_city = self._load(req, "temperature", plan.train_years, direct_unit)

        config = SolarConfig(year=req.scenario.year, source_years=plan.source_years,
                             train_years=plan.train_years, axis_year=plan.axis_year,
                             loss_factor=req.loss_factor, albedo=req.albedo)
        return config, (solar_by_city, temp_by_city)


if __name__ == "__main__":
    """冒烟自检：Schema 请求对象 → Reader 取数与算法配置。运行: python -m biz.reader"""
    from .schema import Schema

    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    start = time.perf_counter()
    config, series = Reader("wind")(Schema("wind")(fixtures / "wind_single_city.json"), None)
    history, temp, pressure = series
    print(f"wind: {type(config).__name__} source={config.source_years[0]}-{config.source_years[-1]}"
          f" 单元={list(history)} 风速长度={len(history['460100_海口市'])}"
          f" 温度长度={len(temp['460100_海口市'])} 气压长度={len(pressure['460100_海口市'])}"
          f" 耗时={time.perf_counter() - start:.1f}s")

    start = time.perf_counter()
    config, series = Reader("solar")(Schema("solar")(fixtures / "solar_danzhou.json"), None)
    solar_by_city, temp_by_city = series
    print(f"solar: {type(config).__name__} source={config.source_years[0]}-{config.source_years[-1]}"
          f" 单元={list(solar_by_city)} 辐照长度={len(solar_by_city['460400_儋州市'])}"
          f" 温度长度={len(temp_by_city['460400_儋州市'])}"
          f" 耗时={time.perf_counter() - start:.1f}s")
