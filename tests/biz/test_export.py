#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_export.py

Export 测试：手工造 WindResult（不走算法）+ fixture 请求，测 CSV 写出、
结果 JSON、power_table 三项。运行（仓库根目录）: python -m tests.biz.test_export
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from new_energy.pipeline import WindResult
from new_energy.power import PowerResult

from biz.export import Export
from biz.schema import Schema

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wind_single_city.json"
COLUMNS = ["time", "province_wind_speed_m_s", "province_power_mw", "province_power_pu",
           "460100_海口市_power_mw"]


def test_csv_written(resp: dict) -> None:
    """CSV 写出，列名列序为冻结约定。"""
    columns = pd.read_csv(Path(resp["output_file"]), nrows=0).columns
    assert list(columns) == COLUMNS


def test_result_json(resp: dict) -> None:
    """结果 JSON 的景型、单元与装机。"""
    assert resp["scenario"] == {"year": 2024, "resource_year_type": "正常资源年"}
    assert resp["cities"] == ["460100_海口市"]
    assert resp["total_capacity_mw"] == 22.29


def test_power_table(resp: dict) -> None:
    """power_table 与 CSV 同列同值，行数 = 时间轴长度。"""
    assert list(resp["power_table"]) == COLUMNS
    assert resp["power_table"]["province_power_mw"] == [10.0, 20.0, 30.0]


def main() -> None:
    """手工造 3 小时结果走一次 Export，依次执行三个用例。"""
    city = "460100_海口市"
    index = pd.date_range("2024-01-01", periods=3, freq="h")
    result = WindResult(
        scenarios={"正常资源年": {city: pd.Series([1.0, 1.0, 1.0], index=index)}},
        power={"正常资源年": PowerResult(
            index=index,
            city_power={city: np.array([10.0, 20.0, 30.0])},
            total_power_mw=np.array([10.0, 20.0, 30.0]),
            total_power_pu=np.array([1.0, 1.0, 1.0]),
            weighted_wind_speed_m_s=np.array([5.0, 6.0, 7.0]),
        )},
    )
    resp = Export("wind", Schema("wind")(FIXTURE))(result, time.perf_counter())
    for test in (test_csv_written, test_result_json, test_power_table):
        test(resp)
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
