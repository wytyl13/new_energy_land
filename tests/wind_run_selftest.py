#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : wind_run_selftest.py

风电业务层唯一入口自测：统一 JSON（文件/dict）校验 + 全流程回归。

运行（仓库根目录）:
    python -m tests.wind_run_selftest

用例：
    1 单市回归   文件路径形态跑海口全流程，输出与冻结基准容差一致（四项核对）+ 返回 JSON 结构核对
    2 单市一致性 dict 形态只传海口，海口列与基准一致，province 列等于本市列
    3 默认值     缺 data_dir/city_configs 时填服务端默认；文件与 dict 解析等价
    4 非法请求   缺字段/未知字段/重复地市/坏类型/缺目录/越界地市/负容量/配置文件不存在 → 报错指明字段
    5 区域单元   单个未命中归属表的单元按传入数据全部格点列聚合；声明前缀不符报错
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from biz.runner import Runner, run
from biz.schema import DEFAULT_DATA_DIR, Schema

logging.disable(logging.INFO)

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
BASELINE = TESTS.parent / ".refactor_baseline" / "BASE_正常资源年.csv"
CITY_COL = "460100_海口市_power_mw"

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


def _rel_max(a: pd.DataFrame, b: pd.DataFrame) -> float:
    """两表数值列的逐元素最大相对偏差。

    Args:
        a (pd.DataFrame): 基准表。
        b (pd.DataFrame): 对照表。

    Returns:
        float: 最大相对偏差。
    """
    num = a.select_dtypes(include=[np.number])
    return float(((num - b[num.columns]).abs() / num.abs().clip(lower=1e-12)).to_numpy().max())


def _load_fixture(name: str) -> dict:
    """读取 tests/fixtures 下的 JSON 请求样例。

    Args:
        name (str): 文件名。

    Returns:
        dict: 请求配置。
    """
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def case_full_regression() -> None:
    """单市（海口）回归：出力对冻结基准（总量≤1e-6、逐元素≤1e-4），返回 JSON 结构核对。

    基准（D-49）＝当前默认口径（全国归属表 + 现行风格点数据）重生成并冻结的单市输出，
    自测按轻量版只跑海口，不再每次全省 18 地市。

    Returns:
        None
    """
    response = run("wind", FIXTURES / "wind_single_city.json")  # 文件路径形态
    a = pd.read_csv(BASELINE, encoding="utf-8-sig")
    b = pd.read_csv(response["output_file"], encoding="utf-8-sig")
    assert a.shape == b.shape == (8784, 5), f"规模不符: 基准{a.shape} vs 实际{b.shape}"
    assert list(a.columns) == list(b.columns), "列集或列序不一致"
    total_rel = abs(a["province_power_mw"].sum() - b["province_power_mw"].sum()) / a["province_power_mw"].sum()
    assert total_rel <= 1e-6, f"总量相对偏差 {total_rel:.3e} 超过 1e-6"
    rel = _rel_max(a, b)
    assert rel <= 1e-4, f"逐元素最大相对偏差 {rel:.3e} 超过 1e-4"
    assert b["province_power_mw"].between(0, 1e5).all(), "单市出力超出合理范围"

    # 返回 JSON 结构核对（规模/数值）
    assert response["scenario"] == {"year": 2024, "resource_year_type": "正常资源年"}
    assert response["cities"] == ["460100_海口市"], "本次计算范围应只有海口"
    assert abs(response["total_capacity_mw"] - 22.29) <= 1e-6, "总装机与默认配置不符"
    table = response["power_table"]
    assert list(table) == list(b.columns), "power_table 列集应与 CSV 一致"
    lengths = {len(v) for v in table.values()}
    assert lengths == {8784}, f"power_table 各列长度应为 8784，实际 {lengths}"
    rel_table = float(
        np.max(
            np.abs(np.asarray(table["province_power_mw"]) - b["province_power_mw"].to_numpy())
            / np.abs(b["province_power_mw"].to_numpy()).clip(1e-12)
        )
    )
    assert rel_table <= 1e-6, f"power_table 与 CSV 逐元素偏差 {rel_table:.3e} 超过 1e-6"


def case_single_city() -> None:
    """单市（海口）dict 形态出力与基准的海口列一致，province 列等于本市列。

    Returns:
        None
    """
    fixture = _load_fixture("wind_single_city.json")
    response = run("wind", fixture)  # dict 形态
    a = pd.read_csv(BASELINE, encoding="utf-8-sig")
    b = pd.read_csv(response["output_file"], encoding="utf-8-sig")
    assert b.shape == (8784, 5), f"单市输出应为 8784×5，实际 {b.shape}"
    assert CITY_COL in b.columns, "缺少海口出力列"
    rel = float(
        (b[CITY_COL] - a[CITY_COL]).abs().to_numpy().max() / a[CITY_COL].abs().clip(lower=1e-12).max()
    )
    assert rel <= 1e-9, f"单市海口列与基准偏差 {rel:.3e}"
    assert np.array_equal(b["province_power_mw"].to_numpy(), b[CITY_COL].to_numpy()), "单市 province 列应等于本市列"
    assert response["cities"] == ["460100_海口市"], "返回 cities 应只有海口"
    assert abs(response["total_capacity_mw"] - 22.29) <= 1e-9, "单市总装机应为 22.29"


def case_defaults() -> None:
    """缺省字段填服务端默认（全省 18 地市），文件与 dict 解析等价。

    Returns:
        None
    """
    # 缺 data_dir/city_configs → 填服务端默认（不跑计算，只核对解析结果）
    req = Runner("wind").request(
        {
            "scenario": {"year": 2024, "resource_year_type": "正常资源年"},
            "output_dir": "Z:/tmp",
        }
    )
    assert req.data_dir == DEFAULT_DATA_DIR, "缺省 data_dir 应为服务端默认数据目录"
    assert len(req.city_configs) == 18, "缺省 city_configs 应为全省 18 地市"
    assert req.loss_factor == 1.0 and req.output_city_power is True and req.evaluate is False
    # 文件与 dict 两种形态解析等价
    fixture = _load_fixture("wind_single_city.json")
    assert Schema("wind")(FIXTURES / "wind_single_city.json") == Schema("wind")(fixture), "文件/dict 解析结果不一致"


def case_invalid_requests() -> None:
    """12 项非法请求与配置文件不存在均报错且指明原因（未知/缺必填 → TypeError，其余 ValueError）。

    Returns:
        None
    """
    base = _load_fixture("wind_single_city.json")

    def expect_error(mutate, needle: str) -> None:
        """按 mutate 改动请求后期望 Runner.request 报错含 needle。

        Args:
            mutate (Callable): 对 payload 的改动函数。
            needle (str): 期望报错包含的关键词。

        Returns:
            None
        """
        payload = copy.deepcopy(base)
        mutate(payload)
        try:
            Runner("wind").request(payload)
        except (TypeError, ValueError) as exc:
            assert needle in str(exc), f"报错未指明 {needle}: {exc}"
        else:
            raise AssertionError(f"未抛错（期望报错含 {needle}）")

    expect_error(lambda p: p.pop("output_dir"), "output_dir")
    expect_error(lambda p: p.pop("scenario"), "scenario")
    expect_error(lambda p: p["scenario"].pop("year"), "year")
    expect_error(lambda p: p["scenario"].__setitem__("resource_year_type", "好年景"), "resource_year_type 必须是")
    expect_error(lambda p: p.__setitem__("foo", 1), "foo")
    expect_error(lambda p: p["city_configs"].append(copy.deepcopy(p["city_configs"][0])), "重复")

    # 单元名匹配规则（D-53）：多单元全部未命中 → 报错；部分未命中 → 跳过（见用例 5 的对照）
    def _two_units_both_invalid(p: dict) -> None:
        p["city_configs"].append(copy.deepcopy(p["city_configs"][0]))
        p["city_configs"][0]["city"] = "460999_不存在市"
        p["city_configs"][1]["city"] = "460998_也不存在市"

    expect_error(_two_units_both_invalid, "归属表之外")
    expect_error(lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5), "capacity_mw 必须为正")
    expect_error(lambda p: p["city_configs"][0]["turbines"][0].__setitem__("rated_power_mw", 0), "rated_power_mw 必须为正")
    expect_error(lambda p: p.__setitem__("loss_factor", "abc"), "loss_factor")
    expect_error(lambda p: p["scenario"].__setitem__("year", "2024"), "year 必须是整数")
    expect_error(lambda p: p.__setitem__("data_dir", "Z:/no_such_dir"), "wind")

    # 文件形态：配置文件不存在
    try:
        run("wind", Path("Z:/no_such_config.json"))
    except ValueError as exc:
        assert "配置文件不存在" in str(exc)
    else:
        raise AssertionError("未抛错（期望 配置文件不存在）")


def case_direct_unit() -> None:
    """单个未命中归属表的单元：按传入数据全部声明前缀格点列聚合，province 列等于该单元列。

    Returns:
        None
    """
    fixture = _load_fixture("wind_single_city.json")
    fixture["city_configs"][0]["city"] = "框选区域A"
    fixture["variable_prefixes"] = {
        "wind": "wind_speed_100m",
        "temperature": "temperature_2m_k",
        "pressure": "surface_pressure_pa",
    }
    response = run("wind", fixture)
    b = pd.read_csv(response["output_file"], encoding="utf-8-sig")
    unit_col = "框选区域A_power_mw"
    assert b.shape == (8784, 5), f"单区域输出应为 8784×5，实际 {b.shape}"
    assert unit_col in b.columns, "缺少区域单元出力列"
    assert np.array_equal(b["province_power_mw"].to_numpy(), b[unit_col].to_numpy()), "province 列应等于区域单元列"
    assert response["cities"] == ["框选区域A"], "返回 cities 应只有该区域单元"

    # 声明前缀与数据不符（10m/100m 混装防线）：读取首个年份文件时即报错
    bad = copy.deepcopy(fixture)
    bad["variable_prefixes"] = {"wind": "wind_speed_10m"}
    try:
        run("wind", bad)
    except ValueError as exc:
        assert "前缀" in str(exc), f"报错未指明前缀: {exc}"
    else:
        raise AssertionError("未抛错（期望前缀校验报错）")


def main() -> None:
    """依次执行全部用例并打印结果表。

    Returns:
        None
    """
    _check("1 单市回归（海口，约20秒）", case_full_regression)
    _check("2 单市一致性", case_single_city)
    _check("3 默认值与解析等价", case_defaults)
    _check("4 非法请求校验", case_invalid_requests)
    _check("5 区域单元聚合", case_direct_unit)

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
