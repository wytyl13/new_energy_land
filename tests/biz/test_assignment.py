#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/09
@Author  : weiyutao
@File    : test_assignment.py

Assignment 四属性测试（真表）。运行（仓库根目录）: python -m tests.biz.test_assignment
"""
from __future__ import annotations

from pathlib import Path

from biz.assignment import Assignment


def test_keys(a: Assignment) -> None:
    """keys：全部合法单元名。"""
    assert len(a.keys) == 426, len(a.keys)
    assert "460400_儋州市" in a.keys


def test_coords(a: Assignment) -> None:
    """coords：单元 → inside 格点坐标列表。"""
    assert len(a.coords("460400_儋州市")) == 5
    assert (19.5, 109.0) in a.coords("460400_儋州市")


def test_centers(a: Assignment) -> None:
    """centers：单元中心 = 格点经纬度均值。"""
    lat, lon = a.centers("460400_儋州市")
    assert abs(lat - 19.6) <= 1e-9 and abs(lon - 109.3) <= 1e-9, (lat, lon)


def test_sea_land(a: Assignment) -> None:
    """land_coords/sea_coords/coord_units：儋州 4 陆 1 海、舟山纯海、海口纯陆。"""
    assert len(a.land_coords("460400_儋州市")) == 4 and len(a.sea_coords("460400_儋州市")) == 1
    assert a.sea_coords("330900_舟山市") and not a.land_coords("330900_舟山市")
    assert a.land_coords("460100_海口市") and not a.sea_coords("460100_海口市")
    assert "460400_儋州市" in a.coord_units


def main() -> None:
    """构造真表，依次执行四个属性测试。"""
    a = Assignment(Path(__file__).resolve().parents[2] / "data" / "全国格点地市归属.csv")
    for test in (test_keys, test_coords, test_centers, test_sea_land):
        test(a)
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
