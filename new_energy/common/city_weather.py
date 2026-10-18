# -*- coding: utf-8 -*-
"""格点 CSV + 归属表 → 各地市气象序列（一次读入，全部地市同时聚合）。

替代旧流程的两处文件接力：hainan_city_weather_csv_strict_inside 地市风速文件
和 read_city_weather() 的逐市重复读文件。时间保持原始 UTC 口径，+8 转北京时间
由调用方按各自流程处理。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

ENC = "utf-8-sig"

logger = logging.getLogger(__name__)

# kind → (文件名/子目录名, 格点列前缀)
KIND_PREFIX = {
    "wind": "wind_speed_100m",
    "solar": "irradiance_w_m2",
    "temperature": "temperature_2m_k",
    "pressure": "surface_pressure_pa",
}


def parse_grid_name(value: str) -> tuple[float, float]:
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
    """读 kind 类型的逐年格点 CSV，返回 {地市键: 逐年拼接的 Series(UTC 时间索引)}。

    grid_dir 形如 .../hainan_era5_grid_csv，函数读 grid_dir/kind/hainan_{kind}_{year}.csv。
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
