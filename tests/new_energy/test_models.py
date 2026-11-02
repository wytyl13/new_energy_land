#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : test_models.py

new_energy 算法包合成数据测试：共用件（quantile_map/Align/结果类）+
风/光各子模块（事件库/基础场景/生成器/物理出力）与模型 forward 接线。
不依赖气象大数据（biz 全流程回归在 wind_run_selftest / solar_selftest）。
运行（仓库根目录）: python -m tests.new_energy.test_models
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from new_energy.align import Align
from new_energy.config import SolarComponent, SolarConfig, SolarUnit, Turbine, WindConfig, WindUnit
from new_energy.constants import PHI_MAX, RESOURCE_YEAR_TYPES
from new_energy.result import PowerResult, SolarResult, WindResult
from new_energy.solar import (
    BaseLoss,
    EventDetails as SolarEvents,
    Generator as SolarGenerator,
    Physics as SolarPhysics,
    SolarModel,
    ideal_irradiance,
    weather_climatology,
)
from new_energy.utils.common import quantile_map
from new_energy.utils.time_axis import year_index
from new_energy.wind import (
    BaseScene,
    EventDetails as WindEvents,
    Generator as WindGenerator,
    Physics as WindPhysics,
    WindModel,
)

results: list[tuple[str, str, float]] = []


def _check(name: str, fn) -> None:
    """执行一个用例并记录 PASS/FAIL 与耗时。"""
    start = time.perf_counter()
    try:
        fn()
        results.append((name, "PASS", time.perf_counter() - start))
    except Exception as exc:  # noqa: BLE001 - 测试脚本汇总所有失败
        results.append((name, f"FAIL: {exc}", time.perf_counter() - start))


# ---------------------------------------------------------------- 合成数据
def _utc_hourly(years: tuple[int, ...], values: np.ndarray) -> pd.Series:
    """逐年拼 UTC 口径整点小时轴 Series（tz-naive，同真实数据；values 行序同 years）。"""
    index = pd.DatetimeIndex(
        [ts for year in years for ts in pd.date_range(f"{year}-01-01", periods=8760, freq="h")]
    )
    return pd.Series(values.ravel(), index=index)


def _wind_grid(years: tuple[int, ...], levels: tuple[float, ...], rng: np.random.Generator) -> np.ndarray:
    """合成风速 (年数, 8760)：年周期 + 日周期 + 噪声，逐年基线水平取 levels。"""
    t = np.arange(8760)
    rows = [
        level + 3.0 * np.sin(2 * np.pi * t / 8760.0) + 1.5 * np.sin(2 * np.pi * t / 24.0) + rng.normal(0, 0.5, 8760)
        for level in levels
    ]
    return np.clip(np.vstack(rows), 0.5, None)


WIND_YEARS = (2020, 2021, 2022, 2023, 2024)
WIND_CONFIG = WindConfig(year=2025, source_years=WIND_YEARS, train_years=(2020, 2021, 2022, 2023), axis_year=2024)
SOLAR_YEARS = (2021, 2022, 2023, 2024)
SOLAR_CONFIG = SolarConfig(year=2025, source_years=SOLAR_YEARS, train_years=(2021, 2022, 2023), axis_year=2024)
LAT, LON = 19.9, 110.35  # 海口附近


def _solar_pair(levels: tuple[float, ...], rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """合成辐照 (年数, 8760) 与对应理想辐照：日正弦理想 × 逐年晴空指数（带小扰动防退化）。"""
    axis = year_index(2024, drop_feb29=True)
    ideal_row = ideal_irradiance(axis, LAT, LON)
    ideal = np.vstack([ideal_row] * len(levels))
    kt = np.vstack([np.full(8760, level) + rng.uniform(-0.05, 0.05, 8760) for level in levels])
    return np.clip(ideal * kt, 0.0, None), ideal


# ---------------------------------------------------------------- 共用件
def test_quantile_map() -> None:
    """强度 0 恒等；强度 s 输出 = (1-s)·原值 + s·全映射（凸组合逐位成立）。"""
    rng = np.random.default_rng(7)
    values = rng.normal(10, 3, 500)
    ref = rng.normal(12, 4, 2000)
    np.testing.assert_allclose(quantile_map(values, ref, 0.0), values)
    full = quantile_map(values, ref, 1.0)
    np.testing.assert_allclose(quantile_map(values, ref, 0.3), 0.7 * values + 0.3 * full)
    assert np.isfinite(full).all() and full.shape == values.shape


def test_align() -> None:
    """两年合成 UTC 序列 → 对齐 (2, 8760)、年标签正确、北京时且无 2/29。"""
    rng = np.random.default_rng(3)
    series = _utc_hourly((2022, 2023), rng.uniform(5, 10, (2, 8760)))
    aligned, grid, years = Align((2022, 2023))(series)
    assert grid.shape == (2, 8760) and np.isfinite(grid).all()
    assert years.tolist() == [2022, 2023]
    assert not ((aligned.index.month == 2) & (aligned.index.day == 29)).any()


def test_result_dataclasses() -> None:
    """结果类必填/默认字段：classes 空字典、metrics/风速默认 None。"""
    power = PowerResult(index=year_index(2024, drop_feb29=True), city_power={}, total_power_mw=np.zeros(3), total_power_pu=np.zeros(3))
    assert power.weighted_wind_speed_m_s is None
    wind = WindResult(scenarios={}, power={})
    assert wind.classes == {} and wind.metrics is None
    solar = SolarResult(scenarios={}, power={})
    assert solar.classes == {}


# ---------------------------------------------------------------- 风电子模块
def test_wind_event_details() -> None:
    """埋低风/大风段被扫入明细（类型/时长/月份正确），segment 与源切片一致。"""
    rng = np.random.default_rng(11)
    years3 = (2020, 2021, 2022)
    grid = _wind_grid(years3, (6, 6, 6), rng)
    grid[2, 2190:2200] = 0.0    # 2022 年 4 月（年/日周期峰处）低风段 10h
    grid[2, 6570:6578] = 30.0   # 2022 年 10 月（年/日周期谷处）大风段 8h
    years = np.array(years3)
    labels = np.array(("偏小资源年", "正常资源年", "偏小资源年"))
    events = WindEvents(grid, years, labels)
    assert events.details.shape[1] == 5 and (events.details[:, 3] >= 3).all()
    low = events.details[(events.details[:, 0] == 2022) & (events.details[:, 1] == 0)]
    assert any(start == 2190 and dur == 10 for _, _, start, dur, _ in low), low
    high = events.details[(events.details[:, 0] == 2022) & (events.details[:, 1] == 1)]
    assert any(start == 6570 and dur == 8 for _, _, start, dur, _ in high), high
    row = int(np.flatnonzero((events.details[:, 2] == 2190) & (events.details[:, 1] == 0))[0])
    np.testing.assert_array_equal(events.segment(row), grid[2, 2190:2200])
    assert events.n_case_years("偏小资源年") == 2
    assert row in events.pool("偏小资源年", 4, 0)  # 埋低风段在 (偏小, 4 月, 低风) 格子


def test_wind_base_scene() -> None:
    """基础场景 (8760,) 非负、φ 截到 [0, PHI_MAX]、量级不离谱。"""
    grid = _wind_grid(WIND_YEARS, (5, 6, 7, 8, 9), np.random.default_rng(21))
    scene, phi = BaseScene(WIND_CONFIG)(grid[:4])
    assert scene.shape == (8760,) and (scene >= 0).all()
    assert 0.0 <= phi <= PHI_MAX
    assert 0.1 < scene.mean() < 30.0


def test_wind_generator() -> None:
    """生成器 → (3, 8760) 非负、行序同 RESOURCE_YEAR_TYPES、方案行 8 列键。"""
    grid = _wind_grid(WIND_YEARS, (5, 6, 7, 8, 9), np.random.default_rng(31))
    years = np.array(WIND_YEARS)
    metric = np.mean(grid**3, axis=1)
    p25, p75 = np.quantile(metric[:4], [0.25, 0.75])
    labels = np.where(metric <= p25, "偏小资源年", np.where(metric >= p75, "偏大资源年", "正常资源年"))
    events = WindEvents(grid[:4], years[:4], labels[:4])
    base, _ = BaseScene(WIND_CONFIG)(grid[:4])
    scenes, plans = WindGenerator(WIND_CONFIG)(grid, years, labels, events, base)
    assert scenes.shape == (3, 8760) and (scenes >= 0).all()
    assert len(plans) == 3 and all(isinstance(p, list) for p in plans)
    for rows in plans:
        for row in rows:
            assert set(row) == {"事件编号", "年景类型", "事件类型", "月份", "开始时间", "持续小时", "来源年份", "来源事件编号"}


def test_wind_physics() -> None:
    """功率曲线端点：切入前 0、额定段恒额定×效率×损耗、切出后 0。"""
    unit = WindUnit(
        capacity_mw=100.0,
        turbines=(
            Turbine(
                model="T", count=10, rated_power_mw=10.0, hub_height_m=100.0,
                cut_in_m_s=3.0, rated_m_s=12.0, cut_out_m_s=25.0, efficiency=0.95,
            ),
        ),
    )
    config = WindConfig(year=2025, source_years=WIND_YEARS, train_years=(2020,), axis_year=2024, loss_factor=0.9)
    axis = year_index(2024, drop_feb29=True)
    temp = pd.Series(288.0, index=axis)
    pressure = pd.Series(101325.0, index=axis)
    physics = WindPhysics(config)
    calm = physics({"c": pd.Series(0.0, index=axis)}, temp, pressure, unit)["c"]
    rated = physics({"c": pd.Series(20.0, index=axis)}, temp, pressure, unit)["c"]
    cutout = physics({"c": pd.Series(40.0, index=axis)}, temp, pressure, unit)["c"]
    np.testing.assert_allclose(calm, 0.0)
    np.testing.assert_allclose(rated, 10 * 10 * 1.0 * 0.95 * 0.9)
    np.testing.assert_allclose(cutout, 0.0)


def test_wind_forward() -> None:
    """WindModel 合成 5 年全流程：7 元组、3×8760 场景/出力、出力非负有上界。"""
    rng = np.random.default_rng(41)
    wind = _utc_hourly(WIND_YEARS, _wind_grid(WIND_YEARS, (5, 6, 7, 8, 9), rng))
    axis = year_index(2025, drop_feb29=True)
    temp = pd.Series(288.0, index=axis, name="t")
    pressure = pd.Series(101325.0, index=axis, name="p")
    unit = WindUnit(
        capacity_mw=100.0,
        turbines=(Turbine(model="T", count=10, rated_power_mw=10.0, hub_height_m=100.0, cut_in_m_s=3.0, rated_m_s=12.0, cut_out_m_s=25.0, efficiency=0.95),),
    )
    out = WindModel(WIND_CONFIG).forward("test_风电", wind, temp, pressure, unit)
    assert len(out) == 7 and out[0] == "test_风电"
    _, labels, scenarios, power, n_events, phi, metrics = out
    assert labels.shape == (5,) and set(labels) <= set(RESOURCE_YEAR_TYPES)
    assert list(scenarios) == list(RESOURCE_YEAR_TYPES) and all(len(s) == 8760 for s in scenarios.values())
    assert list(power) == list(RESOURCE_YEAR_TYPES)
    for mw in power.values():
        assert mw.shape == (8760,) and (mw >= 0).all() and mw.max() <= 100.0 * 1.2
    assert isinstance(n_events, int) and isinstance(phi, float) and metrics is None


# ---------------------------------------------------------------- 光伏子模块
def test_ideal_irradiance() -> None:
    """理想辐照：非负、有零（夜间）、夏至日正午量级 >600 W/m2。"""
    axis = year_index(2024, drop_feb29=True)
    ideal = ideal_irradiance(axis, LAT, LON)
    assert ideal.shape == (8760,) and (ideal >= 0).all()
    assert ideal.min() == 0.0
    noon = int(np.flatnonzero((axis.month == 6) & (axis.day == 21) & (axis.hour == 13))[0])
    assert ideal[noon] > 600.0


def test_weather_climatology() -> None:
    """两年相同温度 → 气候态等于原值；整年缺数据报错。"""
    t = np.arange(8760)
    one_year = 280.0 + 10.0 * np.sin(2 * np.pi * t / 8760.0)
    temp = _utc_hourly((2022, 2023), np.vstack([one_year, one_year]))
    clim = weather_climatology(temp, (2022, 2023))
    assert len(clim) == 8760
    assert not ((clim.index.month == 2) & (clim.index.day == 29)).any()
    np.testing.assert_allclose(clim.to_numpy(), one_year, atol=1e-9)
    try:
        weather_climatology(temp, (2030, 2031))  # 所给年份全缺 → 报错
    except ValueError:
        pass
    else:
        raise AssertionError("所给年份全缺数据未报错")


def test_solar_event_details() -> None:
    """埋低晴空指数日被扫入事件明细，segment = 理想−辐照截负。"""
    grid, ideal = _solar_pair((0.8, 0.8, 0.8), np.random.default_rng(51))
    day2 = slice(24, 48)
    grid[1, day2] = ideal[1, day2] * 0.1  # 第 2 年第 2 天白天压低
    years = np.array((2021, 2022, 2023))
    labels = np.array(RESOURCE_YEAR_TYPES)
    events = SolarEvents(SOLAR_CONFIG, grid, ideal, years, labels)
    assert events.details.shape[1] == 4 and (events.details[:, 2] >= SOLAR_CONFIG.min_duration).all()
    hits = events.details[events.details[:, 0] == 2022]
    assert len(hits) >= 1, events.details
    year, start, dur, _ = hits[0]
    r = int(np.flatnonzero(years == year)[0])
    np.testing.assert_allclose(
        events.segment(int(np.flatnonzero(events.details[:, 0] == 2022)[0])),
        np.clip(ideal[r, start : start + dur] - grid[r, start : start + dur], 0.0, None),
    )
    assert int(np.flatnonzero(events.details[:, 0] == 2022)[0]) in events.pool("正常资源年", 1)  # 埋段在 (正常, 1 月) 格子


def test_solar_base_loss() -> None:
    """基础损失 (8760,) 有限、φ 截到 [0, PHI_MAX]。"""
    grid, ideal = _solar_pair((0.7, 0.8, 0.9), np.random.default_rng(61))
    history = pd.Series(grid.ravel(), index=_utc_hourly((2021, 2022, 2023), grid).index + pd.Timedelta(hours=8))
    axis = year_index(2024, drop_feb29=True)
    target_ideal = ideal_irradiance(axis, LAT, LON)
    loss, phi = BaseLoss(SOLAR_CONFIG)(history, ideal, axis, target_ideal)
    assert loss.shape == (8760,) and np.isfinite(loss).all()
    assert 0.0 <= phi <= PHI_MAX


def test_solar_generator() -> None:
    """生成器 → (3, 8760) 非负、夜间（理想≤白天阈值）恒 0。"""
    grid, ideal = _solar_pair((0.7, 0.8, 0.9, 1.0), np.random.default_rng(71))
    years = np.array(SOLAR_YEARS)
    metric = grid.sum(axis=1) / 1000.0
    p25, p75 = np.quantile(metric[:3], [0.25, 0.75])
    labels = np.where(metric <= p25, "偏小资源年", np.where(metric >= p75, "偏大资源年", "正常资源年"))
    events = SolarEvents(SOLAR_CONFIG, grid[:3], ideal[:3], years[:3], labels[:3])
    history = pd.Series(grid[:3].ravel(), index=_utc_hourly((2021, 2022, 2023), grid[:3]).index + pd.Timedelta(hours=8))
    axis = year_index(2024, drop_feb29=True)
    target_ideal = ideal_irradiance(axis, LAT, LON)
    base_loss, _ = BaseLoss(SOLAR_CONFIG)(history, ideal[:3], axis, target_ideal)
    scenes = SolarGenerator(SOLAR_CONFIG)(grid, years, labels, events, base_loss, target_ideal)
    assert scenes.shape == (3, 8760) and (scenes >= 0).all()
    assert (scenes[:, target_ideal <= SOLAR_CONFIG.daylight_threshold] == 0.0).all()


def test_solar_physics() -> None:
    """GHI=0 夜间出力 0；恒定强光出力恒定且有上界。"""
    unit = SolarUnit(
        capacity_mw=50.0,
        latitude=LAT,
        longitude=LON,
        components=(SolarComponent(name="C", count=100, rated_power_mw=0.5, tilt_deg=20.0, azimuth_deg=0.0, noct_c=45.0, gamma_p_per_c=-0.004, efficiency=0.98),),
    )
    physics = SolarPhysics(SOLAR_CONFIG)
    temp_clim = np.full(8760, 298.15)
    night = physics({"c": np.zeros(8760)}, temp_clim, unit)["c"]
    assert np.allclose(night, 0.0)
    noon = physics({"c": np.full(8760, 900.0)}, temp_clim, unit)["c"]
    assert (noon >= 0).all() and noon.max() <= 50.0 * 1.2


def test_solar_forward() -> None:
    """SolarModel 合成 4 年全流程：6 元组、3×8760 场景/出力、出力非负有上界。"""
    grid, ideal = _solar_pair((0.7, 0.8, 0.9, 1.0), np.random.default_rng(81))
    solar = _utc_hourly(SOLAR_YEARS, grid)
    t = np.arange(8760)
    one_year = 295.0 + 8.0 * np.sin(2 * np.pi * t / 8760.0)
    temp = _utc_hourly((2021, 2022, 2023), np.vstack([one_year] * 3))
    unit = SolarUnit(
        capacity_mw=50.0,
        latitude=LAT,
        longitude=LON,
        components=(SolarComponent(name="C", count=100, rated_power_mw=0.5, tilt_deg=20.0, azimuth_deg=0.0, noct_c=45.0, gamma_p_per_c=-0.004, efficiency=0.98),),
    )
    out = SolarModel(SOLAR_CONFIG).forward("test_光伏", solar, temp, unit)
    assert len(out) == 6 and out[0] == "test_光伏"
    _, labels, scenarios, power, n_events, phi = out
    assert labels.shape == (4,) and set(labels) <= set(RESOURCE_YEAR_TYPES)
    assert list(scenarios) == list(RESOURCE_YEAR_TYPES) and all(len(s) == 8760 for s in scenarios.values())
    for mw in power.values():
        assert mw.shape == (8760,) and (mw >= 0).all() and mw.max() <= 50.0 * 1.2
    assert isinstance(n_events, int) and isinstance(phi, float)


def main() -> None:
    """顺序执行全部用例并汇总。"""
    cases = [
        ("quantile_map 性质", test_quantile_map),
        ("Align 对齐", test_align),
        ("结果类默认值", test_result_dataclasses),
        ("风·事件库明细", test_wind_event_details),
        ("风·基础场景", test_wind_base_scene),
        ("风·生成器", test_wind_generator),
        ("风·物理出力", test_wind_physics),
        ("风·forward 全流程", test_wind_forward),
        ("光·理想辐照度", test_ideal_irradiance),
        ("光·温度气候态", test_weather_climatology),
        ("光·事件库明细", test_solar_event_details),
        ("光·基础损失", test_solar_base_loss),
        ("光·生成器", test_solar_generator),
        ("光·物理出力", test_solar_physics),
        ("光·forward 全流程", test_solar_forward),
    ]
    for name, fn in cases:
        _check(name, fn)
        label, status, cost = results[-1]
        print(f"{label}: {status} ({cost:.1f}s)")
    failed = [r for r in results if r[1] != "PASS"]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
