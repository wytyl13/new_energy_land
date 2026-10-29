#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : runner.py

业务层总装入口（D-51）：Runner 把 Schema（校验）→ 单元名匹配（D-29）→
Reader（取数）→ 算法模型 → Export（写出）串成全流程。
run 是对外唯一入口（CLI 与 HTTP 同路）。算法侧只收内存序列、只返回
结果对象（D-44）；输出表的列集、列名、行序在 Export 组装（D-45）。
"""
from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path

from new_energy.pipeline import SolarPowerModel, WindPowerModel

from .assignment import Assignment
from .export import Export
from .grids import GridData
from .reader import Reader
from .schema import Schema

logger = logging.getLogger(__name__)


class Runner:
    """业务层组装器：request 校验匹配，result 取数算到结果，forward 写出返回。

    Attributes:
        kind (str): 算法类型（wind/solar）。
        direct_unit (str | None): 未命中归属表的区域单元名（request 阶段得出，D-29）。
    """

    def __init__(self, kind: str):
        """记录算法类型。

        Args:
            kind (str): 算法类型（wind/solar）。
        """
        self.kind = kind
        self.direct_unit = None

    def request(self, source: dict | str | Path):
        """Schema 解析 + 单元名匹配（D-29）+ 光伏经纬度补全 → 请求对象。

        Args:
            source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。

        Returns:
            RequestBase: 请求对象（光伏 city_configs 已含补全的经纬度）。
        """
        req = Schema(self.kind)(source)
        # 单元名匹配规则（D-29，D-53）：全命中 → 归属表给格点；部分命中 → 未命中的
        # 无格点单元跳过并告警（如东城区）；单单元未命中 → 合法自定义区域，
        # 格点 = 传入数据中声明前缀的全部格点列；多单元全部未命中 → 报错
        unmatched = [city for city in req.city_configs if city not in Assignment(req.assignment_file).keys]
        if unmatched and len(unmatched) == len(req.city_configs) and len(unmatched) > 1:
            raise ValueError(f"city_configs 含归属表之外的地市: {unmatched}")
        if unmatched and len(unmatched) < len(req.city_configs):
            logger.warning("跳过归属表之外的地市（无格点数据）: %s", unmatched)
            for city in unmatched:
                del req.city_configs[city]
        else:
            self.direct_unit = unmatched[0] if unmatched else None
        if self.direct_unit:
            logger.info("单单元 %s 未命中归属表，按传入数据全部格点列聚合", self.direct_unit)

        if self.kind == "solar":
            # 补 latitude/longitude 缺省：命中单元用归属表格点中心，未命中的单单元用数据格点坐标均值
            centers = Assignment(req.assignment_file).centers
            for city, unit in req.city_configs.items():
                if unit.latitude is not None:
                    continue
                center = (GridData(req.data_dir, req.assignment_file,
                                   prefixes=req.variable_prefixes).center("solar")
                          if city == self.direct_unit else centers.get(city))
                if center is None:
                    raise ValueError(f"city_configs 未提供 {city} 的 latitude/longitude，且归属表无该地市格点中心")
                req.city_configs[city] = dataclasses.replace(unit, latitude=center[0], longitude=center[1])
        return req

    def result(self, source: dict | str | Path):
        """请求 → 取数 → 算法全流程（不写文件），供回归自测取场景与出力。

        Args:
            source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。

        Returns:
            WindResult | SolarResult: 算法侧结果对象（scenarios/power/classes[/metrics]）。
        """
        return self._model(self.request(source))

    def forward(self, source: dict | str | Path) -> dict:
        """全流程：校验 → 取数 → 算法 → 写出力 CSV → 结果 JSON。

        Args:
            source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。

        Returns:
            dict: {"output_file", "elapsed_s", "scenario", "cities",
                   "total_capacity_mw", "power_table"}，power_table 为列名→数组的字典。
        """
        start = time.perf_counter()
        req = self.request(source)
        return Export(self.kind, req)(self._model(req), start)

    __call__ = forward

    def _model(self, req):
        """Reader 取数 → 算法模型 → 结果对象。"""
        config, series = Reader(self.kind)(req, self.direct_unit)
        model = WindPowerModel if self.kind == "wind" else SolarPowerModel
        return model(config)(*series, req.city_configs)


def run(kind: str, source: dict | str | Path) -> dict:
    """业务层对外唯一入口（薄包装）：统一 JSON → 全流程 → 写出力 CSV → 返回结果 JSON。

    入口处打印请求参数日志（city_configs 只打单元名，不打机组明细）。

    Args:
        kind (str): 算法类型（wind/solar）。
        source (dict | str | Path): 统一 JSON 的 dict（服务层），或 JSON 文件路径（CLI）。

    Returns:
        dict: Runner.forward 的结果 JSON。
    """
    if isinstance(source, dict):
        logger.info("%s 请求: %s 单元=%s", kind,
                    {k: v for k, v in source.items() if k != "city_configs"},
                    [u.get("city") for u in source.get("city_configs") or []] or "默认")
    else:
        logger.info("%s 请求: 配置文件 %s", kind, source)
    return Runner(kind)(source)


if __name__ == "__main__":
    """冒烟自检：Runner 全流程（对外唯一入口 run 同路）。运行: python -m biz.runner"""
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "wind_single_city.json"
    resp = Runner("wind")(fixture)
    print(f"Runner(wind): {Path(resp['output_file']).name} rows={len(resp['power_table']['time'])}"
          f" cols={list(resp['power_table'])} elapsed={resp['elapsed_s']}s")
