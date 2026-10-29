#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : download_era5_google.py

从 Google ARCO-ERA5 公共镜像（zarr，免账号）下载 ERA5 逐小时再分析（netCDF）
到 data/nc/，全国域（lat 18-54、lon 73-136，0.25°，与全国格点地市归属表同网格）。

与 scripts/download_era5.py（哥白尼 CDS）同口径：CDS 服务端 2026-10 起同月多日期
请求必失败（Duplicate value for month，服务端 bug），改走镜像。数据一致性已实测：
与 CDS 下的 2024-12-31 文件逐点比对，t2m 差 8e-4 K、sp 差 0.44 Pa（存储精度级）。

输出与 CDS 文件同 schema（变量 t2m/sp、时间维 valid_time、纬度降序），zlib 压缩
（不压缩约 69 GB 超 C 盘空间，压缩后约 30 GB）。

用法（仓库根目录）:
    python scripts/download_era5_google.py            # 断点续传：已存在的文件跳过
    python scripts/download_era5_google.py --dry-run  # 只列计划，不下载
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import gcsfs
import xarray as xr

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "nc"
STORE = "gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
LAT_SLICE, LON_SLICE = slice(54, 18), slice(73, 136)  # ARCO 纬度降序、经度升序
VARS = {"2m_temperature": "t2m", "surface_pressure": "sp"}  # ARCO 名 -> CDS 名
RETRIES, BACKOFF_S = 5, 120

# (文件名, 起始时刻, 结束时刻)；整年一年一个文件，头尾补边单日；近年最先到手。
CHUNKS = [(f"era5_t2m_sp_{y}.nc", f"{y}-01-01", f"{y}-12-31T23:00") for y in range(2025, 1999, -1)]
CHUNKS[1:1] = [("era5_t2m_sp_2024-12-31.nc", "2024-12-31", "2024-12-31T23:00"),
               ("era5_t2m_sp_2026-01-01.nc", "2026-01-01", "2026-01-01T23:00")]
CHUNKS.append(("era5_t2m_sp_1999-12-31.nc", "1999-12-31", "1999-12-31T23:00"))


def main() -> None:
    parser = argparse.ArgumentParser(description="从 Google ARCO-ERA5 镜像下载 t2m/sp 全国域逐小时 netCDF")
    parser.add_argument("--dry-run", action="store_true", help="只列计划不下载")
    parser.add_argument("--only", nargs="+", metavar="关键字", help="只下文件名含任一关键字的块，如 --only 2025")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks = [(n, a, b) for n, a, b in CHUNKS if not args.only or any(k in n for k in args.only)]
    todo = [(n, a, b) for n, a, b in chunks
            if not (OUT_DIR / n).exists() or (OUT_DIR / n).stat().st_size == 0]
    done = [n for n, _, _ in chunks if n not in [t[0] for t in todo]]
    print(f"计划 {len(chunks)} 个文件，已完成 {len(done)}，待下载 {len(todo)} → {OUT_DIR}", flush=True)
    for name, t_a, t_b in todo:
        print(f"  待下载: {name} ({t_a} ~ {t_b})", flush=True)
    if args.dry_run or not todo:
        return

    fs = gcsfs.GCSFileSystem(token="anon")
    ds = xr.open_zarr(fs.get_mapper(STORE), consolidated=True)[list(VARS)]
    encoding = {v: {"zlib": True, "complevel": 4} for v in VARS.values()}
    for i, (name, t_a, t_b) in enumerate(todo, 1):
        target = OUT_DIR / name
        part = target.with_suffix(".nc.part")
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"[{i}/{len(todo)}] 下载 {name} ({t_a} ~ {t_b}, 逐小时) (尝试 {attempt})...", flush=True)
                t0 = time.perf_counter()
                sub = ds.sel(time=slice(t_a, t_b), latitude=LAT_SLICE, longitude=LON_SLICE)
                sub = sub.rename(VARS).rename({"time": "valid_time"})
                sub.load().to_netcdf(part, engine="netcdf4", encoding=encoding)
                part.replace(target)
                print(f"[{i}/{len(todo)}] 完成 {name}: {target.stat().st_size / 1e9:.2f} GB，"
                      f"耗时 {(time.perf_counter() - t0) / 60:.1f} 分钟", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 - 统一重试后报具体文件
                print(f"  失败({attempt}/{RETRIES}) {name}: {exc}", file=sys.stderr, flush=True)
                part.unlink(missing_ok=True)
                if attempt == RETRIES:
                    raise RuntimeError(f"下载最终失败: {name}") from exc
                time.sleep(BACKOFF_S * attempt)
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
