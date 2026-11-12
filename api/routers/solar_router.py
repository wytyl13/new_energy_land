#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : solar_router.py

光伏路由：POST /api/solar/run + GET /api/solar/structure，透传统一 JSON / 查询参数给业务层。

请求体不走 Pydantic 模型：校验与默认值填充是业务层入口的职责
（skill `build-fastapi-service` 的透传例外），服务层不重复校验。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Query

from api.response import make_response
from biz.runner import run
from biz.structure import structure

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 光电路由（POST /api/solar/run + GET /api/solar/structure）
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/solar", tags=["Solar"])


@router.post("/run")
async def run_solar_scenario(
    payload: dict = Body(..., description="统一 JSON 配置：scenario.year/resource_year_type、output_dir 必填，其余可选走默认值"),
) -> dict:
    """【光伏出力场景计算】同步阻塞，默认儋州单区域约 30 秒；成功返回结果 JSON（含 power_table）。
"""
    logger.info("solar/run 请求参数: %s", payload)
    result = await asyncio.to_thread(run, "solar", payload)
    return make_response(success=True, data=result)


@router.get("/structure")
async def solar_structure(province: str = Query(..., description="归属表省名，如 海南省")) -> dict:
    """【光伏结构查询】省名 → 要填的城市配置（不分海陆，无海风结构，P10.6）。
"""
    return make_response(success=True, data=structure("solar", province))
