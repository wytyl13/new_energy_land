#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : solar_selftest.py

光伏业务层唯一入口自测：统一 JSON（文件/dict）校验 + 全流程回归。

运行（仓库根目录）:
    python -m tests.solar_selftest

用例：
    1 全流程回归   儋州单市，三类辐照度场景与区域出力均与冻结基准容差一致 + 公共入口双份返回核对
    2 默认值       缺 data_dir/city_configs 时填服务端默认（儋州）；文件与 dict 解析等价
    3 非法请求     缺字段/未知字段/重复地市/越界地市/组件台数或额定非法/缺目录/坏类型 → 报错指明字段
    4 区域单元     单个未命中归属表的单元按传入数据全部格点列聚合，经纬度缺省取格点均值
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from biz.assignment import Assignment
from biz.runner import Runner, run
from biz.schema import DEFAULT_DATA_DIR, Schema

logging.disable(logging.INFO)

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
BASELINE = REPO / ".refactor_baseline" / "solar"
FIXTURES = TESTS / "fixtures"
CASES = ["偏小资源年", "正常资源年", "偏大资源年"]
CITY = "460400_儋州市"

# 与 .refactor_baseline/solar/orig/danzhou_pv_physical_model_config.json 同口径（统一 JSON 格式）
CONFIG = json.loads((FIXTURES / "solar_danzhou.json").read_text(encoding="utf-8"))

# 基准口径（P7.9 重生成）：与生产默认一致（全国归属表 + 显式经纬度），D-47 旧口径差异不再适用；
# 出力仍按总量比对（晨昏 17 时 POA 对太阳位置边界敏感，逐元素不可复现）
BASELINE_PAYLOAD = copy.deepcopy(CONFIG)

results: list[tuple[str, str, float]] = []


def _check(name: str, fn) -> None:
    """执行一个用例并记录 PASS/FAIL 与耗时。

    Args:
        name (str): 用例名。
        fn (Callable): 用例函数。

    Returns:
        None
    """
    start = time.perf_counter()
    try:
        fn()
        results.append((name, "PASS", time.perf_counter() - start))
    except Exception as exc:  # noqa: BLE001 - 自测脚本汇总所有失败
        results.append((name, f"FAIL: {exc}", time.perf_counter() - start))


def _assert_close(actual: np.ndarray, ref: np.ndarray, what: str) -> None:
    """容差判据：总量相对偏差 ≤1e-6，逐元素 |差| ≤ 1e-6 + 1e-4×|基准值|（覆盖基准文件 6 位小数舍入）。

    Args:
        actual (np.ndarray): 实际值数组。
        ref (np.ndarray): 基准值数组。
        what (str): 比对项名（用于断言消息）。

    Returns:
        None
    """
    assert actual.shape == ref.shape, f"{what} 规模不符: 基准{ref.shape} vs 实际{actual.shape}"
    total_ref = float(np.abs(ref).sum())
    total_rel = float(np.abs(actual.sum() - ref.sum()) / max(total_ref, 1e-12))
    assert total_rel <= 1e-6, f"{what} 总量相对偏差 {total_rel:.3e} 超过 1e-6"
    gap = float(np.max(np.abs(actual - ref) - (1e-6 + 1e-4 * np.abs(ref))))
    assert gap <= 0.0, f"{what} 逐元素偏差超过容差 (最大超出 {gap:.3e})"


def _assert_power_total(actual: np.ndarray, ref: np.ndarray, what: str, tol: float = 1e-3) -> None:
    """出力列只比总量（D-47）：基准温度数据快照（common80 目录）已删、且晨昏 17 时 POA
    对太阳位置边界敏感，逐元素不可复现（实测 7/8760 小时全开/全关翻转、月度最大 ~1.5%）；
    场景列仍按 _assert_close 全量逐元素比对。

    Args:
        actual (np.ndarray): 实际值数组。
        ref (np.ndarray): 基准值数组。
        what (str): 比对项名（用于断言消息）。
        tol (float): 总量相对偏差容差。

    Returns:
        None
    """
    assert actual.shape == ref.shape, f"{what} 规模不符: 基准{ref.shape} vs 实际{actual.shape}"
    total_ref = float(np.abs(ref).sum())
    total_rel = float(np.abs(actual.sum() - ref.sum()) / max(total_ref, 1e-12))
    assert total_rel <= tol, f"{what} 总量相对偏差 {total_rel:.3e} 超过 {tol:g}"


def case_full_regression() -> None:
    """儋州三景全量回归（场景+出力对冻结基准），并核对公共入口双份返回。

    Returns:
        None
    """
    # 内部三景全量比对（场景 + 出力，与冻结基准一致，按基准口径传参 D-47）
    result = Runner("solar").result(BASELINE_PAYLOAD)
    for case in CASES:
        base_scene = pd.read_csv(BASELINE / "scenario" / f"BASE_{case}_辐照度场景.csv", encoding="utf-8-sig")
        _assert_close(
            result.scenarios[case][CITY].to_numpy(float),
            base_scene["生成辐照度_W_m2"].to_numpy(float),
            f"{case} 生成辐照度",
        )
        base_power = pd.read_csv(BASELINE / "power" / f"BASE_{case}_光伏出力.csv", encoding="utf-8-sig", index_col=0)
        pr = result.power[case]
        assert len(pr.index) == len(base_power) == 8760, f"{case} 出力行数应为 8760，实际 {len(pr.index)}"
        _assert_power_total(pr.city_power[CITY], base_power[f"{CITY}_总出力_MW"].to_numpy(float), f"{case} {CITY}_总出力_MW")
        _assert_power_total(pr.total_power_mw, base_power["区域总出力_MW"].to_numpy(float), f"{case} 区域总出力_MW")
        _assert_power_total(pr.total_power_pu, base_power["区域总出力_pu"].to_numpy(float), f"{case} 区域总出力_pu")
    power = result.power["正常资源年"].total_power_mw
    assert ((power >= 0) & (power <= 1e4)).all(), "区域出力超出合理范围"

    # 公共入口：dict 直传 → 写 CSV + 返回 JSON 双份核对
    resp = run("solar", BASELINE_PAYLOAD)
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
    """缺省字段填服务端默认（儋州区域），文件与 dict 解析等价。

    Returns:
        None
    """
    # 缺 data_dir/city_configs → 填服务端默认（儋州区域），不跑计算
    req = Runner("solar").request(
        {
            "scenario": {"year": 2025, "resource_year_type": "正常资源年"},
            "output_dir": "Z:/tmp",
        }
    )
    assert req.data_dir == DEFAULT_DATA_DIR, "缺省 data_dir 应为服务端默认数据目录"
    assert list(req.city_configs) == [CITY], "缺省 city_configs 应为儋州"
    assert req.loss_factor == 1.0 and req.albedo == 0.2 and req.output_city_power is True
    # latitude/longitude 缺省 → 归属表地市格点中心
    payload = copy.deepcopy(CONFIG)
    for field in ("latitude", "longitude"):
        payload["city_configs"][0].pop(field)
    omitted = Runner("solar").request(payload)
    center = Assignment(omitted.assignment_file).centers[CITY]
    got = omitted.city_configs[CITY]
    assert (got.latitude, got.longitude) == center, "缺省经纬度应为地市格点中心"
    # 文件与 dict 两种形态解析等价
    assert Schema("solar")(FIXTURES / "solar_danzhou.json") == Schema("solar")(CONFIG), "文件/dict 解析结果不一致"


def case_invalid_requests() -> None:
    """13 项非法请求均报错且指明问题字段（未知/缺必填 → TypeError，其余 ValueError）。

    Returns:
        None
    """

    def expect_error(mutate, needle: str) -> None:
        """按 mutate 改动请求后期望 Runner.request 报错含 needle。

        Args:
            mutate (Callable): 对 payload 的改动函数。
            needle (str): 期望报错包含的关键词。

        Returns:
            None
        """
        payload = copy.deepcopy(CONFIG)
        mutate(payload)
        try:
            Runner("solar").request(payload)
        except (TypeError, ValueError) as exc:
            assert needle in str(exc), f"报错未指明 {needle}: {exc}"
        else:
            raise AssertionError(f"未抛错（期望报错含 {needle}）")

    expect_error(lambda p: p.pop("output_dir"), "output_dir")
    expect_error(lambda p: p.pop("scenario"), "scenario")
    expect_error(lambda p: p.__setitem__("foo", 1), "foo")
    expect_error(lambda p: p.__setitem__("evaluate", True), "evaluate")

    # 单元名匹配规则（D-53）：多单元全部未命中 → 报错；部分未命中 → 跳过（见用例 4 的对照）
    def _two_units_both_invalid(p: dict) -> None:
        p["city_configs"].append(copy.deepcopy(p["city_configs"][0]))
        p["city_configs"][0]["city"] = "460999_不存在市"
        p["city_configs"][1]["city"] = "460998_也不存在市"

    expect_error(_two_units_both_invalid, "归属表之外")
    expect_error(lambda p: p["city_configs"].append(copy.deepcopy(p["city_configs"][0])), "重复")
    expect_error(lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5), "capacity_mw 必须为正")
    expect_error(lambda p: p["city_configs"][0]["components"][0].__setitem__("count", 0), "count 必须是正整数")
    expect_error(lambda p: p["city_configs"][0]["components"][0].__setitem__("rated_power_mw", -1), "rated_power_mw 必须为正")
    expect_error(lambda p: p["city_configs"][0].__setitem__("latitude", 999), "latitude 必须在")
    expect_error(lambda p: p.__setitem__("albedo", "abc"), "albedo")
    expect_error(lambda p: p["scenario"].__setitem__("year", "2025"), "year 必须是整数")
    expect_error(lambda p: p.__setitem__("data_dir", "Z:/no_such_dir"), "solar")


def case_direct_unit() -> None:
    """单个未命中归属表的单元：全部格点列聚合，经纬度缺省取数据格点坐标均值。

    Returns:
        None
    """
    payload = copy.deepcopy(CONFIG)
    payload["city_configs"][0]["city"] = "框选区域B"
    for field in ("latitude", "longitude"):
        payload["city_configs"][0].pop(field)
    payload["variable_prefixes"] = {"solar": "irradiance_w_m2", "temperature": "temperature_2m_k"}

    cfg = Runner("solar").request(payload)
    got = cfg.city_configs["框选区域B"]
    assert abs(got.latitude - 19.125) <= 1e-9 and abs(got.longitude - 109.875) <= 1e-9, \
        f"缺省经纬度应为数据格点坐标均值，实际 ({got.latitude}, {got.longitude})"

    resp = run("solar", payload)
    assert resp["cities"] == ["框选区域B"], "返回 cities 应只有该区域单元"
    table = resp["power_table"]
    assert "框选区域B_总出力_MW" in table, "缺少区域单元出力列"
    assert np.allclose(np.asarray(table["区域总出力_MW"]), np.asarray(table["框选区域B_总出力_MW"])), \
        "区域总出力应等于该单元出力"


def main() -> None:
    """依次执行全部用例并打印结果表。

    Returns:
        None
    """
    _check("1 全流程回归（儋州，约1分钟）", case_full_regression)
    _check("2 默认值与解析等价", case_defaults)
    _check("3 非法请求校验", case_invalid_requests)
    _check("4 区域单元聚合", case_direct_unit)

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
