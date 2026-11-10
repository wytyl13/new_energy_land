#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/10
@Author  : weiyutao
@File    : structure.py

结构查询（P10.6，D-61）：省名 → 前端要填的配置结构。
风电返回 city_configs（每单元陆/海格点数）+ sea_configs（是否需要海风配置）+
pure_sea_units（纯海单元名单，不进 city_configs，配了也会被跳过）；
光伏不分海陆，只返回 city_configs（每单元全部 inside 格点数）。
"""
from __future__ import annotations

from pathlib import Path

from .assignment import Assignment
from .schema import DEFAULT_ASSIGNMENT


def structure(kind: str, province: str, assignment_file: Path = DEFAULT_ASSIGNMENT) -> dict:
    """省名 → 该省要填写的配置结构（GET /api/{wind,solar}/structure 的数据层）。

    Args:
        kind (str): wind 或 solar；wind 拆海陆并给海风结构，solar 不拆。
        province (str): 归属表 province 列的省名（如 "海南省"），不在表内报错。
        assignment_file (Path): 全国归属表路径（默认同计算请求）。

    Returns:
        dict: wind → {"city_configs": [{"city", "land_grid_count", "sea_grid_count"}],
                      "sea_configs": {"needed", "grid_count"}, "pure_sea_units": [单元名]}；
              solar → {"city_configs": [{"city", "grid_count"}]}。
              city_configs 只含有 inside 格点的单元，字典序。
    """
    a = Assignment(assignment_file)
    units = a.units_of_province(province)

    # 1. 光伏不分海陆：每个有 inside 格点的单元一项，格点数 = 陆 + 海全部
    if kind == "solar":
        coord_units = set(a.coord_units)
        return {"city_configs": [
            {"city": u, "grid_count": len(a.coords(u))} for u in units if u in coord_units
        ]}

    # 2. 风电拆海陆：城市只配有陆格点的单元；纯海单元（无陆格点）另给名单；
    #    sea_configs.grid_count = 所列城市海格点合计（纯海单元海点不含，因其不进 city_configs）
    cities: list[dict] = []
    pure_sea: list[str] = []
    for u in units:
        land, sea = len(a.land_coords(u)), len(a.sea_coords(u))
        if land:
            cities.append({"city": u, "land_grid_count": land, "sea_grid_count": sea})
        elif sea:
            pure_sea.append(u)
    sea_grid_count = sum(c["sea_grid_count"] for c in cities)
    return {
        "city_configs": cities,
        "sea_configs": {"needed": sea_grid_count > 0, "grid_count": sea_grid_count},
        "pure_sea_units": pure_sea,
    }
