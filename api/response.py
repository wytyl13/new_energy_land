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


def make_response(success: bool, message: str = "", data: Any = None) -> dict:
    """统一返回体 {success, message, data, timestamp}。

    Args:
        success (bool): 是否成功。
        message (str): 提示或错误信息。
        data (Any): 业务数据，经 jsonable_encoder 序列化。

    Returns:
        dict: 统一返回体。
    """
    return {
        "success": success,
        "message": message,
        "data": jsonable_encoder(data) if data is not None else None,
        "timestamp": datetime.now().isoformat(),
    }
