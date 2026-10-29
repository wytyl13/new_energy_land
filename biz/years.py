#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/28
@Author  : weiyutao
@File    : years.py

历史年份方案与数据完整性校验：期望窗口 ∩ 各数据类型
实际存在的年份 → 最大连续段 → 逐年行数校验，构造一次全算好。
"""
from __future__ import annotations

import calendar
import logging
import re
from pathlib import Path

from .constants import DEFAULT_YEARS, MIN_HISTORY_YEARS

logger = logging.getLogger(__name__)


class YearsPlan:
    """历史年份方案：构造时算好并校验，三个属性即方案。

    Attributes:
        source_years (tuple[int, ...]): 实际使用的历史年份（两端含）。
        train_years (tuple[int, ...]): 除最后一年（训练统计用）。
        axis_year (int): 最后一年（不参与统计，只借 8760 小时日历模板，之后重排到输出年份）。

    构造规则（不符即报错，本地磁盘文件防御性编程）：
        1. 多个数据类型取年份交集（最小可用范围），窗口外年份不纳入；
        2. 取最大连续年份段，段外年份丢弃并告警（防止跨年缺口被插值）；
        3. 段内每个文件校验行数（闰年 8784 / 平年 8760 小时），不符报错；
        4. 年份数低于 MIN_HISTORY_YEARS 报错。
    """

    def __init__(self, grid_dir: Path, kinds: tuple[str, ...], window: tuple[int, int] = DEFAULT_YEARS):
        """输入数据目录与数据类型，算出三个年份属性（规则与报错见类说明）。

        Args:
            grid_dir (Path): 数据目录（其下每类型一个子目录）。
            kinds (tuple[str, ...]): 数据类型（风为 wind；光伏为 solar 与 temperature）。
            window (tuple[int, int]): 期望年份窗口（两端含），缺省 DEFAULT_YEARS。
        """
        # 1. 窗口配置校验
        start, end = window
        if start >= end:
            raise ValueError(f"历史年份窗口配置错误: 起始 {start} ≥ 结束 {end}")

        # 2. 防御性：扫各类型目录得 {年份: 文件路径}（第 5 步行数校验直接用）
        per_kind = self._scan(grid_dir, kinds)

        # 3. 防御性：各类型年份交集 ∩ 窗口（空 → 报错，报错附各类型实际年份）
        in_window = sorted(set(range(start, end + 1)).intersection(*per_kind.values()))
        if not in_window:
            detail = {k: sorted(v) for k, v in per_kind.items()}
            raise ValueError(f"数据目录在期望窗口 {start}-{end} 内没有任何年份: {grid_dir}，各类型实际年份 {detail}")

        # 4. 防御性：取最大连续年份段，段外丢弃告警（防跨年缺口被插值）
        best = self._longest_run(in_window)
        if dropped := sorted(set(in_window) - set(best)):
            logger.warning("历史年份不连续，丢弃 %s，使用 %d-%d", dropped, best[0], best[-1])

        # 5. 防御性：段内逐年行数校验（闰 8784 / 平 8760 小时，不符报错指明文件）
        for year in best:
            expected = 8784 if calendar.isleap(year) else 8760
            for kind in kinds:
                path = per_kind[kind][year]
                rows = len(path.read_bytes().splitlines()) - 1  # 总行数减表头 = 数据行数
                if rows != expected:
                    raise ValueError(f"{path} 数据行数 {rows} ≠ {year} 年应有的 {expected} 行，数据不完整")

        # 6. 年份数下限校验；窗口缺年告警
        if len(best) < MIN_HISTORY_YEARS:
            raise ValueError(
                f"历史年份数不足: 实际 {len(best)} 年（{best[0]}-{best[-1]}），下限 {MIN_HISTORY_YEARS} 年"
            )
        if missing := sorted(set(range(start, end + 1)) - set(best)):
            logger.warning("期望窗口 %d-%d 缺 %s，实际使用 %d-%d", start, end, missing, best[0], best[-1])

        # 7. 产出三属性：source 全部年 / train 去最后一年 / axis 最后一年
        self.source_years, self.train_years, self.axis_year = tuple(best), tuple(best[:-1]), best[-1]

    @staticmethod
    def _longest_run(years: list[int]) -> list[int]:
        """升序年份列表的最长连续段（如 [2001, 2002, 2004, 2005] → [2004, 2005]）。

        能接上前一段就接上，接不上开新段，最后取最长段。

        Args:
            years (list[int]): 升序且不重复的年份。

        Returns:
            list[int]: 最长连续段（原顺序）。
        """
        runs: list[list[int]] = []
        for year in years:
            if runs and year == runs[-1][-1] + 1:
                runs[-1].append(year)
            else:
                runs.append([year])
        return max(runs, key=len)

    @staticmethod
    def _scan(grid_dir: Path, kinds: tuple[str, ...]) -> dict[str, dict[int, Path]]:
        """扫各类型子目录，返回 {类型: {年份: 文件路径}}。

        文件名 = 任意前缀 + {类型}_{年份}.csv（如 wind_2005.csv、hainan_wind_2005.csv）；
        同一年多个文件（多区域混放）即报错。

        Args:
            grid_dir (Path): 数据目录（其下每类型一个子目录）。
            kinds (tuple[str, ...]): 数据类型。

        Returns:
            dict[str, dict[int, Path]]: {类型: {年份: 文件路径}}。
        """
        per_kind: dict[str, dict[int, Path]] = {}
        for kind in kinds:
            files: dict[int, Path] = {}
            pattern = re.compile(rf"{kind}_(\d{{4}})\.csv$")
            for path in (grid_dir / kind).glob("*.csv"):
                if m := pattern.search(path.name):
                    year = int(m.group(1))
                    if year in files:
                        raise ValueError(
                            f"{grid_dir / kind} 同一年有多个 {kind} 文件，须只留一个区域: {files[year].name} 与 {path.name}"
                        )
                    files[year] = path
            per_kind[kind] = files
        return per_kind
