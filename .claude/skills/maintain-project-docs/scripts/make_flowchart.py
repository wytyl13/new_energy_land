#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/22 17:20
@Author  : weiyutao
@File    : make_dim_flow_figure.py

NN 结构图风格的维度流转图（参考 算法处理流程_单页.pptx 第2页）：
列=操作层，节点=圆圈，连线=数据流，列下标注 层名/输出形状/注释。
中文字符一律雅黑（等宽字体缺 CJK 字形会渲染为空），形状行纯 ASCII 才用等宽。
覆盖 算法流程图.png。
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle

plt.rcParams['font.family'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

C_IN = ('#D6E4F7', '#2E5E9E')
C_S1 = ('#D9F0E9', '#1F7A5C')
C_S2 = ('#FCEFD3', '#B07A1F')
C_S34 = ('#EBE2F6', '#6C48A8')
C_S5 = ('#FAE3E5', '#A63A45')
C_OUT = ('#DFF3D8', '#2F7A33')
INK, LINE = '#333333', '#AAAAAA'

fig, ax = plt.subplots(figsize=(12.8, 8.6), dpi=200)
ax.set_xlim(0, 106)
ax.set_ylim(62, 0)
ax.set_aspect('equal')
ax.axis('off')

ROWS = (17, 27, 37)
XIN, X1, X2, X3, X4, X5, XO = 8, 23, 39, 55, 71, 86, 97
R, RO = 3.5, 2.1


def node(x, y, text, palette, r=R, fs=7.2):
    ax.add_patch(Circle((x, y), r, fc=palette[0], ec=palette[1], lw=1.4, zorder=3))
    ax.text(x, y, text, ha='center', va='center', fontsize=fs,
            color=palette[1], fontweight='bold', zorder=4, linespacing=1.25)


def link(x0, y0, x1, y1):
    ax.plot([x0, x1], [y0, y1], color=LINE, lw=0.9, zorder=1)


def caption(x, y0, name, shape, note, color):
    """列标注：层名(粗体彩色) / 输出形状(纯ASCII等宽) / 注释(雅黑)。"""
    ax.text(x, y0, name, ha='center', va='top', fontsize=8.8,
            color=color, fontweight='bold')
    ax.text(x, y0 + 3.2, shape, ha='center', va='top', fontsize=8,
            color=INK, fontfamily='monospace')
    ax.text(x, y0 + 6.6, note, ha='center', va='top', fontsize=7.6, color=INK)


ax.text(52, 3.5, '海南风光九条出力曲线 · 数据流转网络图', ha='center', va='center',
        fontsize=15, fontweight='bold', color=INK)

sea, land, sol = ROWS
u100, v100, ssrd = (XIN, 13.5), (XIN, 20.5), (XIN, 37)
for src in (u100, v100):
    for dst_y in (sea, land):
        link(src[0], src[1], X1, dst_y)
link(*ssrd, X1, sol)
for x0, x1 in ((X1, X2), (X2, X3), (X3, X4), (X4, X5)):
    for y in ROWS:
        link(x0, y, x1, y)
groups = ((sea, (11, 15, 19)), (land, (24, 28, 32)), (sol, (37, 41, 45)))
for row_y, outs in groups:
    for oy in outs:
        link(X5, row_y, XO, oy)

node(*u100, 'u100', C_IN); node(*v100, 'v100', C_IN); node(*ssrd, 'ssrd', C_IN)
for y, t in zip(ROWS, ('海风', '陆风', '光伏')):
    node(X1, y, t, C_S1, fs=8.6)
    node(X2, y, f'{t}\n切档', C_S2)
    node(X3, y, f'{t}\n选年', C_S34)
    node(X4, y, f'{t}\n修极端', C_S34, fs=6.8)
    node(X5, y, f'{t}\n出力', C_S5)
for outs in (g[1] for g in groups):
    for oy, t in zip(outs, ('大', '平', '小')):
        node(XO, oy, t, C_OUT, r=RO, fs=7.4)
for gy, name in ((15, '海风库'), (28, '陆风库'), (41, '光伏库')):
    ax.text(102.5, gy, name, rotation=90, ha='center', va='center',
            fontsize=8, color=C_OUT[1], fontweight='bold')

caption(XIN, 45.5, '输入', '(76,8760,8,10)', '风u/v100·光ssrd', C_IN[1])
caption(X1, 45.5, '① 空间平均', '(76,8760)', '海31格|陆49格|光80格', C_S1[1])
caption(X2, 45.5, '② 分档', '[19][38][19]', '风Σv³·光ΣG/1000', C_S2[1])
caption(X3, 45.5, '③ 选代表年', 'rep (8760,)', 'medoid·真实年不平均', C_S34[1])
caption(X4, 45.5, '④ 修极端', '(8760,)', '风≥3h段|光整日', C_S34[1])
caption(X5, 45.5, '⑤ 出力映射', '(8760,)', '风功率曲线|光0.6G/1000', C_S5[1])
caption(XO, 50.5, '输出', '(9,8760,4)+', '9=3类型×3档', C_OUT[1])
ax.text(XO, 59.7, '(9,8760)+(9,)', ha='center', va='top', fontsize=8,
        color=INK, fontfamily='monospace')

ax.text(52, 59.8, '①后空间维消失，只有 8760 时间轴　·　③④形状锁定 (8760,)，取行/换值，全程真实数据无合成',
        ha='center', va='center', fontsize=8.8, color=INK)

out = Path(__file__).resolve().parent / '算法流程图.png'
fig.savefig(out, bbox_inches='tight', facecolor='white')
print('saved:', out)
