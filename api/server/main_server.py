# -*- coding: utf-8 -*-
"""FastAPI 服务装配：lifespan、跨域、请求日志、健康检查、异常映射、路由注册。

启动（仓库根目录，conda 环境）:
    python -m api.server.main_server [--port 9998]
"""
from __future__ import annotations

import argparse
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from api.response import make_response
from api.routers import solar_router, wind_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动: 路由 wind/solar run + health")
    yield
    logger.info("服务关闭")


app = FastAPI(title="新能源出力场景服务", lifespan=lifespan)

# 跨域全放开；前后端分离部署时按需收紧 origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"],
)
# power_table 数 MB 级，超 1KB 的响应启用 gzip
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    logger.info("%s %s -> %s (%.1fs)", request.method, request.url.path, response.status_code, elapsed)
    return response


@app.get("/api/health")
async def health() -> dict:
    """【健康检查】无参纯读。"""
    return {"status": "ok", "message": "服务正常", "timestamp": datetime.now().isoformat()}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """算法/pipeline 抛的参数与数据类错误 → 400，message 原样透出。"""
    logger.warning("%s %s ValueError: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=400, content=make_response(success=False, message=str(exc), code=400))


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常 → 500；堆栈只进日志，不进 message。"""
    logger.exception("%s %s 未捕获异常", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": f"Internal Server Error: {exc}",
            "data": None,
            "timestamp": datetime.now().isoformat(),
        },
    )


app.include_router(wind_router.router)
app.include_router(solar_router.router)


def main() -> None:
    parser = argparse.ArgumentParser(description="新能源出力场景服务")
    parser.add_argument("--port", type=int, default=9998)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=300)


if __name__ == "__main__":
    main()
