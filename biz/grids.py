#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : grids.py

格点数据文件读取（原 reader.py 拆出）：数据列名 → 数值坐标查表，
按单元聚合格点均值为逐年拼接序列（UTC 时间索引）。

列匹配按经纬度数值键（保留 2 位小数）查表，命中列前缀须等于声明前缀
（变量身份校验）；未命中的单个单元（D-29）取声明前缀的全部格点列，
混有其它前缀即报错（防 10m/100m 之类变量混装进同一均值）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .assignment import assignment_coords, parse_grid_column
from .constants import ENC, KIND_PREFIX

logger = logging.getLogger(__name__)


def _grid_column_lookup(columns: object, path_name: str) -> dict[tuple[float, float], list[tuple[str, str]]]:
    """数据列名 → (保留 2 位小数的纬, 经) → [(前缀, 列名)] 查表。

    非格点列（含 time）解析失败跳过，不进查表（数据多余列不参与聚合，
    与旧行为一致）；无可识别格点列即报错。
    """
    lookup: dict[tuple[float, float], list[tuple[str, str]]] = {}
    for col in columns:
        try:
            prefix, lat, lon = parse_grid_column(col)
        except ValueError:
            continue
        lookup.setdefault((round(lat, 2), round(lon, 2)), []).append((prefix, col))
    if not lookup:
        raise ValueError(f"{path_name} 无可识别的格点列")
    return lookup


def _match_column(
    lookup: dict[tuple[float, float], list[tuple[str, str]]],
    key: str,
    lat: float,
    lon: float,
    prefix: str,
    path_name: str,
) -> str:
    """按数值坐标键与声明前缀查一个格点列，缺失或前缀不符即报错（指明单元/坐标/现有前缀）。"""
    candidates = lookup.get((round(lat, 2), round(lon, 2)), [])
    for candidate_prefix, col in candidates:
        if candidate_prefix == prefix:
            return col
    hint = f"（该坐标现有前缀: {sorted({p for p, _ in candidates})}）" if candidates else ""
    raise ValueError(
        f"{path_name} 缺少 {key} 前缀为 {prefix} 的格点列: lat_{lat:.2f}_lon_{lon:.2f}{hint}"
    )


def find_grid_file(kind_dir: Path, kind: str, year: int) -> Path:
    """按 *_{kind}_{year}.csv 找某年数据文件（区域前缀任意，hainan_/beijing_ 均可）；
    同一年多个匹配（多区域文件混放）报错。"""
    files = sorted(kind_dir.glob(f"*_{kind}_{year}.csv"))
    if not files:
        raise ValueError(f"数据文件不存在: {kind_dir / f'*_{kind}_{year}.csv'}")
    if len(files) > 1:
        raise ValueError(f"{kind_dir} 同一年有多个 {kind} 文件，须只留一个区域: {[f.name for f in files]}")
    return files[0]


def _first_grid_file(kind_dir: Path, kind: str) -> Path:
    """kind 子目录按文件名排序最前的年份文件（region_center 专用：格点集年份间恒定；
    single_unit 的列清单取实际读取的首个年份文件，不吃窗口外的老文件）。"""
    files = sorted(kind_dir.glob(f"*_{kind}_*.csv"))
    if not files:
        raise ValueError(f"数据目录无 {kind} 文件: {kind_dir}")
    return files[0]


def _single_unit_columns(first_path: Path, prefix: str, unit: str) -> list[str]:
    """未命中单元的格点范围：首个读取年份文件中声明前缀的全部格点列。"""
    lookup = _grid_column_lookup(pd.read_csv(first_path, nrows=0).columns, first_path.name)
    matched = sorted(col for pts in lookup.values() for p, col in pts if p == prefix)
    others = sorted({p for pts in lookup.values() for p, _ in pts if p != prefix})
    if not matched:
        raise ValueError(f"{first_path.name} 无前缀为 {prefix} 的格点列，无法聚合单元 {unit}")
    if others:
        raise ValueError(
            f"{first_path.name} 混有其它前缀的格点列 {others}，无法聚合单元 {unit}（前缀应为 {prefix}）"
        )
    return matched


def region_center(grid_dir: Path, kind: str, prefixes: dict[str, str] | None = None) -> tuple[float, float]:
    """未命中单元的代表性坐标：首个文件中声明前缀格点列的坐标均值（光伏理想辐照用）。"""
    prefix = (prefixes or {}).get(kind) or KIND_PREFIX[kind]
    first = _first_grid_file(Path(grid_dir) / kind, kind)
    coords = [
        key
        for key, pts in _grid_column_lookup(pd.read_csv(first, nrows=0).columns, first.name).items()
        if any(p == prefix for p, _ in pts)
    ]
    if not coords:
        raise ValueError(f"{first.name} 无前缀为 {prefix} 的格点列，无法确定单元坐标")
    return sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)


def load_city_series(
    grid_dir: Path,
    assignment_csv: Path,
    kind: str,
    years: range | list[int] | tuple[int, ...],
    single_unit: str | None = None,
    units: set[str] | None = None,
    prefixes: dict[str, str] | None = None,
) -> dict[str, pd.Series]:
    """读 kind 逐年格点 CSV（grid_dir/kind/*_{kind}_{year}.csv，区域前缀任意）→ 单元键 → 逐年拼接序列。

    命中归属表的单元按归属表坐标取列聚合（格点均值）；single_unit（未命中
    单元）取数据中声明前缀的全部格点列（assignment_csv 忽略）。units 只聚合
    这些归属表单元（None = 表内全部，全国表含数据范围外单元，应传请求单元集），
    传入时与产出核对，配置了但归属表/数据没有的单元在此报错；
    prefixes 为 kind → 声明变量前缀，缺省 KIND_PREFIX。
    """
    prefix = (prefixes or {}).get(kind) or KIND_PREFIX[kind]
    if single_unit is None:
        coords_by_city = assignment_coords(assignment_csv, kind)
        if units is not None:
            coords_by_city = {key: coords for key, coords in coords_by_city.items() if key in units}
        single_columns: list[str] | None = None
    else:
        coords_by_city = {}
        single_columns = _single_unit_columns(
            find_grid_file(Path(grid_dir) / kind, kind, years[0]), prefix, single_unit)

    pieces: dict[str, list[pd.Series]] = {key: [] for key in coords_by_city}
    if single_unit is not None:
        pieces[single_unit] = []
    for year in years:
        path = find_grid_file(Path(grid_dir) / kind, kind, year)
        logger.info("读取格点文件: %s (%d 个单元)", path.relative_to(grid_dir), len(pieces))
        data = pd.read_csv(path, encoding=ENC)
        time = pd.to_datetime(data["time"], errors="coerce")
        lookup = _grid_column_lookup(data.columns, path.name)
        cols_by_unit: dict[str, list[str]] = {}
        if single_columns is not None:
            missing = sorted(set(single_columns) - set(data.columns))
            if missing:
                raise ValueError(f"{path.name} 缺少{single_unit}的格点列: {missing[:3]}...")
            cols_by_unit[single_unit] = single_columns
        else:
            for key, coords in coords_by_city.items():
                cols_by_unit[key] = [
                    _match_column(lookup, key, lat, lon, prefix, path.name) for lat, lon in coords
                ]
        for key, cols in cols_by_unit.items():
            values = data[cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
            pieces[key].append(pd.Series(values.to_numpy(float), index=time, name=key))

    # 请求单元集与产出核对：配置了但归属表/数据没有的单元在此报错（数值红线）
    if units is not None and (missing := sorted(units - set(pieces))):
        raise ValueError(f"city_configs 含数据范围之外的地市键: {missing}")
    return {key: pd.concat(parts).sort_index() for key, parts in pieces.items()}
