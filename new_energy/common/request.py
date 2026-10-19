#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : request.py

统一 JSON 请求的公共解析件：文件/dict 装载、字段类型校验、归属表地市键。

风电/光伏 pipeline 的 _parse_request 共用；所有校验失败抛 ValueError 并指明
字段/文件/地市（服务层据此映射 400）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ENC = "utf-8-sig"

# 服务端默认路径（换区域时改这里）
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _ROOT / "data" / "hainan_era5_grid_csv"
DEFAULT_ASSIGNMENT = _ROOT / "data" / "海南地市分区" / "海南共同80格点_地市归属.csv"

# 服务端历史口径默认值（扩历史范围时改这里）
DEFAULT_YEARS = (2000, 2025)  # 历史数据期望窗口，两端年份均包含；数据超出窗口不纳入
MIN_HISTORY_YEARS = 12  # 历史年份下限，低于此值报错（三类资源年各需足够样本）
RANDOM_SEED = 20261008  # 场景生成随机种子（风电/光伏共用同一复现口径）

RESOURCE_YEAR_TYPES = ("偏小资源年", "正常资源年", "偏大资源年")


def fail(msg: str) -> None:
    raise ValueError(msg)


def load_payload(source: dict | str | Path) -> dict:
    """统一 JSON 来源装载：dict 原样返回；str/Path 读文件。

    文件不存在/坏 JSON/根不是对象 → ValueError。
    """
    if isinstance(source, dict):
        return source
    path = Path(source)
    if not path.exists():
        fail(f"配置文件不存在: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"配置文件不是合法 JSON: {path} ({exc})")
    if not isinstance(payload, dict):
        fail("配置文件根必须是 JSON 对象")
    return payload


def reject_unknown(payload: dict, allowed: set[str], ctx: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        fail(f"{ctx}存在未知字段: {unknown}")


def string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{name} 必须是非空字符串")
    return value


def number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{name} 必须是数字")
    return float(value)


def assignment_keys(assignment_csv: Path) -> set[str]:
    """读归属表，返回全部合法地市键集合（行政区代码_地市名）。
"""
    table = pd.read_csv(assignment_csv, encoding=ENC)
    return {f"{int(code)}_{city}" for code, city in zip(table["city_code"], table["city"])}
