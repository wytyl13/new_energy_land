#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_schema.py

Schema 测试：风电请求构造、缺省机组配置、非法输入报错。
运行（仓库根目录）: python -m tests.biz.test_schema
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from biz.schema import Schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_wind_request() -> None:
    """fixture 构造 WindRequest，city_configs 机组对象化。"""
    req = Schema("wind")(FIXTURES / "wind_single_city.json")
    unit = req.city_configs["460100_海口市"]
    assert req.scenario.year == 2024 and req.scenario.resource_year_type == "正常资源年"
    assert unit.capacity_mw == 22.29
    assert unit.turbines[0].model == "6MW机组"
    assert req.output_dir == Path(req.output_dir) and req.output_dir.name == "output"


def test_default_configs() -> None:
    """city_configs 未传 → 读服务端默认机组配置（18 个单元）。"""
    req = Schema("wind")(FIXTURES / "wind_minimal.json")
    assert len(req.city_configs) == 18
    assert "460100_海口市" in req.city_configs


def test_invalid_error() -> None:
    """非法输入报错：负装机、字符串年份、缺必填字段、未知根字段。"""
    payload = json.loads((FIXTURES / "wind_single_city.json").read_text(encoding="utf-8"))
    cases = {
        "capacity_mw=-5": lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5),
        "year='2024'": lambda p: p["scenario"].__setitem__("year", "2024"),
        "缺 output_dir": lambda p: p.pop("output_dir"),
        "未知字段 foo": lambda p: p.__setitem__("foo", 1),
    }
    for label, mutate in cases.items():
        bad = copy.deepcopy(payload)
        mutate(bad)
        try:
            Schema("wind")(bad)
        except (TypeError, ValueError):
            continue
        else:
            raise AssertionError(f"{label} 未报错")


def main() -> None:
    """依次执行三个用例。"""
    for test in (test_wind_request, test_default_configs, test_invalid_error):
        test()
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
