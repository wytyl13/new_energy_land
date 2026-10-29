#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_grids.py

load_city_series 测试（真实数据 2024 单年）：归属表路径、未命中单元路径、
范围外单元报错。运行（仓库根目录）: python -m tests.biz.test_grids
"""
from __future__ import annotations

from pathlib import Path

from biz.grids import GridData

GRID_DIR = Path(__file__).resolve().parents[2] / "data" / "hainan_era5_grid_csv"
ASSIGNMENT = Path(__file__).resolve().parents[2] / "data" / "全国格点地市归属.csv"


def test_city_series() -> None:
    """归属表路径：海口按表内坐标取列聚合，8784 小时无缺失。"""
    grids = GridData(GRID_DIR, ASSIGNMENT, units={"460100_海口市"})
    series = grids("wind", [2024])
    assert series["460100_海口市"].index.is_monotonic_increasing
    assert len(series["460100_海口市"]) == 8784
    assert not series["460100_海口市"].isna().any()


def test_single_unit_series() -> None:
    """未命中单元路径：取声明前缀全部格点列均值，键名与长度不变。"""
    grids = GridData(GRID_DIR, ASSIGNMENT)
    series = grids("wind", [2024], single_unit="test_自定义区域")
    assert list(series) == ["test_自定义区域"]
    assert len(series["test_自定义区域"]) == 8784
    assert not series["test_自定义区域"].isna().any()


def test_missing_unit_error() -> None:
    """范围外单元：配置了但归属表没有的键报错（数值红线）。"""
    grids = GridData(GRID_DIR, ASSIGNMENT, units={"999999_不存在市"})
    try:
        grids("wind", [2024])
    except ValueError as exc:
        assert "999999_不存在市" in str(exc), exc
    else:
        raise AssertionError("范围外单元未报错")


def main() -> None:
    """依次执行三个用例。"""
    for test in (test_city_series, test_single_unit_series, test_missing_unit_error):
        test()
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
