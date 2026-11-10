#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : assignment.py

全国归属表：单元（要算出力的地区，如 460400_儋州市）→ 格点坐标查表。

输入 CSV 格式（每行一个格点，utf-8-sig）：
    中心纬度_N,中心经度_E,province_code,province,city_code,city,assignment_method,海陆判定
    19.50,109.25,46,海南省,460400,儋州市,inside,0
原始表只存一份（DataFrame，挂 unit/inside/sea 派生列），查询即一行布尔索引，
不预存 coords/centers 等派生字典（D-56）：
    a = Assignment(Path("data/全国格点地市归属.csv"))
    "460400_儋州市" in a.keys            # 合法单元名吗？→ True/False
    a.coords("460400_儋州市")            # → [(19.5, 109.0), ...] 该单元全部 inside 格点
    a.land_coords("460400_儋州市")       # → 陆格点；a.sea_coords(...) → 海格点（风电海陆区分用）
    a.centers("460400_儋州市")           # → (19.6, 109.3) 中心 = 格点经纬度均值
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from .constants import ENC


# ─────────────────────────────────────────────────────────────────────────────
# 地市归属（全国表存一份，查询 = 一行布尔索引）
# ─────────────────────────────────────────────────────────────────────────────

class Assignment:
    """一张全国归属表的内存形态：构造时读 CSV → 校验 → 原始表存一份（挂派生列）；
    查询方法一行布尔索引现取，不预存派生结构（D-56）。

    Attributes:
        keys (set[str]): 全部合法单元名（行政区代码_地市名，含非 inside 行）。
        coord_units (list[str]): 有 inside 格点的单元名（字典序，grids 据此定单元容器）。
    """

    REQUIRED_COLS = {"city_code", "city", "assignment_method", "中心纬度_N", "中心经度_E", "海陆判定"}

    def __init__(self, assignment_csv: Path):
        """读表一次；缺列、海陆判定缺值或非 0/1 立即报错；原始表存一份。

        Args:
            assignment_csv (Path): 全国归属表 CSV 路径（格式见模块说明）。
        """
        table = pd.read_csv(assignment_csv, encoding=ENC)

        # 1. 集合校验缺失列（一次性找出所有缺失列）+ 海陆判定值域（NaN 或非 0/1 都拦，防静默当陆点）
        if missing := self.REQUIRED_COLS - set(table.columns):
            raise ValueError(f"归属表缺少列 {missing}: {assignment_csv}")
        if (~table["海陆判定"].isin([0, 1])).any():
            raise ValueError(f"归属表 海陆判定 列存在缺值或非 0/1 值: {assignment_csv}")

        # 2. 原始表存一份；单元名与两个布尔列一次算好挂在表上（省市两列忽略）
        table["unit"] = table["city_code"].astype(int).astype(str) + "_" + table["city"]
        table["inside"] = table["assignment_method"].eq("inside")
        table["sea"] = table["海陆判定"] == 1
        self._t = table


    @property
    def keys(self) -> set[str]:
        """全部合法单元名（行政区代码_地市名）。"""
        return set(self._t["unit"])


    @property
    def coord_units(self) -> list[str]:
        """有 inside 格点的单元名（字典序）。"""
        return sorted(set(self._t.loc[self._t["inside"], "unit"]))


    def units_of_province(self, province: str) -> list[str]:
        """省名 → 该省全部单元名（字典序）；省名不在归属表立即报错（结构查询接口用，P10.6）。"""
        b = self._t[self._t["province"] == province]
        if b.empty:
            raise ValueError(f"province 不在归属表: {province}")
        return sorted(set(b["unit"]))


    def coords(self, unit: str) -> list[tuple[float, float]]:
        """单元名 → 全部 inside 格点坐标列表（grids 据此取气象数据列）。"""
        b = self._t[self._t["inside"] & (self._t["unit"] == unit)]
        return list(zip(b["中心纬度_N"].tolist(), b["中心经度_E"].tolist()))


    def land_coords(self, unit: str) -> list[tuple[float, float]]:
        """单元名 → 陆格点坐标（风电陆风聚合用）。"""
        b = self._t[self._t["inside"] & ~self._t["sea"] & (self._t["unit"] == unit)]
        return list(zip(b["中心纬度_N"].tolist(), b["中心经度_E"].tolist()))


    def sea_coords(self, unit: str) -> list[tuple[float, float]]:
        """单元名 → 海格点坐标（区域海风组 = 请求内各单元海格点并集）。"""
        b = self._t[self._t["inside"] & self._t["sea"] & (self._t["unit"] == unit)]
        return list(zip(b["中心纬度_N"].tolist(), b["中心经度_E"].tolist()))


    def centers(self, unit: str) -> tuple[float, float]:
        """单元名 → (纬度均值, 经度均值)（补缺省经纬度用）。"""
        b = self._t[self._t["inside"] & (self._t["unit"] == unit)]
        return (float(b["中心纬度_N"].mean()), float(b["中心经度_E"].mean()))
