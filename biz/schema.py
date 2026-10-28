#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : schema.py

业务层请求结构与校验（D-50/D-51）：统一 JSON → 请求对象（直接构造即校验）。
未知/缺字段由 dataclass __init__ 抛 TypeError（指名字段），数值范围/类型由
__post_init__ 抛 ValueError；文件存在性与前缀身份是仅剩的两项手工检查。
单元名匹配与光伏经纬度补全不在本层（需归属表，属总装层 Runner 职责）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from new_energy.config import SolarComponent, SolarUnit, Turbine, WindUnit
from new_energy.constants import RESOURCE_YEAR_TYPES

logger = logging.getLogger(__name__)

# 服务端默认路径（换区域时改这里）
_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = _ROOT / "data" / "hainan_era5_grid_csv"
# 默认归属表 = 全国表（每行一个格点数值坐标，368 单元）；海南旧表（wind_grid/solar_grid
# 列名结构）仍可经 assignment_file 传入，两种结构均兼容
DEFAULT_ASSIGNMENT = _ROOT / "data" / "全国格点地市归属.csv"
_BIZ_DIR = Path(__file__).resolve().parent

# 各 kind 的数据子目录，兼作 variable_prefixes 允许的变量键（D-29）
SUBDIRS = {
    "wind": ("wind", "temperature", "pressure"),
    "solar": ("solar", "temperature"),
}


@dataclass(frozen=True)
class Scenario:
    """请求 scenario 段：输出年份与资源年类型。"""

    year: int
    resource_year_type: str

    def __post_init__(self) -> None:
        if isinstance(self.year, bool) or not isinstance(self.year, (int, float)) or self.year != int(self.year):
            raise ValueError("year 必须是整数")
        if self.resource_year_type not in RESOURCE_YEAR_TYPES:
            raise ValueError(f"resource_year_type 必须是 {RESOURCE_YEAR_TYPES} 之一")


@dataclass(frozen=True)
class WindCityConfig(WindUnit):
    """风电 city_configs 数组项：city + 机组配置同层（city 默认空串是继承字段序约束，__post_init__ 查非空）。"""

    city: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.city.strip():
            raise ValueError("city 必须是非空字符串")


@dataclass(frozen=True)
class SolarCityConfig(SolarUnit):
    """光伏 city_configs 数组项：city + 组件配置同层（city 默认空串是继承字段序约束，__post_init__ 查非空）。"""

    city: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.city.strip():
            raise ValueError("city 必须是非空字符串")


@dataclass(frozen=True)
class RequestBase:
    """统一 JSON 请求信封（wind/solar 共有字段，差异字段在子类）。"""

    output_dir: Path
    scenario: Scenario
    city_configs: dict[str, WindUnit | SolarUnit]
    data_dir: Path = DEFAULT_DATA_DIR
    assignment_file: Path = DEFAULT_ASSIGNMENT
    variable_prefixes: dict[str, str] = field(default_factory=dict)
    loss_factor: float = 1.0
    output_city_power: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.loss_factor, bool) or not isinstance(self.loss_factor, (int, float)):
            raise ValueError("loss_factor 必须是数字")


@dataclass(frozen=True)
class WindRequest(RequestBase):
    """风电请求：共有信封 + evaluate。"""

    evaluate: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.evaluate, bool):
            raise ValueError("evaluate 必须是布尔值")


@dataclass(frozen=True)
class SolarRequest(RequestBase):
    """光伏请求：共有 + albedo。"""

    albedo: float = 0.2

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.albedo, bool) or not isinstance(self.albedo, (int, float)):
            raise ValueError("albedo 必须是数字")


class Schema:
    """JSON 请求 → 请求对象（WindRequest/SolarRequest）。"""

    def __init__(self, kind: str):
        """记录并校验算法类型。

        Args:
            kind (str): 算法类型（wind/solar）。
        """
        self.kind = kind
        if kind not in SUBDIRS:
            raise ValueError(f"未知的算法类型: {kind}")

    def forward(self, source: dict | str | Path) -> RequestBase:
        """装载统一 JSON、直接构造请求对象（构造即校验）。

        Args:
            source (dict | str | Path): 统一 JSON 的 dict，或 JSON 文件路径。

        Returns:
            RequestBase: WindRequest 或 SolarRequest（路径为 Path，city_configs
            为 单元键 → WindCityConfig/SolarCityConfig；不含单元匹配与经纬度
            补全，属 Runner）。
        """
        # 1 装载：dict 直传；str/Path 读 JSON 文件（坏 JSON 的 JSONDecodeError 本身是 ValueError 子类）
        if not isinstance(source, dict) and not Path(source).exists():
            raise ValueError(f"配置文件不存在: {source}")
        payload = source if isinstance(source, dict) else json.loads(Path(source).read_text("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置文件根必须是 JSON 对象")

        # 2 city_configs：缺省读服务端默认机组；逐项直接构造（未知/缺字段 → TypeError，范围 → __post_init__）
        if "city_configs" not in payload:
            default_configs = _BIZ_DIR / f"default_city_configs_{self.kind}.json"
            payload = {**payload, "city_configs": json.loads(
                default_configs.read_text(encoding="utf-8"))["city_configs"]}
            logger.info("city_configs 未传，使用默认机组配置: %s", default_configs.name)
        city_configs: dict[str, WindUnit | SolarUnit] = {}
        for raw in payload["city_configs"]:
            if self.kind == "wind":
                entry = WindCityConfig(**{**raw, "turbines": tuple(Turbine(**t) for t in raw["turbines"])})
            else:
                entry = SolarCityConfig(**{**raw, "components": tuple(SolarComponent(**c) for c in raw["components"])})
            if entry.city in city_configs:
                raise ValueError(f"city_configs 存在重复地市: {entry.city}")
            city_configs[entry.city] = entry

        # 3 构造请求对象：缺省字段走类默认值；str 路径转 Path
        if "scenario" not in payload:
            raise ValueError("请求缺少必填字段 scenario")
        request_cls = WindRequest if self.kind == "wind" else SolarRequest
        req = request_cls(**{**payload,
                             "scenario": Scenario(**payload["scenario"]),
                             "city_configs": city_configs,
                             **{k: Path(payload[k]) for k in ("output_dir", "data_dir", "assignment_file")
                                if k in payload}})

        # 4 文件存在性（构造管不了的文件系统检查）
        if not req.assignment_file.exists():
            raise ValueError(f"归属表不存在: {req.assignment_file}")
        for sub in SUBDIRS[self.kind]:
            if not (req.data_dir / sub).is_dir():
                raise ValueError(f"data_dir 下缺少子目录 {sub}: {req.data_dir / sub}")

        # 5 变量前缀身份：键须属于本 kind 的变量集（D-29）
        unknown = sorted(set(req.variable_prefixes) - set(SUBDIRS[self.kind]))
        if unknown:
            raise ValueError(f"variable_prefixes存在未知字段: {unknown}")
        return req

    __call__ = forward


if __name__ == "__main__":
    """冒烟自检：合法构造输出字段，非法输入输出报错文案。运行: python -m biz.schema"""
    import copy

    fixture = _ROOT / "tests" / "fixtures" / "wind_single_city.json"
    req = Schema("wind")(fixture)
    unit = req.city_configs["460100_海口市"]
    print(f"{type(req).__name__}: year={req.scenario.year} {req.scenario.resource_year_type}"
          f" 单元数={len(req.city_configs)} capacity={unit.capacity_mw}"
          f" 机型={unit.turbines[0].model} evaluate={req.evaluate}")

    solar = Schema("solar")(_ROOT / "tests" / "fixtures" / "solar_danzhou.json")
    s = solar.city_configs["460400_儋州市"]
    print(f"{type(solar).__name__}: year={solar.scenario.year} {solar.scenario.resource_year_type}"
          f" 单元数={len(solar.city_configs)} capacity={s.capacity_mw}"
          f" lat/lon=({s.latitude}, {s.longitude}) components={len(s.components)}")

    payload = json.loads(fixture.read_text(encoding="utf-8"))
    for label, mutate in [
        ("capacity_mw=-5   ", lambda p: p["city_configs"][0].__setitem__("capacity_mw", -5)),
        ("未知字段 foo      ", lambda p: p.__setitem__("foo", 1)),
        ("year='2024'      ", lambda p: p["scenario"].__setitem__("year", "2024")),
        ("缺 output_dir    ", lambda p: p.pop("output_dir")),
    ]:
        bad = copy.deepcopy(payload)
        mutate(bad)
        try:
            Schema("wind")(bad)
        except (TypeError, ValueError) as exc:
            print(f"{label}-> {type(exc).__name__}: {exc}")
