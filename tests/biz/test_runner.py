#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_runner.py

Runner.request 单元名匹配测试（D-29 四情形 + 光伏经纬度补全），不跑算法。
运行（仓库根目录）: python -m tests.biz.test_runner
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from biz.runner import Runner

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_request_matched() -> None:
    """全命中：direct_unit 为 None，单元不变。"""
    payload = json.loads((FIXTURES / "wind_single_city.json").read_text(encoding="utf-8"))
    runner = Runner("wind")
    req = runner.request(payload)
    assert runner.direct_unit is None
    assert list(req.city_configs) == ["460100_海口市"]


def test_request_skip() -> None:
    """部分未命中：未命中项跳过，剩余命中项。"""
    payload = json.loads((FIXTURES / "wind_single_city.json").read_text(encoding="utf-8"))
    extra = copy.deepcopy(payload["city_configs"][0])
    extra["city"] = "110101_东城区"
    payload["city_configs"].append(extra)
    runner = Runner("wind")
    req = runner.request(payload)
    assert runner.direct_unit is None
    assert list(req.city_configs) == ["460100_海口市"]


def test_request_direct() -> None:
    """单单元未命中：direct_unit = 该键，光伏经纬度由数据格点均值补全。"""
    payload = json.loads((FIXTURES / "solar_danzhou.json").read_text(encoding="utf-8"))
    unit = payload["city_configs"][0]
    unit.update(city="test_自定义区域")
    unit.pop("latitude"), unit.pop("longitude")
    runner = Runner("solar")
    req = runner.request(payload)
    assert runner.direct_unit == "test_自定义区域"
    config = req.city_configs["test_自定义区域"]
    assert config.latitude is not None and config.longitude is not None


def test_request_error() -> None:
    """多单元全未命中：报错含未命中列表。"""
    payload = json.loads((FIXTURES / "wind_single_city.json").read_text(encoding="utf-8"))
    base = payload["city_configs"][0]
    payload["city_configs"] = [dict(base, city="区域A"), dict(base, city="区域B")]
    try:
        Runner("wind").request(payload)
    except ValueError as exc:
        assert "区域A" in str(exc), exc
    else:
        raise AssertionError("全未命中未报错")


def main() -> None:
    """依次执行四个用例。"""
    for test in (test_request_matched, test_request_skip, test_request_direct, test_request_error):
        test()
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
