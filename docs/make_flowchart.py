#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/27 08:50
@Author  : weiyutao
@File    : make_flowchart.py

DESIGN「算法设计·流程图」的数据流转网络图：列=阶段（①~⑥），行=风电/光伏并行分支，
温压/温度格点虚线直通 ⑥（全流程唯一读输出年数据的地方）。
中文字符一律雅黑（等宽字体缺 CJK 字形会渲染为空），形状行纯 ASCII 才用等宽。
脚本复制自 skill maintain-project-docs/scripts/make_flowchart.py，只改了数据部分。
改流程后重跑本脚本更新 docs/数据流转网络图.png。
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle

plt.rcParams['font.family'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

C_IN = ('#D6E4F7', '#2E5E9E')
C_S1 = ('#D9F0E9', '#1F7A5C')
C_S2 = ('#FCEFD3', '#B07A1F')
C_S3 = ('#EBE2F6', '#6C48A8')
C_S4 = ('#E5EEF8', '#4A6FA5')
C_S5 = ('#FAE3E5', '#A63A45')
C_OUT = ('#DFF3D8', '#2F7A33')
INK, LINE = '#333333', '#AAAAAA'

fig, ax = plt.subplots(figsize=(13.4, 8.8), dpi=200)
ax.set_xlim(0, 106)
ax.set_ylim(62, 0)
ax.set_aspect('equal')
ax.axis('off')

WIND, SOLAR = 17, 37
XIN, X1, X2, X3, X4, X5, X6, XO = 8, 21, 34, 47, 60, 73, 86, 97
R, RI, RO = 3.5, 3.1, 2.1


def node(x, y, text, palette, r=R, fs=7.2):
    ax.add_patch(Circle((x, y), r, fc=palette[0], ec=palette[1], lw=1.4, zorder=3))
    ax.text(x, y, text, ha='center', va='center', fontsize=fs,
            color=palette[1], fontweight='bold', zorder=4, linespacing=1.25)


def link(x0, y0, x1, y1, ls='-'):
    ax.plot([x0, x1], [y0, y1], color=LINE, lw=0.9, ls=ls, zorder=1)


def caption(x, y0, name, shape, note, color):
    """列标注：层名(粗体彩色) / 输出形状(纯ASCII等宽) / 注释(雅黑)。"""
    ax.text(x, y0, name, ha='center', va='top', fontsize=8.8,
            color=color, fontweight='bold')
    ax.text(x, y0 + 3.2, shape, ha='center', va='top', fontsize=8,
            color=INK, fontfamily='monospace')
    ax.text(x, y0 + 6.6, note, ha='center', va='top', fontsize=7.6, color=INK)


ax.text(52, 3.5, '海南陆上风光出力场景 · 数据流转网络图', ha='center', va='center',
        fontsize=15, fontweight='bold', color=INK)
ax.text(2, WIND, '风电', ha='center', va='center', fontsize=10, color=C_S5[1], fontweight='bold')
ax.text(2, SOLAR, '光伏', ha='center', va='center', fontsize=10, color=C_S5[1], fontweight='bold')

wind_in = [(XIN, 13.5, '风速\n格点', C_IN), (XIN, 20.5, '温压\n格点', C_IN), (XIN, 27.5, '归属表\n+装机', C_IN)]
solar_in = [(XIN, 33.5, '辐照\n格点', C_IN), (XIN, 40.5, '温度\n格点', C_IN), (XIN, 47.5, '归属表\n+装机', C_IN)]
for x, y, t, p in wind_in + solar_in:
    node(x, y, t, p, r=RI, fs=6.8)

# ①~⑥ 阶段节点（上=风电行，下=光伏行）
stages = [
    (X1, '地市风速\n历史', '地市辐照\n+理想', C_S1),
    (X2, '资源年\n分类', '资源年\n分类', C_S2),
    (X3, '低/大风\n事件库', '低辐照\n事件库', C_S3),
    (X4, 'GPR\n基础场景', 'GPR\n损失场景', C_S4),
    (X5, '校准+\n事件插入', '校准+\n事件插入', C_S3),
    (X6, '物理出力', '物理出力', C_S5),
]
for x, wt, st, p in stages:
    node(x, WIND, wt, p)
    node(x, SOLAR, st, p)

# 输出节点按 2 域 × 3 类展开
for gy in (WIND, SOLAR):
    for dy, t in ((-4, '偏小'), (0, '正常'), (4, '偏大')):
        node(XO, gy + dy, t, C_OUT, r=RO, fs=7.4)
ax.text(102.5, WIND, '风电出力表', rotation=90, ha='center', va='center',
        fontsize=8, color=C_OUT[1], fontweight='bold')
ax.text(102.5, SOLAR, '光伏出力表', rotation=90, ha='center', va='center',
        fontsize=8, color=C_OUT[1], fontweight='bold')

# 连线：输入→①（温压/温度格点虚线直通⑥，唯一读输出年数据处）
for x, y, _, _ in wind_in[:2] + solar_in[:2]:
    link(x, y, X1, WIND if y < 30 else SOLAR)
link(wind_in[2][0], wind_in[2][1], X1, WIND)
link(solar_in[2][0], solar_in[2][1], X1, SOLAR)
link(wind_in[1][0], wind_in[1][1], X6, WIND, ls='--')
link(solar_in[1][0], solar_in[1][1], X6, SOLAR, ls='--')
for xa, xb in ((X1, X2), (X2, X3), (X3, X4), (X4, X5), (X5, X6)):
    for y in (WIND, SOLAR):
        link(xa, y, xb, y)
for gy in (WIND, SOLAR):
    for dy in (-4, 0, 4):
        link(X6, gy, XO, gy + dy)

caption(XIN, 53.5, '输入', '(8760,G)/yr', '格点CSV·UTC', C_IN[1])
caption(X1, 53.5, '① 地市历史', '(Y*8760,)', '风:格点均值|光:+理想辐照', C_S1[1])
caption(X2, 53.5, '② 资源年分类', '(Y,)', 'mean(v3)|Sum(G)/1000·P25/75', C_S2[1])
caption(X3, 53.5, '③ 极端事件库', 'runs>=3h', 'P10/P90|Kt<=P10', C_S3[1])
caption(X4, 53.5, '④ 基础场景', '(8760,)', 'GPR均值+AR(1)残差', C_S4[1])
caption(X5, 53.5, '⑤ 校准插入', '3x(8760,)', '校准→插入→映射', C_S3[1])
caption(X6, 53.5, '⑥ 物理出力', '(T_out,)', '切变·密度|POA·NOCT', C_S5[1])
caption(XO, 50.5, '输出', 'CSV+JSON', '2域x3类·列集冻结', C_OUT[1])

ax.text(52, 60.5, '地市独立并行，总量=求和　·　场景统计只用训练年，场景轴恒为非闰 8760 轴　·　随机种子/分位阈值/校准强度/输出列集冻结',
        ha='center', va='center', fontsize=8.8, color=INK)

out = Path(__file__).resolve().parent / '数据流转网络图.png'
fig.savefig(out, bbox_inches='tight', facecolor='white')
print('saved:', out)
