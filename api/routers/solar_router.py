# -*- coding: utf-8 -*-
"""光伏路由：POST /api/solar/run，同步执行，透传统一 JSON 给 pipeline 唯一入口。

请求体不走 Pydantic 模型：校验与默认值填充是 pipeline 入口的职责
（skill `service-api` 第 3.3 条透传例外），服务层不重复校验。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body

from api.response import make_response
from new_energy.solar.pipeline import run_solar

router = APIRouter(prefix="/api/solar", tags=["Solar"])


@router.post("/run")
async def run_solar_scenario(
    payload: dict = Body(..., description="统一 JSON 配置：scenario.year/resource_year_type、output_dir 必填，其余可选走默认值"),
) -> dict:
    """【光伏出力场景计算】同步阻塞，默认儋州单区域约 30 秒；成功返回结果 JSON（含 power_table）。"""
    result = await asyncio.to_thread(run_solar, payload)
    return make_response(success=True, data=result)
