#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : test_runner_real.py

Runner 真实数据全流程测试：wind/solar 各跑一个真实请求（forward，写出力 CSV 到
tests/output），核对结果 JSON、CSV 列集行数与数值范围。冻结基准容差回归在
tests/wind_run_selftest / tests/solar_selftest，此处只验执行通路。
运行（仓库根目录）: python -m tests.biz.test_runner_real
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from biz.runner import Runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

FIXTURE_WIND = "tests/fixtures/wind_single_city.json"
FIXTURE_SOLAR = "tests/fixtures/solar_danzhou.json"
WIND_COLUMNS = ["time", "province_wind_speed_m_s", "province_power_mw", "province_power_pu", "460100_海口市_power_mw"]
SOLAR_COLUMNS = ["时间", "460400_儋州市_总出力_MW", "区域总出力_MW", "区域总出力_pu"]

results: list[tuple[str, str, float]] = []


def _check(name: str, fn) -> None:
    """执行一个用例并记录 PASS/FAIL 与耗时。"""
    start = time.perf_counter()
    try:
        fn()
        results.append((name, "PASS", time.perf_counter() - start))
    except Exception as exc:  # noqa: BLE001 - 测试脚本汇总所有失败
        results.append((name, f"FAIL: {exc}", time.perf_counter() - start))


def test_runner_wind_real() -> None:
    """Runner(wind) 真实数据：海口单市全流程写出 CSV → 结果 JSON/列集/数值范围核对。"""
    resp = Runner("wind")(FIXTURE_WIND)
    assert resp["scenario"] == {"year": 2024, "resource_year_type": "正常资源年"}
    assert resp["cities"] == ["460100_海口市"]
    assert resp["total_capacity_mw"] == 22.29
    output = Path(resp["output_file"])
    assert output.exists(), resp["output_file"]
    csv = pd.read_csv(output)
    assert list(csv) == WIND_COLUMNS
    assert len(csv) == 8784  # 输出年 2024 闰年
    for col in ("province_wind_speed_m_s", "province_power_mw", "province_power_pu", "460100_海口市_power_mw"):
        values = csv[col].to_numpy(float)
        assert np.isfinite(values).all() and (values >= 0).all(), col
    assert csv["460100_海口市_power_mw"].max() <= 22.29 * 1.2
    assert list(resp["power_table"]) == WIND_COLUMNS and len(resp["power_table"]["time"]) == 8784


def test_runner_solar_real() -> None:
    """Runner(solar) 真实数据：儋州单市全流程写出 CSV → 结果 JSON/列集/数值范围核对。"""
    resp = Runner("solar")(FIXTURE_SOLAR)
    assert resp["scenario"] == {"year": 2025, "resource_year_type": "正常资源年"}
    assert resp["cities"] == ["460400_儋州市"]
    assert resp["total_capacity_mw"] == 955.21
    output = Path(resp["output_file"])
    assert output.exists(), resp["output_file"]
    csv = pd.read_csv(output)
    assert list(csv) == SOLAR_COLUMNS
    assert len(csv) == 8760  # 输出年 2025 平年
    for col in ("460400_儋州市_总出力_MW", "区域总出力_MW", "区域总出力_pu"):
        values = csv[col].to_numpy(float)
        assert np.isfinite(values).all() and (values >= 0).all(), col
    assert csv["460400_儋州市_总出力_MW"].max() <= 955.21 * 1.2
    assert list(resp["power_table"]) == SOLAR_COLUMNS and len(resp["power_table"]["时间"]) == 8760


def main() -> None:
    """顺序执行真实数据用例并汇总。"""
    for name, fn in (("wind 真实数据全流程", test_runner_wind_real), ("solar 真实数据全流程", test_runner_solar_real)):
        _check(name, fn)
        label, status, cost = results[-1]
        print(f"{label}: {status} ({cost:.1f}s)")
    failed = [r for r in results if r[1] != "PASS"]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
