#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/18 19:35
@Author  : weiyutao
@File    : __init__.py

新能源出力核心算法包：模型按风/光各一个模块（wind.py / solar.py，
run_wind / run_solar 为入口，torch 式子模块 + forward），配置类在 config（D-46/D-48），
结果对象在 result，数据对齐在 align，共用零散工具在 utils/common、查表工具在 utils/time_axis。
"""
