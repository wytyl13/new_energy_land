#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27
@Author  : weiyutao
@File    : run.py

统一 CLI 入口（原 wind/run_wind.py 与 solar/run_solar.py 合并）：
python run.py wind|solar 配置.json

薄壳：配置 JSON 交给业务层唯一入口 biz.runner.run，写 CSV 与结果 JSON 均由其
完成，此处只做日志与摘要打印。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from biz.runner import run

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI 入口：读配置 JSON → run → 打印结果摘要。

    Args: None

    Returns: None
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if len(sys.argv) != 3 or sys.argv[1] not in ("wind", "solar"):
        print("用法: python run.py wind|solar 配置.json")
        raise SystemExit(1)
    kind, config_path = sys.argv[1], Path(sys.argv[2])
    logger.info("%s出力全流程开始: 配置=%s", "风电" if kind == "wind" else "光伏", config_path)
    response = run(kind, config_path)
    print(f"已生成: {response['output_file']}")
    print(
        f"计算范围: {len(response['cities'])} 个地市, "
        f"总装机 {response['total_capacity_mw']} MW, 耗时 {response['elapsed_s']} 秒"
    )


if __name__ == "__main__":
    main()
