#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/29
@Author  : weiyutao
@File    : test_time_axis.py

beijing_aligned 测试（合成 UTC 序列）：北京时间对齐、删闰日、截负插值。
运行（仓库根目录）: python -m tests.new_energy.test_time_axis
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from new_energy.utils.time_axis import beijing_aligned, no_feb29


def test_aligned_index(aligned: pd.Series) -> None:
    """2023-2024 → 北京时间轴 17520 小时（删 2024 年 2 月 29 日），无缺失。"""
    assert aligned.index[0] == pd.Timestamp("2023-01-01 00:00:00")
    assert len(aligned) == 8760 + 8784 - 24
    assert not aligned.isna().any()


def test_no_feb29_index(aligned: pd.Series) -> None:
    """输出索引不含任何 2 月 29 日。"""
    assert no_feb29(aligned.index).all()


def test_clip_interpolate(aligned: pd.Series) -> None:
    """负值截为 0；缺口（2023-05-01 00:00 前后跳过一小时）插值为邻点均值。"""
    assert (aligned >= 0).all()
    assert aligned[pd.Timestamp("2023-05-01 00:00:00")] == 1.0


def main() -> None:
    """造 2023-2024 UTC 序列（2023-05-01 00:00 缺一小时、第 100 点为负值），依次执行三个用例。"""
    index = pd.date_range("2022-12-31 16:00:00", "2024-12-31 15:00:00", freq="h")
    index = index.delete(index.get_indexer([pd.Timestamp("2023-05-01 00:00:00")]))
    values = np.where(np.arange(len(index)) == 100, -2.0, 1.0)  # 其余全 1，缺口前后均为 1
    aligned = beijing_aligned(pd.Series(values, index=index, name="test"), (2023, 2024))
    for test in (test_aligned_index, test_no_feb29_index, test_clip_interpolate):
        test(aligned)
        print(f"{test.__name__} PASS")


if __name__ == "__main__":
    main()
