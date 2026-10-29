#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : export.py

业务层写出（D-45/D-51，Runner 末环）：算法结果对象 → 冻结列集出力表 →
写 CSV → 结果 JSON。输出表的列集、列名、行序是冻结对外约定，改动先记 DECISIONS。
"""
from __future__ import annotations

import logging
import time

import pandas as pd
from new_energy.power import PowerResult
from .constants import ENC
from .schema import RequestBase

logger = logging.getLogger(__name__)


class Export:
    """出力表写出与结果 JSON（列集列序为冻结对外约定）。

    Attributes:
        kind (str): 算法类型（wind/solar）。
        req (RequestBase): Schema 输出的请求对象（取输出路径/年份/景型/装机）。
    """

    def __init__(self, kind: str, req: RequestBase):
        """记录算法类型与请求对象。

        Args:
            kind (str): 算法类型（wind/solar）。
            req (RequestBase): Schema 输出的请求对象。
        """
        self.kind = kind
        self.req = req

    def forward(self, result, start: float) -> dict:
        """选景出表、写 CSV、组装结果 JSON。

        Args:
            result (WindResult | SolarResult): 算法侧结果对象（scenarios/power）。
            start (float): 全流程起始 perf_counter（耗时统计用）。

        Returns:
            dict: {"output_file", "elapsed_s", "scenario", "cities",
                   "total_capacity_mw", "power_table"}，power_table 为列名→数组的
            字典，与 CSV 同列同值。
        """
        case = self.req.scenario.resource_year_type
        power = result.power[case]
        if self.kind == "wind":
            table = self._wind_table(power)
            out_path = self.req.output_dir / f"onshore_wind_power_{self.req.scenario.year}_{case}.csv"
        else:
            table = self._solar_table(power)
            out_path = self.req.output_dir / f"pv_power_{self.req.scenario.year}_{case}.csv"
        self.req.output_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_path, index=False, encoding=ENC)
        logger.info("%s出力全流程完成: 耗时 %.1f 秒",
                    "风电" if self.kind == "wind" else "光伏", time.perf_counter() - start)

        time_col = "time" if self.kind == "wind" else "时间"
        table = table.copy()
        table[time_col] = table[time_col].astype(str)
        return {
            "output_file": str(out_path),
            "elapsed_s": round(time.perf_counter() - start, 2),
            "scenario": {"year": self.req.scenario.year, "resource_year_type": case},
            "cities": list(next(iter(result.scenarios.values()))),
            "total_capacity_mw": round(sum(c.capacity_mw for c in self.req.city_configs.values()), 6),
            "power_table": table.to_dict("list"),
        }

    __call__ = forward

    def _wind_table(self, result: PowerResult) -> pd.DataFrame:
        """风电结果对象 → 冻结列集出力表（time、省风速、省出力 MW/pu、{地市}_power_mw）。"""
        out = pd.DataFrame(
            {
                "time": result.index,
                "province_wind_speed_m_s": result.weighted_wind_speed_m_s,
                "province_power_mw": result.total_power_mw,
                "province_power_pu": result.total_power_pu,
            }
        )
        if self.req.output_city_power:
            for city, values in result.city_power.items():
                out[f"{city}_power_mw"] = values
        return out

    def _solar_table(self, result: PowerResult) -> pd.DataFrame:
        """光伏结果对象 → 冻结列集出力表（时间、{地市}_总出力_MW、区域总出力 MW/pu）。"""
        data: dict = {"时间": result.index}
        if self.req.output_city_power:
            for city, values in result.city_power.items():
                data[f"{city}_总出力_MW"] = values
        data["区域总出力_MW"] = result.total_power_mw
        data["区域总出力_pu"] = result.total_power_pu
        return pd.DataFrame(data)
