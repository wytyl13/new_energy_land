# -*- coding: utf-8 -*-
"""风电 pipeline 唯一入口自测：统一 JSON（文件/dict）校验 + 全流程回归。

运行（仓库根目录）:
    python -m tests.wind_run_selftest

用例：
    1 全省回归   文件路径形态跑全流程，输出与冻结基准容差一致（四项核对）+ 返回 JSON 结构核对
    2 单市一致性 dict 形态只传海口，海口列与基准一致，province 列等于本市列
    3 默认值     缺 data_dir/city_configs 时填服务端默认；文件与 dict 解析等价
    4 非法请求   缺字段/未知字段/重复地市/坏类型/缺目录/越界地市/负容量/配置文件不存在 → 报错指明字段
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from new_energy.wind.pipeline import DEFAULT_DATA_DIR, _parse_request, run_wind

logging.disable(logging.INFO)

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
BASELINE = TESTS.parent / ".refactor_baseline" / "BASE_正常资源年.csv"
CITY_COL = "460100_海口市_power_mw"

results: list[tuple[str, str, float]] = []


def _check(name: str, fn) -> None:
    start = time.perf_counter()
    try:
        fn()
        results.append((name, "PASS", time.perf_counter() - start))
    except Exception as exc:  # noqa: BLE001 - 自测脚本汇总所有失败
        results.append((name, f"FAIL: {exc}", time.perf_counter() - start))


def _rel_max(a: pd.DataFrame, b: pd.DataFrame) -> float:
    num = a.select_dtypes(include=[np.number])
    return float(((num - b[num.columns]).abs() / num.abs().clip(lower=1e-12)).to_numpy().max())


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def case_full_province() -> None:
    response = run_wind(FIXTURES / "wind_full_province.json")  # 文件路径形态
    a = pd.read_csv(BASELINE, encoding="utf-8-sig")
    b = pd.read_csv(response["output_file"], encoding="utf-8-sig")
    assert a.shape == b.shape == (8784, 22), f"规模不符: 基准{a.shape} vs 实际{b.shape}"
    assert list(a.columns) == list(b.columns), "列集或列序不一致"
    total_rel = abs(a["province_power_mw"].sum() - b["province_power_mw"].sum()) / a["province_power_mw"].sum()
    assert total_rel <= 1e-6, f"总量相对偏差 {total_rel:.3e} 超过 1e-6"
    rel = _rel_max(a, b)
    assert rel <= 1e-4, f"逐元素最大相对偏差 {rel:.3e} 超过 1e-4"
    assert b["province_power_mw"].between(0, 1e5).all(), "全省出力超出合理范围"

    # 返回 JSON 结构核对（规模/数值）
    assert response["scenario"] == {"year": 2024, "resource_year_type": "正常资源年"}
    assert len(response["cities"]) == 18, "本次计算范围应为 18 地市"
    assert abs(response["total_capacity_mw"] - 375.215) <= 1e-6, "总装机与默认配置不符"
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
    fixture = _load_fixture("wind_single_city.json")
    response = run_wind(fixture)  # dict 形态
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
    # 缺 data_dir/city_configs → 填服务端默认（不跑计算，只核对解析结果）
    cfg = _parse_request({
        "scenario": {"year": 2024, "resource_year_type": "正常资源年"},
        "output_dir": "Z:/tmp",
    })
    assert cfg["data_dir"] == DEFAULT_DATA_DIR, "缺省 data_dir 应为服务端默认数据目录"
    assert len(cfg["city_configs"]) == 18, "缺省 city_configs 应为全省 18 地市"
    assert cfg["loss_factor"] == 1.0 and cfg["output_city_power"] is True and cfg["evaluate"] is False
    # 文件与 dict 两种形态解析等价
    fixture = _load_fixture("wind_single_city.json")
    assert _parse_request(FIXTURES / "wind_single_city.json") == _parse_request(fixture), "文件/dict 解析结果不一致"


def case_invalid_requests() -> None:
    base = _load_fixture("wind_single_city.json")

    def expect_error(mutate, needle: str) -> None:
        payload = copy.deepcopy(base)
        mutate(payload)
        try:
            _parse_request(payload)
        except ValueError as exc:
            assert needle in str(exc), f"报错未指明 {needle}: {exc}"
        else:
            raise AssertionError(f"未抛错（期望报错含 {needle}）")

    expect_error(lambda p: p.pop("output_dir"), "output_dir")
    expect_error(lambda p: p.pop("scenario"), "scenario")
    expect_error(lambda p: p["scenario"].pop("year"), "缺少必填字段 year")
    expect_error(lambda p: p["scenario"].__setitem__("resource_year_type", "好年景"), "resource_year_type 必须是")
    expect_error(lambda p: p.__setitem__("foo", 1), "foo")
    expect_error(lambda p: p["city_configs"].append(copy.deepcopy(p["city_configs"][0])), "重复")
    expect_error(lambda p: p["city_configs"][0].__setitem__("city", "460999_不存在市"), "归属表之外")
    expect_error(lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5), "capacity_mw 必须为正")
    expect_error(lambda p: p["city_configs"][0]["turbines"][0].__setitem__("rated_power_mw", 0), "rated_power_mw 必须为正")
    expect_error(lambda p: p.__setitem__("loss_factor", "abc"), "loss_factor")
    expect_error(lambda p: p["scenario"].__setitem__("year", "2024"), "scenario.year")
    expect_error(lambda p: p.__setitem__("data_dir", "Z:/no_such_dir"), "wind")

    # 文件形态：配置文件不存在
    try:
        run_wind(Path("Z:/no_such_config.json"))
    except ValueError as exc:
        assert "配置文件不存在" in str(exc)
    else:
        raise AssertionError("未抛错（期望 配置文件不存在）")


def main() -> None:
    _check("1 全省回归（约4分钟）", case_full_province)
    _check("2 单市一致性", case_single_city)
    _check("3 默认值与解析等价", case_defaults)
    _check("4 非法请求校验", case_invalid_requests)

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
