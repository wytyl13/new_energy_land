#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : solar_selftest.py

光伏 pipeline 唯一入口自测：统一 JSON（文件/dict）校验 + 全流程回归。

运行（仓库根目录）:
    python -m tests.solar_selftest

用例：
    1 全流程回归   儋州单市，三类辐照度场景与区域出力均与冻结基准容差一致 + 公共入口双份返回核对
    2 默认值       缺 data_dir/city_configs 时填服务端默认（儋州）；文件与 dict 解析等价
    3 非法请求     缺字段/未知字段/重复地市/越界地市/组件占比和≠1/缺目录/坏类型 → 报错指明字段
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from new_energy.solar.pipeline import DEFAULT_DATA_DIR, _compute, _parse_request, run_solar

logging.disable(logging.INFO)

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
BASELINE = REPO / ".refactor_baseline" / "solar"
FIXTURES = TESTS / "fixtures"
CASES = ["偏小资源年", "正常资源年", "偏大资源年"]
CITY = "460400_儋州市"

# 与 .refactor_baseline/solar/orig/danzhou_pv_physical_model_config.json 同口径（统一 JSON 格式）
CONFIG = json.loads((FIXTURES / "solar_danzhou.json").read_text(encoding="utf-8"))

results: list[tuple[str, str, float]] = []


def _check(name: str, fn) -> None:
    start = time.perf_counter()
    try:
        fn()
        results.append((name, "PASS", time.perf_counter() - start))
    except Exception as exc:  # noqa: BLE001 - 自测脚本汇总所有失败
        results.append((name, f"FAIL: {exc}", time.perf_counter() - start))


def _assert_close(actual: np.ndarray, ref: np.ndarray, what: str) -> None:
    """容差判据：总量相对偏差 ≤1e-6，逐元素 |差| ≤ 1e-6 + 1e-4×|基准值|（覆盖基准文件 6 位小数舍入）。
"""
    assert actual.shape == ref.shape, f"{what} 规模不符: 基准{ref.shape} vs 实际{actual.shape}"
    total_ref = float(np.abs(ref).sum())
    total_rel = float(np.abs(actual.sum() - ref.sum()) / max(total_ref, 1e-12))
    assert total_rel <= 1e-6, f"{what} 总量相对偏差 {total_rel:.3e} 超过 1e-6"
    gap = float(np.max(np.abs(actual - ref) - (1e-6 + 1e-4 * np.abs(ref))))
    assert gap <= 0.0, f"{what} 逐元素偏差超过容差 (最大超出 {gap:.3e})"


def case_full_regression() -> None:
    # 内部三景全量比对（场景 + 出力，与冻结基准一致）
    result = _compute(_parse_request(CONFIG))
    for case in CASES:
        base_scene = pd.read_csv(BASELINE / "scenario" / f"BASE_{case}_辐照度场景.csv", encoding="utf-8-sig")
        _assert_close(
            result.scenarios[case][CITY].to_numpy(float),
            base_scene["生成辐照度_W_m2"].to_numpy(float),
            f"{case} 生成辐照度",
        )
        base_power = pd.read_csv(BASELINE / "power" / f"BASE_{case}_光伏出力.csv", encoding="utf-8-sig", index_col=0)
        table = result.power[case]
        assert len(table) == len(base_power) == 8760, f"{case} 出力行数应为 8760，实际 {len(table)}"
        for col in base_power.columns:
            _assert_close(table[col].to_numpy(float), base_power[col].to_numpy(float), f"{case} {col}")
    power = result.power["正常资源年"]["区域总出力_MW"]
    assert power.between(0, 1e4).all(), "区域出力超出合理范围"

    # 公共入口：dict 直传 → 写 CSV + 返回 JSON 双份核对
    resp = run_solar(CONFIG)
    b = pd.read_csv(resp["output_file"], encoding="utf-8-sig")
    assert len(b) == 8760 and b.columns[0] == "时间", f"CSV 应为 8760 行、首列时间，实际 {b.shape} {b.columns[0]}"
    assert resp["cities"] == [CITY], "返回 cities 应只有儋州"
    assert abs(resp["total_capacity_mw"] - 955.21) <= 1e-9, "儋州总装机应为 955.21"
    t = resp["power_table"]
    assert list(t) == list(b.columns), "power_table 列集应与 CSV 一致"
    lengths = {len(v) for v in t.values()}
    assert lengths == {8760}, f"power_table 各列长度应为 8760，实际 {lengths}"
    _assert_close(np.asarray(t["区域总出力_MW"]), b["区域总出力_MW"].to_numpy(float), "power_table vs CSV 区域总出力")


def case_defaults() -> None:
    # 缺 data_dir/city_configs → 填服务端默认（儋州区域），不跑计算
    cfg = _parse_request({
        "scenario": {"year": 2025, "resource_year_type": "正常资源年"},
        "output_dir": "Z:/tmp",
    })
    assert cfg["data_dir"] == DEFAULT_DATA_DIR, "缺省 data_dir 应为服务端默认数据目录"
    assert list(cfg["city_configs"]) == [CITY], "缺省 city_configs 应为儋州"
    assert cfg["loss_factor"] == 1.0 and cfg["albedo"] == 0.2 and cfg["output_city_power"] is True
    # 文件与 dict 两种形态解析等价
    assert _parse_request(FIXTURES / "solar_danzhou.json") == _parse_request(CONFIG), "文件/dict 解析结果不一致"


def case_invalid_requests() -> None:
    def expect_error(mutate, needle: str) -> None:
        payload = copy.deepcopy(CONFIG)
        mutate(payload)
        try:
            _parse_request(payload)
        except ValueError as exc:
            assert needle in str(exc), f"报错未指明 {needle}: {exc}"
        else:
            raise AssertionError(f"未抛错（期望报错含 {needle}）")

    expect_error(lambda p: p.pop("output_dir"), "output_dir")
    expect_error(lambda p: p.pop("scenario"), "scenario")
    expect_error(lambda p: p.__setitem__("foo", 1), "foo")
    expect_error(lambda p: p.__setitem__("evaluate", True), "evaluate")
    expect_error(lambda p: p["city_configs"][0].__setitem__("city", "460999_不存在市"), "归属表之外")
    expect_error(lambda p: p["city_configs"].append(copy.deepcopy(p["city_configs"][0])), "重复")
    expect_error(lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5), "capacity_mw 必须为正")
    expect_error(lambda p: p["city_configs"][0]["components"][0].__setitem__("capacity_share", 0.5), "合计应为 1")
    expect_error(lambda p: p["city_configs"][0].pop("latitude"), "缺少必填字段 latitude")
    expect_error(lambda p: p.__setitem__("albedo", "abc"), "albedo")
    expect_error(lambda p: p["scenario"].__setitem__("year", "2025"), "scenario.year")
    expect_error(lambda p: p.__setitem__("data_dir", "Z:/no_such_dir"), "solar")


def main() -> None:
    _check("1 全流程回归（儋州，约1分钟）", case_full_regression)
    _check("2 默认值与解析等价", case_defaults)
    _check("3 非法请求校验", case_invalid_requests)

    print(f"\n{'用例':<28}结果  耗时")
    print("-" * 56)
    failed = 0
    for name, status, elapsed in results:
        print(f"{name:<30}{status.split(':')[0]:<6}{elapsed:6.1f}s")
        if status.startswith("FAIL"):
            failed += 1
            print(f"    {status}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
