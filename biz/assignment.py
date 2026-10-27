#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : assignment.py

业务层格点读取：格点 CSV + 归属表 → 各地市气象序列（原 utils/city_weather.py
迁入，另收光伏地市格点中心 city_centers，原 solar/history.py）。时间保持原始
UTC 口径，+8 转北京时间由算法侧 build_history 按各自流程处理。

单元格点来源（D-29 匹配规则）：命中归属表的单元由归属表给坐标；未命中的
单个单元（single_unit）取数据中声明前缀的全部格点列。列匹配按经纬度数值键
（保留 2 位小数）查表，命中列前缀须等于声明前缀（变量身份校验）。归属表兼容
两种结构：海南表（wind_grid/solar_grid 列名）与全国表（每行一个格点数值坐标）。
"""
from __future__ import annotations

import calendar
import logging
import re
from pathlib import Path
from typing import NamedTuple

import pandas as pd

logger = logging.getLogger(__name__)

ENC = "utf-8-sig"  # 全部 CSV 读写编码

# 服务端历史口径默认值（扩历史范围时改这里）
DEFAULT_YEARS = (2000, 2025)  # 历史数据期望窗口，两端年份均包含；数据超出窗口不纳入
MIN_HISTORY_YEARS = 12  # 历史年份下限，低于此值报错（三类资源年各需足够样本）

# kind → (文件名/子目录名, 格点列前缀)
KIND_PREFIX = {
    "wind": "wind_speed_100m",
    "solar": "irradiance_w_m2",
    "temperature": "temperature_2m_k",
    "pressure": "surface_pressure_pa",
}


def parse_grid_name(value: str) -> tuple[float, float]:
    """解析格点列名末尾的纬度、经度。

    Args:
        value (str): 格点列名，形如 "前缀_lat_19.00_lon_109.50"。

    Returns:
        tuple[float, float]: (纬度, 经度)。
    """
    m = re.search(r"_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)$", str(value))
    if not m:
        raise ValueError(f"无法解析格点名称: {value}")
    return float(m.group(1)), float(m.group(2))


def parse_grid_column(name: str) -> tuple[str, float, float]:
    """解析格点列名，返回 (变量前缀, 纬度, 经度)。

    Args:
        name (str): 格点列名，形如 "前缀_lat_19.00_lon_109.50"。

    Returns:
        tuple[str, float, float]: 前缀与经纬度。
    """
    m = re.fullmatch(r"(.+)_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)", str(name))
    if not m:
        raise ValueError(f"无法解析格点名称: {name}")
    return m.group(1), float(m.group(2)), float(m.group(3))


def _assignment_coords(assignment_csv: Path, kind: str) -> dict[str, list[tuple[float, float]]]:
    """归属表 inside 行 → 单元键 → 期望格点坐标，兼容两种表结构。

    海南表：wind_grid/solar_grid 列名（风取 wind_grid，温压/辐照取 solar_grid）；
    全国表：每行一个格点的 中心纬度_N/中心经度_E 数值坐标。

    Args:
        assignment_csv (Path): 格点归属表 CSV 路径。
        kind (str): 数据类型（wind/solar/temperature/pressure）。

    Returns:
        dict[str, list[tuple[float, float]]]: 单元键 → (纬度, 经度) 列表。
    """
    table = pd.read_csv(assignment_csv, encoding=ENC)
    inside = table.loc[table["assignment_method"].eq("inside")]
    coords_by_city: dict[str, list[tuple[float, float]]] = {}
    if "wind_grid" in table.columns:
        name_col = "wind_grid" if kind == "wind" else "solar_grid"
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True):
            coords_by_city[f"{int(code)}_{city}"] = [parse_grid_name(n) for n in block[name_col]]
    elif "中心纬度_N" in table.columns:
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True):
            coords_by_city[f"{int(code)}_{city}"] = list(
                zip(block["中心纬度_N"].astype(float), block["中心经度_E"].astype(float))
            )
    else:
        raise ValueError(f"归属表缺少格点列（wind_grid 或 中心纬度_N）: {assignment_csv}")
    return coords_by_city


def _grid_column_lookup(columns: object, path_name: str) -> dict[tuple[float, float], list[tuple[str, str]]]:
    """数据列名 → 数值坐标键查表：(保留 2 位小数的纬, 经) → [(前缀, 列名)]。

    非格点列（含 time）不进查表，与旧行为一致（数据多余列不参与聚合）。

    Args:
        columns (object): 数据文件的列集合。
        path_name (str): 文件名（报错定位用）。

    Returns:
        dict[tuple[float, float], list[tuple[str, str]]]: 坐标键 → (前缀, 列名) 列表。
    """
    lookup: dict[tuple[float, float], list[tuple[str, str]]] = {}
    for col in columns:
        try:
            col_prefix, lat, lon = parse_grid_column(col)
        except ValueError:
            continue
        lookup.setdefault((round(lat, 2), round(lon, 2)), []).append((col_prefix, col))
    if not lookup:
        raise ValueError(f"{path_name} 无可识别的格点列")
    return lookup


def _first_grid_file(kind_dir: Path, kind: str) -> Path:
    """取 kind 子目录按文件名排序最前的年份文件。

    仅用于 region_center（代表坐标，格点集年份间恒定）；single_unit 的列清单
    取实际读取的首个年份文件（见 load_city_series），不吃窗口外的老文件。

    Args:
        kind_dir (Path): 数据类型子目录。
        kind (str): 数据类型。

    Returns:
        Path: 第一个年份文件路径。
    """
    files = sorted(kind_dir.glob(f"hainan_{kind}_*.csv"))
    if not files:
        raise ValueError(f"数据目录无 {kind} 文件: {kind_dir}")
    return files[0]


def _single_unit_columns(first_path: Path, prefix: str, unit: str) -> list[str]:
    """未命中单元的格点范围：首个读取年份文件中声明前缀的全部格点列。

    出现其它前缀的格点列即报错（防 10m/100m 之类变量混装进同一均值）。

    Args:
        first_path (Path): 实际读取的首个年份文件路径。
        prefix (str): 声明变量前缀。
        unit (str): 单元名（报错定位用）。

    Returns:
        list[str]: 格点列名列表。
    """
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
    """未命中单元的代表性坐标：声明前缀格点列的坐标均值（光伏理想辐照用）。

    Args:
        grid_dir (Path): 格点数据根目录。
        kind (str): 数据类型。
        prefixes (dict[str, str] | None): kind → 声明变量前缀，缺省 KIND_PREFIX。

    Returns:
        tuple[float, float]: (纬度, 经度)。
    """
    prefix = (prefixes or {}).get(kind) or KIND_PREFIX[kind]
    first = _first_grid_file(Path(grid_dir) / kind, kind)
    lookup = _grid_column_lookup(pd.read_csv(first, nrows=0).columns, first.name)
    coords = [key for key, pts in lookup.items() if any(p == prefix for p, _ in pts)]
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
    """读 kind 类型的逐年格点 CSV，按单元聚合为逐年拼接的序列。

    grid_dir 形如 .../hainan_era5_grid_csv，函数读 grid_dir/kind/hainan_{kind}_{year}.csv。
    命中归属表的单元按归属表坐标取列；single_unit（未命中单元）取数据中声明
    前缀的全部格点列。列匹配按经纬度数值键查表，命中列前缀须等于声明前缀。

    Args:
        grid_dir (Path): 格点数据根目录。
        assignment_csv (Path): 格点归属表 CSV 路径（single_unit 时忽略）。
        kind (str): 数据类型（wind/solar/temperature/pressure）。
        years (range | list[int] | tuple[int, ...]): 要读取的年份序列。
        single_unit (str | None): 未命中归属表的单元名（格点=数据全部声明前缀列）。
        units (set[str] | None): 只聚合这些归属表单元；None = 表内全部（全国表
            含数据范围外单元，服务层应传请求单元集）。
        prefixes (dict[str, str] | None): kind → 声明变量前缀，缺省 KIND_PREFIX。

    Returns:
        dict[str, pd.Series]: 单元键 → 逐年拼接 Series（UTC 时间索引，格点均值）。
    """
    prefix = (prefixes or {}).get(kind) or KIND_PREFIX[kind]
    if single_unit is None:
        coords_by_city = _assignment_coords(assignment_csv, kind)
        if units is not None:
            coords_by_city = {key: coords for key, coords in coords_by_city.items() if key in units}
        single_columns: list[str] | None = None
    else:
        coords_by_city = {}
        first_path = Path(grid_dir) / kind / f"hainan_{kind}_{years[0]}.csv"
        single_columns = _single_unit_columns(first_path, prefix, single_unit)

    pieces: dict[str, list[pd.Series]] = {key: [] for key in coords_by_city}
    if single_unit is not None:
        pieces[single_unit] = []
    for year in years:
        path = Path(grid_dir) / kind / f"hainan_{kind}_{year}.csv"
        if not path.exists():
            raise ValueError(f"数据文件不存在: {path}")
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

    return {
        key: pd.concat(parts).sort_index() for key, parts in pieces.items()
    }


def _match_column(
    lookup: dict[tuple[float, float], list[tuple[str, str]]],
    key: str,
    lat: float,
    lon: float,
    prefix: str,
    path_name: str,
) -> str:
    """按数值坐标键与声明前缀查一个格点列，缺失或前缀不符即报错。

    Args:
        lookup (dict): _grid_column_lookup 的查表结果。
        key (str): 单元键（报错定位用）。
        lat (float): 期望纬度。
        lon (float): 期望经度。
        prefix (str): 声明变量前缀。
        path_name (str): 文件名（报错定位用）。

    Returns:
        str: 命中的列名。
    """
    candidates = lookup.get((round(lat, 2), round(lon, 2)), [])
    for candidate_prefix, col in candidates:
        if candidate_prefix == prefix:
            return col
    hint = f"（该坐标现有前缀: {sorted({p for p, _ in candidates})}）" if candidates else ""
    raise ValueError(
        f"{path_name} 缺少 {key} 前缀为 {prefix} 的格点列: lat_{lat:.2f}_lon_{lon:.2f}{hint}"
    )


def city_centers(assignment_csv: Path) -> dict[str, tuple[float, float]]:
    """按严格边界内格点的平均经纬度给出各地市代表性坐标。

    Args:
        assignment_csv (Path): 格点归属表 CSV 路径。

    Returns:
        dict[str, tuple[float, float]]: 地市键 → (纬度, 经度)。
    """
    table = pd.read_csv(assignment_csv, encoding=ENC)
    inside = table.loc[table["assignment_method"].eq("inside")]
    # 兼容两种归属表结构：全国表 中心纬度_N/中心经度_E，海南表 latitude/longitude
    national = "中心纬度_N" in table.columns
    lat_col, lon_col = ("中心纬度_N", "中心经度_E") if national else ("latitude", "longitude")
    return {
        f"{int(code)}_{city}": (float(block[lat_col].mean()), float(block[lon_col].mean()))
        for (code, city), block in inside.groupby(["city_code", "city"], sort=True)
    }


def assignment_keys(assignment_csv: Path) -> set[str]:
    """读归属表，返回全部合法地市键集合。

    Args:
        assignment_csv (Path): 格点归属表 CSV 路径。

    Returns:
        set[str]: 地市键集合（行政区代码_地市名）。
    """
    table = pd.read_csv(assignment_csv, encoding=ENC)
    return {f"{int(code)}_{city}" for code, city in zip(table["city_code"], table["city"])}


class YearsPlan(NamedTuple):
    """历史年份方案：源年份、训练年份、场景轴年份的派生关系见 resolve_years。"""

    source_years: tuple[int, ...]  # 实际使用的历史年份（两端含）
    train_years: tuple[int, ...]   # 训练统计用 = source_years 除最后一年
    axis_year: int                 # 场景轴年份 = source_years 最后一年（只当日历模板）


def _file_years(grid_dir: Path, kind: str) -> set[int]:
    """扫数据子目录，返回实际存在的年份集合。

    Args:
        grid_dir (Path): 格点数据根目录。
        kind (str): 数据类型（子目录名）。

    Returns:
        set[int]: hainan_{kind}_{年}.csv 对应的年份集合。
    """
    pattern = re.compile(rf"hainan_{kind}_(\d{{4}})\.csv")
    return {
        int(match.group(1))
        for path in (Path(grid_dir) / kind).glob(f"hainan_{kind}_*.csv")
        if (match := pattern.fullmatch(path.name))
    }


def _row_count(path: Path) -> int:
    """数 CSV 数据行数（减去表头行）。

    Args:
        path (Path): CSV 文件路径。

    Returns:
        int: 数据行数。
    """
    with path.open("rb") as handle:
        return sum(1 for _ in handle) - 1


def resolve_years(
    grid_dir: Path,
    kinds: tuple[str, ...],
    window: tuple[int, int] = DEFAULT_YEARS,
) -> YearsPlan:
    """期望窗口 ∩ 各 kind 实际存在的年份 → 历史年份方案。

    规则：
        1. 多个 kind 取交集（最小可用范围），窗口外年份不纳入；
        2. 取最大连续年份段，段外年份丢弃并告警（防止跨年缺口被插值）；
        3. 段内每个文件校验行数（闰年 8784 / 平年 8760 小时），不符报错；
        4. 年份数低于 MIN_HISTORY_YEARS 报错；
        5. train_years = 除最后一年；axis_year = 最后一年（数据不参与统计，
           只借当 8760 小时日历模板，之后重排到请求的输出年份）。

    Args:
        grid_dir (Path): 格点数据根目录（下含 wind/solar/temperature/pressure 子目录）。
        kinds (tuple[str, ...]): 本流程历史要读的数据类型，如风电 ("wind",)、
            光伏 ("solar", "temperature")。
        window (tuple[int, int]): 期望窗口 (起始年, 结束年)，两端包含。

    Returns:
        YearsPlan: source_years / train_years / axis_year。
    """
    start, end = int(window[0]), int(window[1])
    if start >= end:
        raise ValueError(f"历史年份窗口配置错误: 起始 {start} ≥ 结束 {end}")
    per_kind = {kind: _file_years(grid_dir, kind) for kind in kinds}
    found = set.intersection(*per_kind.values())
    in_window = sorted(year for year in found if start <= year <= end)
    if not in_window:
        detail = {kind: sorted(years) for kind, years in per_kind.items()}
        raise ValueError(f"数据目录在期望窗口 {start}-{end} 内没有任何年份: {grid_dir}，各类型实际年份 {detail}")

    runs: list[list[int]] = []
    for year in in_window:
        if runs and year == runs[-1][-1] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    best = max(runs, key=len)
    dropped = sorted(set(in_window) - set(best))
    if dropped:
        logger.warning("历史年份不连续，丢弃 %s，使用 %d-%d", dropped, best[0], best[-1])

    for year in best:
        expected = 8784 if calendar.isleap(year) else 8760
        for kind in kinds:
            path = Path(grid_dir) / kind / f"hainan_{kind}_{year}.csv"
            rows = _row_count(path)
            if rows != expected:
                raise ValueError(f"{path} 数据行数 {rows} ≠ {year} 年应有的 {expected} 行，数据不完整")

    if len(best) < MIN_HISTORY_YEARS:
        raise ValueError(
            f"历史年份数不足: 实际 {len(best)} 年（{best[0]}-{best[-1]}），下限 {MIN_HISTORY_YEARS} 年"
        )
    missing = sorted(set(range(start, end + 1)) - set(best))
    if missing:
        logger.warning("期望窗口 %d-%d 缺 %s，实际使用 %d-%d", start, end, missing, best[0], best[-1])
    return YearsPlan(tuple(best), tuple(best[:-1]), best[-1])
