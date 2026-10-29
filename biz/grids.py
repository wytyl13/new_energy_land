#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : grids.py

格点数据源（原 reader.py 拆出）：按 (类型, 年份) 提取并聚合格点数据。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from .assignment import Assignment
from .constants import ENC, KIND_PREFIX

logger = logging.getLogger(__name__)


class GridData:
    """格点数据源：按 (类型, 年份) 查询单元序列与代表坐标。"""

    def __init__(self, grid_dir: Path, assignment_csv: Path,
                 units: set[str] | None = None, prefixes: dict[str, str] | None = None):
        """读归属表一遍、按请求单元集过滤一遍（查询时不再重复读表）。

        Args:
            grid_dir (Path): 数据目录（其下每类型一个子目录）。
            assignment_csv (Path): 全国格点地市归属表。
            units (set[str] | None): 请求单元集（查询产出后核对），None = 表内全部单元。
            prefixes (dict[str, str] | None): 类型 → 声明变量前缀，缺省 KIND_PREFIX。
        """
        # 1. 数据目录与前缀声明
        self.grid_dir = grid_dir
        self.prefixes = prefixes or {}
        self.units = set(units) if units is not None else None

        # 2. 归属表坐标一遍读好，按请求单元集过滤
        self.coords_by_city = Assignment(assignment_csv).coords
        if self.units is not None:
            self.coords_by_city = {k: v for k, v in self.coords_by_city.items() if k in self.units}

    def forward(self, kind: str, years: range | list[int] | tuple[int, ...],
                single_unit: str | None = None) -> dict[str, pd.Series]:
        """读 kind 逐年格点 CSV → {单元键: 逐年拼接序列}（UTC 索引、升序）。

        列匹配按保留 2 位小数的坐标键查表，命中列前缀须等于声明前缀
        （变量身份校验）；single_unit（未命中单元）取声明前缀的全部格点列，
        列清单首年取定、后续年核对存在。

        Args:
            kind (str): 数据类型（wind/solar/temperature/pressure）。
            years (range | list[int] | tuple[int, ...]): 读取年份。
            single_unit (str | None): 未命中归属表的区域单元名，None = 走归属表。

        Returns:
            dict[str, pd.Series]: 单元键 → 逐年拼接的格点均值序列。
        """
        prefix = self.prefixes.get(kind) or KIND_PREFIX[kind]

        # 1. 确定目标单元容器：未命中单元 → 单单元；否则归属表全部单元
        pieces = {single_unit: []} if single_unit else {k: [] for k in self.coords_by_city}
        single_columns: list[str] | None = None

        # 2. 逐年读文件按单元取列求格点均值
        for year in years:
            path = self._get_file(kind, year)
            logger.info("读取格点文件: %s (%d 个单元)", path.relative_to(self.grid_dir), len(pieces))

            df = pd.read_csv(path, encoding=ENC)
            time_idx = pd.to_datetime(df["time"], errors="coerce")
            lookup = self._parse_columns(df.columns, path.name)

            for unit in pieces:
                if single_unit:
                    # 模式 A：未命中单元，取声明前缀的全部格点列（首年取定，后续年核对存在）
                    if single_columns is None:
                        single_columns = [col for pts in lookup.values() for p, col in pts if p == prefix]
                        others = {p for pts in lookup.values() for p, _ in pts if p != prefix}
                        if not single_columns:
                            raise ValueError(f"{path.name} 无前缀为 {prefix} 的格点列，无法聚合 {unit}")
                        if others:
                            raise ValueError(
                                f"{path.name} 混入其他前缀 {others}，无法聚合 {unit}（前缀应为 {prefix}）"
                            )
                    if missing := sorted(set(single_columns) - set(df.columns)):
                        raise ValueError(f"{path.name} 缺少 {unit} 的格点列: {missing[:3]}...")
                    cols = single_columns
                else:
                    # 模式 B：命中归属表，按单元格点坐标逐点匹配列
                    cols = []
                    for lat, lon in self.coords_by_city[unit]:
                        candidates = lookup.get((round(lat, 2), round(lon, 2)), [])
                        match = [col for p, col in candidates if p == prefix]
                        if not match:
                            hint = f"（该坐标现有前缀: {sorted({p for p, _ in candidates})}）" if candidates else ""
                            raise ValueError(
                                f"{path.name} 缺少 {unit} ({prefix}) 的格点列: "
                                f"lat_{lat:.2f}_lon_{lon:.2f}{hint}"
                            )
                        cols.append(match[0])

                values = df[cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
                pieces[unit].append(pd.Series(values.to_numpy(float), index=time_idx, name=unit))

        # 3. 请求单元集与产出核对：配置了但归属表/数据没有的单元在此报错（数值红线）
        if self.units is not None and (missing := sorted(self.units - set(pieces))):
            raise ValueError(f"city_configs 含数据范围之外的地市键: {missing}")
        return {key: pd.concat(parts).sort_index() for key, parts in pieces.items()}

    __call__ = forward

    def center(self, kind: str) -> tuple[float, float]:
        """未命中单元的代表性坐标：按文件名排序最前文件中，声明前缀格点列的坐标均值。

        Args:
            kind (str): 数据类型。

        Returns:
            tuple[float, float]: (平均纬度, 平均经度)。
        """
        prefix = self.prefixes.get(kind) or KIND_PREFIX[kind]

        # 1. 按文件名排序最前的年份文件（格点集年份间恒定）
        files = sorted((self.grid_dir / kind).glob(f"*{kind}_*.csv"))
        if not files:
            raise ValueError(f"数据目录无 {kind} 文件: {self.grid_dir / kind}")
        first = files[0]

        # 2. 声明前缀格点列的坐标均值
        lookup = self._parse_columns(pd.read_csv(first, nrows=0).columns, first.name)
        coords = [coord for coord, pts in lookup.items() if any(p == prefix for p, _ in pts)]
        if not coords:
            raise ValueError(f"{first.name} 无前缀为 {prefix} 的格点列，无法确定单元坐标")
        return sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)

    def _get_file(self, kind: str, year: int) -> Path:
        """按 *{kind}_{year}.csv 找某年数据文件（前缀任意，可为空）。

        Args:
            kind (str): 数据类型。
            year (int): 年份。

        Returns:
            Path: 数据文件路径，匹配数不等于 1（不存在或同年多个混放）报错。
        """
        files = list((self.grid_dir / kind).glob(f"*{kind}_{year}.csv"))
        if len(files) != 1:
            raise ValueError(f"预期 1 个文件 {self.grid_dir / kind / f'[区域_]{kind}_{year}.csv'}，"
                             f"实际匹配到: {[f.name for f in files]}")
        return files[0]

    @staticmethod
    def _parse_columns(columns: pd.Index, path_name: str) -> dict[tuple[float, float], list[tuple[str, str]]]:
        """数据列名 → (保留 2 位小数的纬, 经) → [(前缀, 列名)] 查表。

        非格点列（含 time）不匹配即跳过（数据多余列不参与聚合）；
        无可识别格点列报错。

        Args:
            columns (pd.Index): 数据文件的列名。
            path_name (str): 文件名（报错指明用）。

        Returns:
            dict[tuple[float, float], list[tuple[str, str]]]: 坐标键 → [(前缀, 列名)]。
        """
        lookup: dict[tuple[float, float], list[tuple[str, str]]] = {}
        for col in columns:
            if m := re.fullmatch(r"(.+)_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)", str(col)):
                key = (round(float(m.group(2)), 2), round(float(m.group(3)), 2))
                lookup.setdefault(key, []).append((m.group(1), str(col)))
        if not lookup:
            raise ValueError(f"{path_name} 无可识别的格点列")
        return lookup
