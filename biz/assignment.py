#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : assignment.py

全国归属表：单元（要算出力的地区，如 460400_儋州市）→ 格点坐标查表。

输入 CSV 格式（每行一个格点，utf-8-sig）：
    中心纬度_N,中心经度_E,province_code,province,city_code,city,assignment_method
    19.50,109.25,46,海南省,460400,儋州市,inside
本类只用其中五列：city_code、city、assignment_method、中心纬度_N、中心经度_E
（省市两列忽略）。assignment_method 为 inside 的行算坐标与中心，其余行只进合法单元名。

用法——查询是取属性，不是调函数；CSV 只在构造时读一次，之后全在内存取：
    a = Assignment(Path("data/全国格点地市归属.csv"))
    "460400_儋州市" in a.keys                  # 合法单元名吗？→ True/False
    a.coords["460400_儋州市"]                  # → [(19.5, 109.0), ...] 该单元的格点坐标
    a.centers["460400_儋州市"]                 # → (19.6, 109.3) 中心 = 格点经纬度均值
属性是共享数据，调用方只读不改。
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd
from .constants import ENC


class Assignment:
    """一张全国归属表的内存形态：构造时读 CSV -> 校验列 -> 算好三项查询结果。

    Attributes:
        keys (set[str]): 全部合法单元名（行政区代码_地市名，含非 inside 行）。
        coords (dict[str, list[tuple[float, float]]]): 单元名 → inside 格点坐标列表 （grids 据此取气象数据列）。
        centers (dict[str, tuple[float, float]]): 单元名 → (纬度均值, 经度均值)（补缺省经纬度用）。
    """
    
    REQUIRED_COLS = {"city_code", "city", "assignment_method", "中心纬度_N", "中心经度_E"}
    COORD_COLS = ["中心纬度_N", "中心经度_E"]

    def __init__(self, assignment_csv: Path):
        """读表一次；缺列立即报错指明列名；结果存为上述三个属性。

        Args:
            assignment_csv (Path): 全国归属表 CSV 路径（格式见模块说明）。
        """
        table = pd.read_csv(assignment_csv, encoding=ENC)
        
        # 1. 集合校验缺失列（一次性找出所有缺失列）
        if missing := self.REQUIRED_COLS - set(table.columns):
            raise ValueError(f"归属表缺少列 {missing}: {assignment_csv}")
        
        # 2. 向量化生成单元名 & 提前转换经纬度类型（避免在循环中重复处理）
        unit_keys = table["city_code"].astype(int).astype(str) + "_" + table["city"]
        table[self.COORD_COLS] = table[self.COORD_COLS].astype(float)
        
        # 3. 收集全部合法单元名
        self.keys: set[str] = set(unit_keys)
        
        # 4. 筛选 inside 行并按单元名只分组一次，同时算出 coords 和 centers
        inside = table.loc[table["assignment_method"].eq("inside")]
        inside_keys = unit_keys.loc[inside.index]
        self.coords = {}
        self.centers = {}
        for key, block in inside.groupby(inside_keys):
            lats, lons = block["中心纬度_N"], block["中心经度_E"]
            self.coords[key] = list(zip(lats, lons))
            self.centers[key] = (float(lats.mean()), float(lons.mean()))