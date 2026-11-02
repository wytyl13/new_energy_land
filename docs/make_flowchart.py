#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/11/02
@Author  : weiyutao
@File    : make_flowchart.py

DESIGN「算法设计·流程图」的维度流转图：主链一条 = DESIGN「维度流转」的总流转式，
框 = 阶段（①~⑥），框上标注该阶段输出维度，框内两行写风/光差异；
温压/温度格点虚线弧直通 ⑥（全流程唯一绕过主链的输入）。
中文字符一律雅黑（等宽字体缺 CJK 字形会渲染为空），维度标注纯 ASCII 才用等宽。
改流程后重跑本脚本更新 docs/维度流转图.png。
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams['font.family'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

INK, LINE, GRAY = '#333333', '#9AA5B1', '#666666'
STAGE = ['#6FA0CF', '#5D92C6', '#4B84BD', '#3976B4', '#2768AB', '#155AA2']
C_IN, C_OUT = '#8A94A0', '#2F7A33'

W, GAP, CY, BH = 14.0, 7.0, 14.5, 9.5
XS = [2 + i * (W + GAP) for i in range(8)]
XMAX = XS[-1] + W + 2

fig, ax = plt.subplots(figsize=(16.5, 4.0), dpi=200)
ax.set_xlim(0, XMAX)
ax.set_ylim(0, 27)
ax.axis('off')


def box(i, title, body, color):
    ax.add_patch(FancyBboxPatch((XS[i], CY - BH / 2), W, BH,
                                boxstyle='round,pad=0,rounding_size=1.1',
                                fc='white', ec=color, lw=1.6, zorder=3))
    ax.text(XS[i] + W / 2, CY + 2.6, title, ha='center', va='center',
            fontsize=10.5, color=color, fontweight='bold', zorder=4)
    ax.text(XS[i] + W / 2, CY - 1.6, body, ha='center', va='center',
            fontsize=7.2, color=GRAY, linespacing=1.7, zorder=4)


def dim(i, text, color):
    ax.text(XS[i] + W / 2, CY + BH / 2 + 2.3, text, ha='center', va='center',
            fontsize=7.6, color=color, fontweight='bold',
            fontfamily='monospace', zorder=4)


def arrow(i):
    ax.add_patch(FancyArrowPatch((XS[i] + W, CY), (XS[i + 1], CY),
                                 arrowstyle='-|>', mutation_scale=13,
                                 color=LINE, lw=1.4, zorder=2))


nodes = [
    ('输入', '格点 CSV · UTC\n归属表 + 装机', '(N,8760,G)', C_IN),
    ('① 单元历史', '格点聚合→北京时→删2/29\n光伏:+理想辐照', '(N,8760)', STAGE[0]),
    ('② 资源年分类', '年指标分位 P25/P75\n风:mean(v³) 光:ΣG/1000', '(N,)', STAGE[1]),
    ('③ 极端事件库', '连续段≥3h 登记+分月标准\n风:低/大风 光:低辐照', 'E x (Le,)+(3,12,2)', STAGE[2]),
    ('④ 基础场景', 'GPR 均值 + AR(1) 残差\n光伏:损失域采样', '(8760,)', STAGE[3]),
    ('⑤ 校准与插入', '分位校准→事件插入\n→映射 / 月能量校正', '3 x (8760,)', STAGE[4]),
    ('⑥ 物理出力', '风:切变·密度·v³\n光:POA·NOCT·重排', '(Tout,)', STAGE[5]),
    ('输出', '出力 CSV + JSON\n列集冻结', '(Tout, 4+C*b)', C_OUT),
]
for i, (title, body, shape, color) in enumerate(nodes):
    box(i, title, body, color)
    dim(i, shape, color)
    if i < 7:
        arrow(i)

# 温压/温度格点：唯一绕过主链的输入，虚线弧直通 ⑥（物理出力）
ax.add_patch(FancyArrowPatch((XS[0] + W / 2, CY - BH / 2), (XS[6] + W / 2, CY - BH / 2),
                             arrowstyle='-|>', mutation_scale=13, color=C_IN, lw=1.3,
                             linestyle=(0, (5, 3)), zorder=2,
                             connectionstyle='arc3,rad=-0.09'))
ax.text((XS[0] + XS[6] + W) / 2, 3.3, '温压 / 温度格点（风:输出年 · 光:训练年气候态）→ ⑥ 物理出力',
        ha='center', va='center', fontsize=8.2, color=GRAY)

ax.text(XMAX / 2, 25.2, '陆上风光出力场景 · 算法维度流转图',
        ha='center', va='center', fontsize=15.5, fontweight='bold', color=INK)
ax.text(XMAX / 2, 1.3, '单元独立并行　·　场景统计只用训练年　·　场景轴恒为非闰 8760　·　种子 / 阈值 / 列集冻结',
        ha='center', va='center', fontsize=8.6, color=GRAY)

out = Path(__file__).resolve().parent / '维度流转图.png'
fig.savefig(out, bbox_inches='tight', facecolor='white')
print('saved:', out)
