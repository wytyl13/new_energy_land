#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29 10:56
@Author  : weiyutao
@File    : test_years.py

YearsPlan 三属性测试（真实数据 data/hainan_era5_grid_csv，默认窗口 2000-2025，
2026 年在窗口外不纳入）。运行（仓库根目录）: python -m tests.biz.test_years
"""
from __future__ import annotations

from pathlib import Path

from biz.years import YearsPlan


def test_source_years(plan: YearsPlan) -> None:
    """实际使用的历史年份 = 窗口内全部年。"""
    assert plan.source_years == tuple(range(2000, 2026))


def test_train_years(plan: YearsPlan) -> None:
    """训练年 = 去最后一年。"""
    assert plan.train_years == tuple(range(2000, 2025))


def test_axis_year(plan: YearsPlan) -> None:
    """轴年 = 最后一年。"""
    assert plan.axis_year == 2025


def main() -> None:
    """真实 wind 数据构造一次，依次执行三个属性测试。"""
    plan = YearsPlan(Path(__file__).resolve().parents[2] / "data" / "hainan_era5_grid_csv", ("wind",))
    for test in (test_source_years, test_train_years, test_axis_year):
        test(plan)
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
