#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_reader.py

Reader 测试（真实数据 + wind_single_city fixture，海口单市）：
算法配置、风速历史、输出年温压北京时间时轴。运行（仓库根目录）: python -m tests.biz.test_reader
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from new_energy.config import WindConfig

from biz.reader import Reader
from biz.schema import Schema

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wind_single_city.json"


def test_config(config: WindConfig) -> None:
    """算法配置字段与年份方案一致。"""
    assert config.year == 2024
    assert config.source_years == tuple(range(2000, 2026))
    assert config.train_years == tuple(range(2000, 2025))
    assert config.axis_year == 2025


def test_history(history: dict[str, pd.Series]) -> None:
    """风速历史：海口单市，26 年逐时拼接（含 7 个闰日）。"""
    assert list(history) == ["460100_海口市"]
    assert len(history["460100_海口市"]) == 8760 * 26 + 24 * 7


def test_beijing_axis(temp: dict[str, pd.Series], pressure: dict[str, pd.Series]) -> None:
    """输出年温压：北京时间整点 8784 小时（2024 闰年），插值后无缺失。"""
    axis = pd.date_range("2024-01-01", "2024-12-31 23:00:00", freq="h")
    for series in (temp["460100_海口市"], pressure["460100_海口市"]):
        assert series.index.equals(axis)
        assert not series.isna().any()


def main() -> None:
    """真实数据走一次 Reader（wind 分支），依次执行三个用例。"""
    config, (history, temp, pressure) = Reader("wind")(Schema("wind")(FIXTURE), None)
    for test, args in ((test_config, (config,)), (test_history, (history,)), (test_beijing_axis, (temp, pressure))):
        test(*args)
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
