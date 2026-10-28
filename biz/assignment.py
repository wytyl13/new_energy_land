#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : assignment.py

归属表读取与格点列名解析（原 reader.py 拆出）。

一张归属表每次请求被多处读取（单元→格点坐标、地市中心、合法地市键），
统一 lru_cache 每个文件只读一次；返回值被缓存，调用方只读不改。

兼容两种表结构：海南表（wind_grid/solar_grid 列名，即格点列名）与
全国表（每行一个格点的 中心纬度_N/中心经度_E 数值坐标）。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .constants import ENC


def parse_grid_column(name: str) -> tuple[str, float, float]:
    """解析格点列名 "前缀_lat_19.00_lon_109.50" → (前缀, 纬度, 经度)，不符报错。"""
    m = re.fullmatch(r"(.+)_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)", str(name))
    if not m:
        raise ValueError(f"无法解析格点名称: {name}")
    return m.group(1), float(m.group(2)), float(m.group(3))


@lru_cache(maxsize=None)
def _table(assignment_csv: Path) -> pd.DataFrame:
    """读归属表（缓存按路径，进程内不重读；归属表是静态数据）。"""
    return pd.read_csv(assignment_csv, encoding=ENC)


@lru_cache(maxsize=None)
def assignment_coords(assignment_csv: Path, kind: str) -> dict[str, list[tuple[float, float]]]:
    """归属表 inside 行 → 单元键 → 期望格点坐标列表。

    海南表取 wind_grid/solar_grid 列名解析（风取 wind_grid，温压/辐照取
    solar_grid）；全国表直接取 中心纬度_N/中心经度_E 数值。
    """
    table = _table(assignment_csv)
    inside = table.loc[table["assignment_method"].eq("inside")]
    coords: dict[str, list[tuple[float, float]]] = {}
    if "wind_grid" in table.columns:
        name_col = "wind_grid" if kind == "wind" else "solar_grid"
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True):
            coords[f"{int(code)}_{city}"] = [parse_grid_column(n)[1:] for n in block[name_col]]
    elif "中心纬度_N" in table.columns:
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True):
            coords[f"{int(code)}_{city}"] = list(
                zip(block["中心纬度_N"].astype(float), block["中心经度_E"].astype(float))
            )
    else:
        raise ValueError(f"归属表缺少格点列（wind_grid 或 中心纬度_N）: {assignment_csv}")
    return coords


def city_centers(assignment_csv: Path) -> dict[str, tuple[float, float]]:
    """各地市代表坐标 = 严格边界内格点的平均经纬度（全国表中心两列，海南表 latitude/longitude）。"""
    table = _table(assignment_csv)
    inside = table.loc[table["assignment_method"].eq("inside")]
    lat_col, lon_col = ("中心纬度_N", "中心经度_E") if "中心纬度_N" in table.columns else ("latitude", "longitude")
    return {
        f"{int(code)}_{city}": (float(block[lat_col].mean()), float(block[lon_col].mean()))
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True)
    }


def assignment_keys(assignment_csv: Path) -> set[str]:
    """归属表全部合法地市键（行政区代码_地市名）。"""
    table = _table(assignment_csv)
    return {f"{int(code)}_{city}" for code, city in zip(table["city_code"], table["city"])}


if __name__ == "__main__":
    """冒烟自检：全国表与海南表各自三种读取。运行: python -m biz.assignment"""
    data = Path(__file__).resolve().parents[1] / "data"
    for label, table_file in (("全国表", data / "全国格点地市归属.csv"),
                              ("海南表", data / "海南地市分区" / "海南共同80格点_地市归属.csv")):
        keys = sorted(assignment_keys(table_file))
        sample = keys[0]
        print(f"{label}: {len(keys)} 单元 示例={sample}"
              f" 格点数={len(assignment_coords(table_file, 'solar')[sample])}"
              f" 中心={city_centers(table_file)[sample]}")
