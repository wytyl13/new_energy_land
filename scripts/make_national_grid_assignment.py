#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27 09:53
@Author  : weiyutao
@File    : make_national_grid_assignment.py

全国气象格点地市归属表生成器：全国网格海陆分类.csv 的格点坐标 × DataV 政区边界
（geo.datav.aliyun.com，逐省下载）→ 点在多边形判断 → 全国格点地市归属.csv。

约定（与用户确认）：
    - 与海陆表分开放，只共享坐标键（中心纬度_N/中心经度_E，两位小数，utf-8-sig）
    - 只存坐标不存格点列名；只写 inside 行，海点/境外点不指派（不写 nearest）
    - 直辖市取下一级区县子要素（bound/{code}_full.json；0 格点的区不建单元——
      无数据即无单元，不借点）；港澳台整体作为一个单元（bound/{code}.json 单要素）；
      其余省份用 bound/{code}_full.json 的下一级地市子要素（海南与既有 18 地市表同口径）
    - 一个格点命中多个单元：相邻行政区共享边上的精确命中属数据固有现象，
      按 city_code 小者得仲裁并逐条留痕；仲裁数超过上限视为边界数据重叠损坏，报错

注意：DataV 边界为简化几何（约 GCJ-02 口径），与原始 shapefile 判定在边缘点
（距边界数百米内）可能有个别差异；差异规模见运行后的交叉核对，不在本脚本内修正。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

ROOT = Path(__file__).resolve().parent.parent  # scripts/ 的上一级 = 项目根
LAND_SEA_CSV = ROOT / "data" / "全国网格海陆分类.csv"
OUT_CSV = ROOT / "data" / "全国格点地市归属.csv"
BOUND_URL = "https://geo.datav.aliyun.com/areas_v3/bound/{}.json"
RETRIES, TIMEOUT = 3, 60
EXPECTED_ROWS = 145 * 253  # 海陆表应有行数（纬 18–54 × 经 73–136，0.25°）
# 完整性容差：网格含大片境外陆地（蒙古/俄/哈/日等），"未归属"只对国界内判定；
# 国界内未归属 / 海点被归属 超容差视为系统性缺失，报错不写盘
CHINA_MISSED_TOLERANCE = 0.01
ASSIGNED_SEA_TOLERANCE = 0.01
MAX_BORDER_ARBITRATIONS = 20  # 共享边精确命中仲裁数上限，超出视为边界数据重叠损坏

# (adcode, 省名, 是否整体单单元)；单单元=港澳台，多单元=取下一级子要素（直辖市=区县、其余=地市）
PROVINCES = [
    (110000, "北京市", False), (120000, "天津市", False), (310000, "上海市", False),
    (500000, "重庆市", False), (130000, "河北省", False), (140000, "山西省", False),
    (150000, "内蒙古自治区", False), (210000, "辽宁省", False), (220000, "吉林省", False),
    (230000, "黑龙江省", False), (320000, "江苏省", False), (330000, "浙江省", False),
    (340000, "安徽省", False), (350000, "福建省", False), (360000, "江西省", False),
    (370000, "山东省", False), (410000, "河南省", False), (420000, "湖北省", False),
    (430000, "湖南省", False), (440000, "广东省", False), (450000, "广西壮族自治区", False),
    (460000, "海南省", False), (510000, "四川省", False), (520000, "贵州省", False),
    (530000, "云南省", False), (540000, "西藏自治区", False), (610000, "陕西省", False),
    (620000, "甘肃省", False), (630000, "青海省", False), (640000, "宁夏回族自治区", False),
    (650000, "新疆维吾尔自治区", False), (710000, "台湾省", True), (810000, "香港特别行政区", True),
    (820000, "澳门特别行政区", True),
]


def download(code: int | str) -> dict:
    """下载并解析一个边界 GeoJSON（带本地缓存），失败重试，最终失败即报错退出。"""
    url = BOUND_URL.format(code)
    cache = Path(os.environ.get("TEMP", ".")) / "datav_bound"
    cached = cache / f"{code}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            cache.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload
        except Exception as exc:  # noqa: BLE001 - 统一重试后报具体 URL
            print(f"  下载失败({attempt}/{RETRIES}) {url}: {exc}", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"边界下载最终失败: {url}")


def rings_of(geometry: dict) -> list[tuple[list, list[list]]]:
    """把 Polygon/MultiPolygon 拆成 (外环, [内环...]) 列表；内环=洞（湖泊飞地等）。"""
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [(poly[0], poly[1:]) for poly in polys if poly]


def unit_mask(geometry: dict, pts_lonlat: np.ndarray) -> np.ndarray:
    """该单元的格点命中掩码：落在任一外环内且不在内环（洞）里。

    pts_lonlat 列序为（经度, 纬度），与 GeoJSON 环顶点 [x=经度, y=纬度] 同空间。
    """
    hit = np.zeros(len(pts_lonlat), dtype=bool)
    for ext, holes in rings_of(geometry):
        hit |= MplPath(np.asarray(ext, dtype=float)).contains_points(pts_lonlat)
        for hole in holes:
            hit &= ~MplPath(np.asarray(hole, dtype=float)).contains_points(pts_lonlat)
    return hit


def main() -> None:
    grid = pd.read_csv(LAND_SEA_CSV, encoding="utf-8-sig")
    if len(grid) != EXPECTED_ROWS:
        raise ValueError(f"海陆表行数 {len(grid)} ≠ 网格应有 {EXPECTED_ROWS} 行: {LAND_SEA_CSV}")
    for col in ("中心纬度_N", "中心经度_E", "海陆判定"):
        if grid[col].isna().any():
            bad = (grid.index[grid[col].isna()].tolist())[:5]
            raise ValueError(f"海陆表 {col} 列存在缺值，行号示例 {bad}: {LAND_SEA_CSV}")
    if not grid["海陆判定"].isin([0, 1]).all():
        raise ValueError(f"海陆表 海陆判定 列存在非 0/1 值: {LAND_SEA_CSV}")
    pts = grid[["中心纬度_N", "中心经度_E"]].to_numpy(float)
    pts_lonlat = pts[:, ::-1]
    owner = np.full(len(pts), -1, dtype=int)  # 格点 → 单元下标
    units: list[tuple[int, str, int, str]] = []  # (province_code, province, city_code, city)
    overlaps: list[str] = []  # 共享边命中仲裁留痕

    for code, name, single in PROVINCES:
        key = str(code) if single else f"{code}_full"
        data = download(key)
        features = data["features"]
        if not features:
            raise ValueError(f"{name} 边界 features 为空: {BOUND_URL.format(key)}")
        rows = []
        for fe in features:
            prop = fe["properties"]
            rows.append((code, name, int(prop["adcode"]), str(prop["name"]), fe["geometry"]))
        print(f"{name}: {len(rows)} 个单元")
        for pcode, pname, ccode, cname, geom in rows:
            idx = len(units)
            units.append((pcode, pname, ccode, cname))
            for i in np.flatnonzero(unit_mask(geom, pts_lonlat)):
                prev = owner[i]
                if prev < 0:
                    owner[i] = idx
                else:
                    old = units[prev]
                    winner = (pname, cname) if ccode < old[2] else (old[1], old[3])
                    owner[i] = idx if ccode < old[2] else prev
                    overlaps.append(
                        f"格点({pts[i, 0]:.2f},{pts[i, 1]:.2f}) 同命中 "
                        f"{old[1]}{old[3]} 与 {pname}{cname}，判给 {winner[0]}{winner[1]}"
                    )

    assigned = owner >= 0
    if len(overlaps) > MAX_BORDER_ARBITRATIONS:
        raise ValueError(f"共享边命中仲裁 {len(overlaps)} 处，超过上限 {MAX_BORDER_ARBITRATIONS}，疑似边界数据重叠损坏")
    for line in overlaps:
        print("边界仲裁:", line)

    # 完整性判据：国界内的点必须几乎全部归属（境外陆地未归属属正常，不算异常）
    china = unit_mask(download(100000)["features"][0]["geometry"], pts_lonlat)
    missed_in_china = int((china & ~assigned).sum())
    if missed_in_china > CHINA_MISSED_TOLERANCE * int(china.sum()):
        raise ValueError(
            f"国界内未归属 {missed_in_china} 个，超过国界内点 {CHINA_MISSED_TOLERANCE:.0%} 容差，"
            f"疑似边界数据系统性缺失，拒写输出"
        )
    land = grid["海陆判定"].to_numpy(int) == 0
    sea_assigned_idx = np.flatnonzero(assigned & ~land)
    assigned_sea = len(sea_assigned_idx)
    if assigned_sea:
        counts = pd.Series(
            [f"{units[owner[i]][1]}·{units[owner[i]][3]}" for i in sea_assigned_idx]
        ).value_counts()
        print("归属但标海分布（海岸线/湖泊口径差异，前 10）:")
        print(counts.head(10).to_string())
    if assigned_sea > ASSIGNED_SEA_TOLERANCE * int(assigned.sum()):
        raise ValueError(
            f"海点被归属 {assigned_sea} 个，超过归属点 {ASSIGNED_SEA_TOLERANCE:.0%} 容差，"
            f"疑似几何判定错误（如内环扣除失效），拒写输出"
        )

    pcode, pname, ccode, cname = zip(*units)
    out = pd.DataFrame(
        {
            "中心纬度_N": pts[assigned, 0],
            "中心经度_E": pts[assigned, 1],
            "province_code": np.array(pcode, dtype=object)[owner[assigned]],
            "province": np.array(pname, dtype=object)[owner[assigned]],
            "city_code": np.array(ccode, dtype=object)[owner[assigned]],
            "city": np.array(cname, dtype=object)[owner[assigned]],
            "assignment_method": "inside",
        }
    ).sort_values(["中心纬度_N", "中心经度_E"], ascending=[False, True])
    if out.duplicated(["中心纬度_N", "中心经度_E"]).any():
        raise ValueError("输出存在重复格点行")
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig", float_format="%.2f")

    print(f"\n单元总数 {len(units)}；归属格点 {int(assigned.sum())} / {len(pts)}"
          f"（国界内格点 {int(china.sum())}，海陆表陆地格点 {int(land.sum())}，"
          f"含境外陆地）")
    print(f"国界内未归属: {missed_in_china} 个（容差内，边界简化差异）")
    print(f"归属但标海:   {assigned_sea} 个（容差内，海岸线口径差异）")
    print(f"已写出: {OUT_CSV}")


if __name__ == "__main__":
    main()
