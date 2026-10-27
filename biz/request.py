#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : request.py

业务层统一 JSON 请求解析：文件/dict 装载、字段校验、默认值填充、单元名匹配。

原风电/光伏两条 _parse_request 合并为 parse_request(source, kind)：公共段一份，
kind 分支只留差异（子目录集、机型/组件校验、光伏经纬度缺省）。校验失败一律
ValueError 并指明字段/文件/单元（服务层据此映射 400）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from new_energy.constants import RESOURCE_YEAR_TYPES

from .assignment import assignment_keys, city_centers, region_center

logger = logging.getLogger(__name__)

# 服务端默认路径（换区域时改这里）
_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = _ROOT / "data" / "hainan_era5_grid_csv"
# 默认归属表 = 全国表（每行一个格点数值坐标，368 单元）；海南旧表（wind_grid/solar_grid
# 列名结构）仍可经 assignment_file 传入，两种结构均兼容
DEFAULT_ASSIGNMENT = _ROOT / "data" / "全国格点地市归属.csv"
# 服务端默认装机配置（风电 = 全省 18 地市，光伏 = 儋州区域）
_BIZ_DIR = Path(__file__).resolve().parent

# kind 差异项：请求额外字段、data_dir 须含的子目录、variable_prefixes 允许的键
KIND_OPTIONS = {
    "wind": {
        "extra_fields": {"evaluate"},
        "subdirs": ("wind", "temperature", "pressure"),
        "prefix_kinds": ("wind", "temperature", "pressure"),
    },
    "solar": {
        "extra_fields": {"albedo"},
        "subdirs": ("solar", "temperature"),
        "prefix_kinds": ("solar", "temperature"),
    },
}
REQUEST_FIELDS = {
    "data_dir", "output_dir", "scenario", "city_configs", "assignment_file",
    "loss_factor", "output_city_power", "variable_prefixes",
}
WIND_CITY_FIELDS = {"city", "capacity_mw", "turbines"}
TURBINE_FIELDS = {
    "model", "count", "rated_power_mw", "hub_height_m",
    "cut_in_m_s", "rated_m_s", "cut_out_m_s", "efficiency", "wind_shear_exponent",
}
TURBINE_REQUIRED = (
    "model", "count", "rated_power_mw", "hub_height_m",
    "cut_in_m_s", "rated_m_s", "cut_out_m_s",
)
SOLAR_CITY_FIELDS = {"city", "capacity_mw", "latitude", "longitude", "components"}
SOLAR_CITY_REQUIRED = ("city", "capacity_mw", "components")
COMPONENT_FIELDS = {
    "name", "count", "rated_power_mw", "tilt_deg", "azimuth_deg", "noct_c", "gamma_p_per_c", "efficiency",
}
COMPONENT_REQUIRED = ("name", "count", "rated_power_mw", "tilt_deg", "azimuth_deg", "noct_c", "gamma_p_per_c")


def fail(msg: str) -> None:
    """抛出校验错误 ValueError。

    Args:
        msg (str): 错误信息（指明字段/文件/地市）。

    Returns:
        None
    """
    raise ValueError(msg)


def load_payload(source: dict | str | Path) -> dict:
    """统一 JSON 来源装载：dict 原样返回；str/Path 读文件。

    文件不存在/坏 JSON/根不是对象 → ValueError。

    Args:
        source (dict | str | Path): 请求配置（dict 或 JSON 文件路径）。

    Returns:
        dict: 请求配置字典。
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
    """存在 allowed 之外的键时抛 ValueError。

    Args:
        payload (dict): 请求对象。
        allowed (set[str]): 允许的字段名集合。
        ctx (str): 报错上下文（如 "scenario"）。

    Returns:
        None
    """
    unknown = sorted(set(payload) - allowed)
    if unknown:
        fail(f"{ctx}存在未知字段: {unknown}")


def string(value: object, name: str) -> str:
    """校验非空字符串，不合法则抛 ValueError。

    Args:
        value (object): 待校验值。
        name (str): 字段名（用于报错）。

    Returns:
        str: 原字符串。
    """
    if not isinstance(value, str) or not value.strip():
        fail(f"{name} 必须是非空字符串")
    return value


def number(value: object, name: str) -> float:
    """校验数字（排除 bool），不合法则抛 ValueError。

    Args:
        value (object): 待校验值。
        name (str): 字段名（用于报错）。

    Returns:
        float: 转换后的数值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{name} 必须是数字")
    return float(value)


def _parse_turbine(raw: object, index: int) -> dict:
    """校验单个机型配置，返回 power 模块所需的机型字典。

    Args:
        raw (object): 请求中的机型对象。
        index (int): 在 turbines 数组中的下标（报错定位用）。

    Returns:
        dict: 校验通过的机型配置。
    """
    ctx = f"turbines[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, TURBINE_FIELDS, ctx)
    for key in TURBINE_REQUIRED:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    count = number(raw["count"], f"{ctx}.count")
    if count != int(count) or count <= 0:
        fail(f"{ctx}.count 必须是正整数")
    rated = number(raw["rated_power_mw"], f"{ctx}.rated_power_mw")
    if rated <= 0:
        fail(f"{ctx}.rated_power_mw 必须为正")
    return {
        "model": string(raw["model"], f"{ctx}.model"),
        "count": int(count),
        "rated_power_mw": rated,
        "hub_height_m": number(raw["hub_height_m"], f"{ctx}.hub_height_m"),
        "cut_in_m_s": number(raw["cut_in_m_s"], f"{ctx}.cut_in_m_s"),
        "rated_m_s": number(raw["rated_m_s"], f"{ctx}.rated_m_s"),
        "cut_out_m_s": number(raw["cut_out_m_s"], f"{ctx}.cut_out_m_s"),
        "efficiency": number(raw.get("efficiency", 1.0), f"{ctx}.efficiency"),
        "wind_shear_exponent": number(raw.get("wind_shear_exponent", 0.15), f"{ctx}.wind_shear_exponent"),
    }


def _parse_wind_city(raw: object, index: int) -> tuple[str, dict]:
    """校验单个风电地市配置，返回 (地市键, 规范化配置)。

    Args:
        raw (object): 请求中的地市对象。
        index (int): 在 city_configs 数组中的下标（报错定位用）。

    Returns:
        tuple[str, dict]: 地市键与规范化配置（capacity_mw/turbines）。
    """
    ctx = f"city_configs[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, WIND_CITY_FIELDS, ctx)
    for key in WIND_CITY_FIELDS:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    city = string(raw["city"], f"{ctx}.city")
    turbines_raw = raw["turbines"]
    if not isinstance(turbines_raw, list) or not turbines_raw:
        fail(f"{ctx}.turbines 必须是非空数组")
    capacity = number(raw["capacity_mw"], f"{ctx}.capacity_mw")
    if capacity <= 0:
        fail(f"{ctx}.capacity_mw 必须为正")
    return city, {
        "capacity_mw": capacity,
        "turbines": [_parse_turbine(t, j) for j, t in enumerate(turbines_raw)],
    }


def _parse_component(raw: object, index: int) -> dict:
    """校验单个组件配置，返回 power 模块所需的组件字典。

    Args:
        raw (object): 请求中的组件对象。
        index (int): 在 components 数组中的下标（报错定位用）。

    Returns:
        dict: 校验通过的组件配置。
    """
    ctx = f"components[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, COMPONENT_FIELDS, ctx)
    for key in COMPONENT_REQUIRED:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    count = number(raw["count"], f"{ctx}.count")
    if count != int(count) or count <= 0:
        fail(f"{ctx}.count 必须是正整数")
    rated = number(raw["rated_power_mw"], f"{ctx}.rated_power_mw")
    if rated <= 0:
        fail(f"{ctx}.rated_power_mw 必须为正")
    return {
        "name": string(raw["name"], f"{ctx}.name"),
        "count": int(count),
        "rated_power_mw": rated,
        "tilt_deg": number(raw["tilt_deg"], f"{ctx}.tilt_deg"),
        "azimuth_deg": number(raw["azimuth_deg"], f"{ctx}.azimuth_deg"),
        "noct_c": number(raw["noct_c"], f"{ctx}.noct_c"),
        "gamma_p_per_c": number(raw["gamma_p_per_c"], f"{ctx}.gamma_p_per_c"),
        "efficiency": number(raw.get("efficiency", 1.0), f"{ctx}.efficiency"),
    }


def _parse_solar_city(raw: object, index: int) -> tuple[str, dict]:
    """校验单个光伏地市配置，返回 (地市键, 规范化配置)。

    latitude/longitude 缺省时先置 None，由 parse_request 按单元匹配规则统一补
    （命中归属表 → 归属表格点中心；未命中的单单元 → 传入数据格点坐标均值）。

    Args:
        raw (object): 请求中的地市对象。
        index (int): 在 city_configs 数组中的下标（报错定位用）。

    Returns:
        tuple[str, dict]: 地市键与规范化配置（capacity_mw/latitude/longitude/components）。
    """
    ctx = f"city_configs[{index}]"
    if not isinstance(raw, dict):
        fail(f"{ctx} 必须是对象")
    reject_unknown(raw, SOLAR_CITY_FIELDS, ctx)
    for key in SOLAR_CITY_REQUIRED:
        if key not in raw:
            fail(f"{ctx} 缺少必填字段 {key}")
    city = string(raw["city"], f"{ctx}.city")
    capacity = number(raw["capacity_mw"], f"{ctx}.capacity_mw")
    if capacity <= 0:
        fail(f"{ctx}.capacity_mw 必须为正")
    if "latitude" in raw or "longitude" in raw:
        if "latitude" not in raw or "longitude" not in raw:
            fail(f"{ctx}.latitude/longitude 须同时提供或同时缺省")
        latitude = number(raw["latitude"], f"{ctx}.latitude")
        longitude = number(raw["longitude"], f"{ctx}.longitude")
        if not -90 <= latitude <= 90:
            fail(f"{ctx}.latitude 必须在 [-90, 90]")
        if not -180 <= longitude <= 180:
            fail(f"{ctx}.longitude 必须在 [-180, 180]")
    else:
        latitude = longitude = None
    components_raw = raw["components"]
    if not isinstance(components_raw, list) or not components_raw:
        fail(f"{ctx}.components 必须是非空数组")
    components = [_parse_component(c, j) for j, c in enumerate(components_raw)]
    return city, {
        "capacity_mw": capacity,
        "latitude": latitude,
        "longitude": longitude,
        "components": components,
    }


def parse_request(source: dict | str | Path, kind: str) -> dict:
    """校验统一 JSON 并填充默认值，返回规范化配置（全项目唯一校验点）。

    Args:
        source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。
        kind (str): 算法类型（wind/solar）。

    Returns:
        dict: 规范化配置（data_dir/assignment_file/output_dir 为 Path，city_configs 已转为按键字典）。
    """
    if kind not in KIND_OPTIONS:
        fail(f"未知的算法类型: {kind}")
    opts = KIND_OPTIONS[kind]
    payload = load_payload(source)
    reject_unknown(payload, REQUEST_FIELDS | opts["extra_fields"], "请求")

    if "output_dir" not in payload:
        fail("请求缺少必填字段 output_dir")
    output_dir = Path(string(payload["output_dir"], "output_dir"))

    if "data_dir" in payload:
        data_dir = Path(string(payload["data_dir"], "data_dir"))
    else:
        data_dir = DEFAULT_DATA_DIR
        logger.info("data_dir 未传，使用默认数据目录: %s", data_dir)
    if "assignment_file" in payload:
        assignment_file = Path(string(payload["assignment_file"], "assignment_file"))
    else:
        assignment_file = DEFAULT_ASSIGNMENT
    if not assignment_file.exists():
        fail(f"归属表不存在: {assignment_file}")
    for sub in opts["subdirs"]:
        if not (data_dir / sub).is_dir():
            fail(f"data_dir 下缺少子目录 {sub}: {data_dir / sub}")

    if "scenario" not in payload:
        fail("请求缺少必填字段 scenario")
    scenario = payload["scenario"]
    if not isinstance(scenario, dict):
        fail("scenario 必须是对象")
    reject_unknown(scenario, {"year", "resource_year_type"}, "scenario")
    for key in ("year", "resource_year_type"):
        if key not in scenario:
            fail(f"scenario 缺少必填字段 {key}")
    year = number(scenario["year"], "scenario.year")
    if year != int(year):
        fail("scenario.year 必须是整数")
    resource_year_type = string(scenario["resource_year_type"], "scenario.resource_year_type")
    if resource_year_type not in RESOURCE_YEAR_TYPES:
        fail(f"scenario.resource_year_type 必须是 {RESOURCE_YEAR_TYPES} 之一")

    if "city_configs" in payload:
        raw_cities = payload["city_configs"]
    else:
        default_configs = _BIZ_DIR / f"default_city_configs_{kind}.json"
        raw_cities = json.loads(default_configs.read_text(encoding="utf-8"))["city_configs"]
        if kind == "wind":
            logger.info("city_configs 未传，使用默认全省机组配置: %s (%d 地市)",
                        default_configs.name, len(raw_cities))
        else:
            logger.info("city_configs 未传，使用默认机组配置（儋州区域）: %s (%d 地市)",
                        default_configs.name, len(raw_cities))
    if not isinstance(raw_cities, list) or not raw_cities:
        fail("city_configs 必须是非空数组")
    valid_keys = assignment_keys(assignment_file)
    city_configs: dict[str, dict] = {}
    for i, raw in enumerate(raw_cities):
        if kind == "wind":
            city, cfg_city = _parse_wind_city(raw, i)
        else:
            city, cfg_city = _parse_solar_city(raw, i)
        if city in city_configs:
            fail(f"city_configs 存在重复地市: {city}")
        city_configs[city] = cfg_city

    # 单元名匹配规则（D-29）：全命中归属表 → 归属表给格点；多单元含未命中 → 违法数据；
    # 单单元未命中 → 合法自定义区域，格点 = 传入数据中声明前缀的全部格点列
    unmatched = [city for city in city_configs if city not in valid_keys]
    if unmatched and len(city_configs) > 1:
        fail(f"city_configs 含归属表之外的地市: {unmatched}")
    direct_unit = unmatched[0] if unmatched else None
    if direct_unit:
        logger.info("单单元 %s 未命中归属表，按传入数据全部格点列聚合", direct_unit)

    raw_prefixes = payload.get("variable_prefixes", {})
    if not isinstance(raw_prefixes, dict):
        fail("variable_prefixes 必须是对象")
    reject_unknown(raw_prefixes, set(opts["prefix_kinds"]), "variable_prefixes")
    variable_prefixes = {k: string(v, f"variable_prefixes.{k}") for k, v in raw_prefixes.items()}

    if kind == "solar":
        # 补 latitude/longitude 缺省：命中单元用归属表格点中心，未命中的单单元用数据格点坐标均值
        centers = city_centers(assignment_file)
        for city, cfg_city in city_configs.items():
            if cfg_city["latitude"] is not None:
                continue
            if city == direct_unit:
                cfg_city["latitude"], cfg_city["longitude"] = region_center(data_dir, "solar", variable_prefixes)
            elif city in centers:
                cfg_city["latitude"], cfg_city["longitude"] = centers[city]
            else:
                fail(f"city_configs 未提供 {city} 的 latitude/longitude，且归属表无该地市格点中心")

    cfg = {
        "data_dir": data_dir,
        "assignment_file": assignment_file,
        "output_dir": output_dir,
        "year": int(year),
        "resource_year_type": resource_year_type,
        "city_configs": city_configs,
        "direct_unit": direct_unit,
        "variable_prefixes": variable_prefixes,
        "loss_factor": number(payload.get("loss_factor", 1.0), "loss_factor"),
        "output_city_power": bool(payload.get("output_city_power", True)),
    }
    if kind == "wind":
        cfg["evaluate"] = bool(payload.get("evaluate", False))
    else:
        cfg["albedo"] = number(payload.get("albedo", 0.2), "albedo")
    return cfg
