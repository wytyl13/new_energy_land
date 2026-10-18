# -*- coding: utf-8 -*-
"""光伏出力 CLI 入口：python -m new_energy.solar.run_solar 配置.json

薄壳：配置 JSON（文件路径）交给 pipeline 唯一入口 run_solar，写 CSV 与结果
JSON 均由其完成，此处只做日志与摘要打印。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .pipeline import run_solar

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI 入口：读配置 JSON → run_solar → 打印结果摘要。

    Args: None

    Returns: None
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if len(sys.argv) < 2:
        print("用法: python -m new_energy.solar.run_solar 配置.json")
        raise SystemExit(1)
    config_path = Path(sys.argv[1])
    logger.info("光伏出力全流程开始: 配置=%s", config_path)
    response = run_solar(config_path)
    print(f"已生成: {response['output_file']}")
    print(
        f"计算范围: {len(response['cities'])} 个地市, "
        f"总装机 {response['total_capacity_mw']} MW, 耗时 {response['elapsed_s']} 秒"
    )


if __name__ == "__main__":
    main()
