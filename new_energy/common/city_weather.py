#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : city_weather.py

格点 CSV + 归属表 → 各地市气象序列（一次读入，全部地市同时聚合）。

替代旧流程的两处文件接力：hainan_city_weather_csv_strict_inside 地市风速文件
和 read_city_weather() 的逐市重复读文件。时间保持原始 UTC 口径，+8 转北京时间
由调用方按各自流程处理。
"""
from __future__ import annotations

import calendar
import logging
import re
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from .request import DEFAULT_YEARS, ENC, MIN_HISTORY_YEARS

logger = logging.getLogger(__name__)

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


def load_city_series(
    grid_dir: Path,
    assignment_csv: Path,
    kind: str,
    years: range | list[int] | tuple[int, ...],
) -> dict[str, pd.Series]:
    """读 kind 类型的逐年格点 CSV，按地市聚合为逐年拼接的序列。

    grid_dir 形如 .../hainan_era5_grid_csv，函数读 grid_dir/kind/hainan_{kind}_{year}.csv。

    Args:
        grid_dir (Path): 格点数据根目录。
        assignment_csv (Path): 格点归属表 CSV 路径。
        kind (str): 数据类型（wind/solar/temperature/pressure）。
        years (range | list[int] | tuple[int, ...]): 要读取的年份序列。

    Returns:
        dict[str, pd.Series]: 地市键 → 逐年拼接 Series（UTC 时间索引，严格边界内格点均值）。
    """
    prefix = KIND_PREFIX[kind]
    assignment = pd.read_csv(assignment_csv, encoding=ENC)

    # 列名前缀按 kind 区分：风速格点列在 wind_grid 字段，温压格点列同名规则
    columns_by_city: dict[str, list[str]] = {}
    inside = assignment.loc[assignment["assignment_method"].eq("inside")]
    for (code, city), block in inside.groupby(["city_code", "city"], sort=True):
        names = block["wind_grid"] if kind == "wind" else block["solar_grid"]
        cols = []
        for name in names:
            lat, lon = parse_grid_name(name)
            cols.append(f"{prefix}_lat_{lat:.2f}_lon_{lon:.2f}")
        columns_by_city[f"{int(code)}_{city}"] = cols

    pieces: dict[str, list[pd.Series]] = {key: [] for key in columns_by_city}
    for year in years:
        path = Path(grid_dir) / kind / f"hainan_{kind}_{year}.csv"
        if not path.exists():
            raise ValueError(f"数据文件不存在: {path}")
        logger.info("读取格点文件: %s (%d 个地市)", path.relative_to(grid_dir), len(columns_by_city))
        data = pd.read_csv(path, encoding=ENC)
        time = pd.to_datetime(data["time"], errors="coerce")
        for key, cols in columns_by_city.items():
            missing = sorted(set(cols) - set(data.columns))
            if missing:
                raise ValueError(f"{path.name} 缺少{key}的格点列: {missing[:3]}...")
            values = data[cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
            pieces[key].append(pd.Series(values.to_numpy(float), index=time, name=key))

    return {
        key: pd.concat(parts).sort_index() for key, parts in pieces.items()
    }


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
