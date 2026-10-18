# -*- coding: utf-8 -*-
"""风电出力 CLI 入口：python -m new_energy.wind.run_wind 配置.json

全流程唯一的磁盘写入点：最终出力 CSV（列结构与旧版一致）。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from .pipeline import run_wind

ENC = "utf-8-sig"

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if len(sys.argv) < 2:
        print("用法: python -m new_energy.wind.run_wind 配置.json")
        raise SystemExit(1)
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    year = int(config["scenario"]["year"])
    logger.info("风电出力全流程开始: 配置=%s, 输出年份=%d", config_path, year)

    start = time.perf_counter()
    result = run_wind(config)
    logger.info("风电出力全流程完成: 耗时 %.1f 秒", time.perf_counter() - start)

    case = config["scenario"]["resource_year_type"]
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"onshore_wind_power_{year}_{case}.csv"
    result.power[case].to_csv(out_path, index=False, encoding=ENC)
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()
