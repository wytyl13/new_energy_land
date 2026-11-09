#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_runner.py

Runner.request 单元名匹配测试（D-29 四情形 + 光伏经纬度补全 + 海风配置核对 D-57），不跑算法。
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


def test_sea_configs() -> None:
    """海风配置核对（D-57）：有海格点缺 sea_configs 报错；无海格点不适用；默认请求填默认海风。"""
    payload = json.loads((FIXTURES / "wind_single_city.json").read_text(encoding="utf-8"))
    sea_cfg = {
        "region": "sea",
        "capacity_mw": 20,
        "turbines": [dict(payload["city_configs"][0]["turbines"][0], wind_shear_exponent=0.7)],
    }

    # 儋州含海格点：缺 sea_configs → 报错指明
    danzhou = copy.deepcopy(payload)
    danzhou["city_configs"][0]["city"] = "460400_儋州市"
    try:
        Runner("wind").request(danzhou)
    except ValueError as exc:
        assert "sea_configs" in str(exc), exc
    else:
        raise AssertionError("含海格点缺 sea_configs 未报错")

    # 儋州带合法 sea_configs → 通过且值正确
    with_sea = copy.deepcopy(danzhou)
    with_sea["sea_configs"] = copy.deepcopy(sea_cfg)
    req = Runner("wind").request(with_sea)
    assert req.sea_configs is not None and req.sea_configs.capacity_mw == 20

    # 海口无海格点：缺省合法（None）；传了报错
    req_land = Runner("wind").request(copy.deepcopy(payload))
    assert req_land.sea_configs is None
    bad_land = copy.deepcopy(payload)
    bad_land["sea_configs"] = copy.deepcopy(sea_cfg)
    try:
        Runner("wind").request(bad_land)
    except ValueError as exc:
        assert "sea_configs" in str(exc), exc
    else:
        raise AssertionError("无海格点传 sea_configs 未报错")

    # region 非 sea → 构造即报错
    bad_region = copy.deepcopy(with_sea)
    bad_region["sea_configs"]["region"] = "land"
    try:
        Runner("wind").request(bad_region)
    except ValueError as exc:
        assert "region" in str(exc), exc
    else:
        raise AssertionError("region 非 sea 未报错")

    # 默认请求（city_configs 未传）：默认海风机组填入
    req_default = Runner("wind").request(
        {"scenario": {"year": 2024, "resource_year_type": "正常资源年"}, "output_dir": "Z:/tmp"}
    )
    assert req_default.sea_configs is not None and req_default.sea_configs.capacity_mw == 20


def main() -> None:
    """依次执行五个用例。"""
    for test in (test_request_matched, test_request_skip, test_request_direct, test_request_error,
                 test_sea_configs):
        test()
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
