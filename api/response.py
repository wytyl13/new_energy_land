#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : response.py

统一返回体包装：所有接口经 make_response 返回，不裸返回 dict/list。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder


def make_response(success: bool, message: str = "", data: Any = None, code: int = 200) -> dict:
    """统一返回体 {success, message, data, timestamp}；code 为业务子码，与 HTTP 状态码分工。
"""
    return {
        "success": success,
        "message": message,
        "data": jsonable_encoder(data) if data is not None else None,
        "timestamp": datetime.now().isoformat(),
    }
