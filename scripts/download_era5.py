#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : download_era5.py

从哥白尼 CDS 下载 ERA5 逐小时再分析（netCDF）到 data/nc/，全国域
（lat 18-54、lon 73-136，0.25°，与全国格点地市归属表同网格）。

下载内容与口径（D-52 后风电输出年温压的北京时间换算需头尾各多 8 小时）：
    - 2m_temperature + surface_pressure，2000-2025 整年
    - 另加 1999-12-31、2026-01-01 两个单日（北京时间年首尾的真实数据）

用法（仓库根目录）:
    python scripts/download_era5.py            # 断点续传：已存在的文件跳过
    python scripts/download_era5.py --dry-run  # 只列计划，不请求
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cdsapi

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "nc"
DATASET = "reanalysis-era5-single-levels"
AREA = [54, 73, 18, 136]  # 北/西/南/东，含 54N/136E
VARIABLES = ["2m_temperature", "surface_pressure"]
RETRIES, BACKOFF_S = 10, 120

# (文件名, 日期区间)；整年一年一个文件，头尾补边单日；从近年往远年（2025 最先到手）
CHUNKS = [(f"era5_t2m_sp_{y}.nc", f"{y}-01-01/{y}-12-31") for y in range(2025, 1999, -1)]
CHUNKS[1:1] = [("era5_t2m_sp_2024-12-31.nc", "2024-12-31/2024-12-31"),
               ("era5_t2m_sp_2026-01-01.nc", "2026-01-01/2026-01-01")]
CHUNKS.append(("era5_t2m_sp_1999-12-31.nc", "1999-12-31/1999-12-31"))


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 ERA5 t2m/sp 全国域逐小时 netCDF")
    parser.add_argument("--dry-run", action="store_true", help="只列计划不请求")
    parser.add_argument("--only", nargs="+", metavar="关键字", help="只下文件名含任一关键字的块，如 --only 2025")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks = [(n, s) for n, s in CHUNKS if not args.only or any(k in n for k in args.only)]
    todo = [(name, span) for name, span in chunks if not (OUT_DIR / name).exists() or (OUT_DIR / name).stat().st_size == 0]
    done = [name for name, _ in chunks if name not in [t[0] for t in todo]]
    print(f"计划 {len(chunks)} 个文件，已完成 {len(done)}，待下载 {len(todo)} → {OUT_DIR}", flush=True)
    for name, span in todo:
        print(f"  待下载: {name} ({span})", flush=True)
    if args.dry_run or not todo:
        return

    client = cdsapi.Client(timeout=600, progress=False)
    for i, (name, span) in enumerate(todo, 1):
        target = OUT_DIR / name
        request = {
            "variable": VARIABLES,
            "date": span,
            "time": [f"{h:02d}:00" for h in range(24)],
            "area": AREA,
            "data_format": "netcdf",
            "download_format": "unarchived",
        }
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"[{i}/{len(todo)}] 请求 {span} (尝试 {attempt})，排队中...", flush=True)
                t0 = time.perf_counter()
                client.retrieve(DATASET, request, str(target))
                print(f"[{i}/{len(todo)}] 完成 {name}: {target.stat().st_size / 1e6:.1f} MB，"
                      f"耗时 {(time.perf_counter() - t0) / 60:.1f} 分钟", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 - 统一重试后报具体文件
                print(f"  失败({attempt}/{RETRIES}) {name}: {exc}", file=sys.stderr, flush=True)
                target.unlink(missing_ok=True)
                if attempt == RETRIES:
                    raise RuntimeError(f"下载最终失败: {name}") from exc
                time.sleep(BACKOFF_S * attempt)
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
