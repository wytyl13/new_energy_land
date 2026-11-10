#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/10
@Author  : weiyutao
@File    : test_structure.py

structure 三属性测试（真表，P10.6）。运行（仓库根目录）: python -m tests.biz.test_structure
"""
from __future__ import annotations

from pathlib import Path

from biz.assignment import Assignment
from biz.structure import structure

PROVINCE = "海南省"


def test_wind_land_city() -> None:
    """风电 city_configs：海口纯陆 4 陆 0 海。"""
    r = structure("wind", PROVINCE)
    cities = {c["city"]: c for c in r["city_configs"]}
    assert cities["460100_海口市"] == {"city": "460100_海口市", "land_grid_count": 4, "sea_grid_count": 0}


def test_wind_sea_city() -> None:
    """风电海结构：儋州 4 陆 1 海，sea_configs.needed 且 grid_count 含其海点。"""
    r = structure("wind", PROVINCE)
    cities = {c["city"]: c for c in r["city_configs"]}
    assert cities["460400_儋州市"] == {"city": "460400_儋州市", "land_grid_count": 4, "sea_grid_count": 1}
    assert r["sea_configs"]["needed"] and r["sea_configs"]["grid_count"] >= 1


def test_solar_all_units_no_sea() -> None:
    """光伏：含该省全部 inside 单元（不分海陆）、无 sea 相关键；儋州 grid_count = 陆+海 5。"""
    r = structure("solar", PROVINCE)
    assert set(r) == {"city_configs"}
    cities = {c["city"]: c["grid_count"] for c in r["city_configs"]}
    assert cities["460400_儋州市"] == 5
    a = Assignment(Path(__file__).resolve().parents[2] / "data" / "全国格点地市归属.csv")
    coord_units = set(a.coord_units)
    expected = [u for u in a.units_of_province(PROVINCE) if u in coord_units]
    assert [c["city"] for c in r["city_configs"]] == expected


def main() -> None:
    """依次执行三个属性测试。"""
    for test in (test_wind_land_city, test_wind_sea_city, test_solar_all_units_no_sea):
        test()
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
