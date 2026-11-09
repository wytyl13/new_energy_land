# -*- coding: utf-8 -*-
"""生成《海陆风电划分逻辑》3 页 PPT（16:9，简洁风格）。一次性脚本，输出到 output/。"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

FONT = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
TEXT = RGBColor(0x1F, 0x29, 0x37)
GRAY = RGBColor(0x4A, 0x55, 0x68)
NOTE = RGBColor(0x6B, 0x72, 0x80)
CARD_BG = RGBColor(0xF7, 0xF9, 0xFC)
CARD_LINE = RGBColor(0xD8, 0xE1, 0xEC)
BOX_BG = RGBColor(0xF0, 0xF5, 0xFB)
BOX_LINE = RGBColor(0xB9, 0xCB, 0xE2)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def _set_run(r, text, size, bold, color):
    r.text = text
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    rPr = r._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT)


def add_text(slide, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP):
    """paras: list of dict(runs=[(text,size,bold,color)], align, space_after, line_spacing)"""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for m in tf.margin_left, tf.margin_right, tf.margin_top, tf.margin_bottom:
        m = 0
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = p.get("align", PP_ALIGN.LEFT)
        para.space_after = Pt(p.get("space_after", 0))
        para.line_spacing = p.get("line_spacing", 1.0)
        for t, s, b, c in p["runs"]:
            _set_run(para.add_run(), t, s, b, c)
    return tb


def add_rect(slide, x, y, w, h, fill, line, rounded=True):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Pt(0.75)
    shape.shadow.inherit = False
    return shape


def add_arrow(slide, x, y, w=0.45, h=0.24):
    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    a.fill.solid()
    a.fill.fore_color.rgb = ACCENT
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def new_slide(title, summary):
    s = prs.slides.add_slide(BLANK)
    add_text(s, 0.6, 0.32, 12.13, 0.62, [dict(runs=[(title, 24, True, TEXT)])])
    add_rect(s, 0.62, 1.02, 1.15, 0.05, ACCENT, ACCENT, rounded=False)
    add_text(s, 0.6, 1.18, 12.13, 0.82,
             [dict(runs=[(summary, 13.5, False, GRAY)], line_spacing=1.25)])
    return s


def add_cards(slide, y, h, cards):
    """cards: list of (num, title, bullets)"""
    w = (12.13 - 0.5) / 3
    for i, (num, ttl, bullets) in enumerate(cards):
        x = 0.6 + i * (w + 0.25)
        add_rect(slide, x, y, w, h, CARD_BG, CARD_LINE)
        add_text(slide, x + 0.18, y + 0.13, w - 0.36, 0.42,
                 [dict(runs=[(num, 20, True, ACCENT), ("  " + ttl, 16, True, TEXT)])])
        paras = [dict(runs=[("• " + b, 12, False, TEXT)],
                      space_after=5, line_spacing=1.18) for b in bullets]
        add_text(slide, x + 0.18, y + 0.62, w - 0.36, h - 0.74, paras)


def flow_box(slide, x, y, w, h, lines, bold_first=True):
    add_rect(slide, x, y, w, h, BOX_BG, BOX_LINE)
    paras = []
    for j, ln in enumerate(lines):
        paras.append(dict(runs=[(ln, 10.5, bold_first and j == 0, TEXT)],
                          align=PP_ALIGN.CENTER, line_spacing=1.15))
    add_text(slide, x + 0.06, y + 0.04, w - 0.12, h - 0.08, paras, anchor=MSO_ANCHOR.MIDDLE)


def add_note(slide, text, y=6.72):
    add_text(slide, 0.6, y, 12.13, 0.5,
             [dict(runs=[(text, 10.5, False, NOTE)], line_spacing=1.2)])


# ── 第 1 页：归属表的生成与海陆标注 ─────────────────────────────
s = new_slide("（一）海陆划分的数据基础：格点地市归属表的生成与海陆标注",
              "归属表由两个公开数据源自动生成——ERA5 海陆掩膜回答“是陆是海”，DataV 行政边界回答"
              "“属于哪个地市”，按格点坐标对齐合成一张表；一次生成，服务运行时只读。")
add_cards(s, 2.1, 2.5, [
    ("01", "两个输入", [
        "全国网格海陆分类.csv（ARCO-ERA5 land_sea_mask）",
        "0.25° 网格全图 36685 点（18–54°N、73–136°E）",
        "每点 0=陆 / 1=海：lsm>0.5 判陆，内陆湖点计海",
        "DataV 行政区划边界 GeoJSON：省、市两级多边形",
    ]),
    ("02", "生成过程", [
        "格点中心 × 地市边界，做 point-in-polygon 判定",
        "落在某市多边形内才归属该市，只写 inside 行",
        "境外点、未命中点不指派（不写最近邻）",
        "海陆判定按同一坐标键随行透传",
    ]),
    ("03", "产出", [
        "全国格点地市归属.csv（一次生成，运行时只读）",
        "15440 点 × 370 地市单元 × 33 省（澳门无格点）",
        "每行 = 坐标 + 省市 + 归属方式 + 海陆判定",
        "海南示例：海口 4 陆｜儋州 4 陆 + 1 海｜三沙 1 海",
    ]),
])
flow_box(s, 0.6, 4.72, 2.85, 0.7, ["ERA5 海陆掩膜", "全国网格海陆分类.csv（36685 点）"])
flow_box(s, 0.6, 5.58, 2.85, 0.7, ["DataV 行政边界", "省、市两级多边形（GeoJSON）"])
add_arrow(s, 3.52, 4.95)
add_arrow(s, 3.52, 5.81)
flow_box(s, 4.05, 5.17, 3.55, 0.95, ["坐标对齐 + 多边形判定", "inside 归属 · 海陆列随行透传"])
add_arrow(s, 7.68, 5.52)
flow_box(s, 8.2, 5.17, 4.5, 0.95, ["全国格点地市归属表", "15440 点 × 370 地市 × 33 省（含海陆判定列）"])
add_note(s, "注：生成时质量校验（网格行数、海陆列无缺值、海点被归属 ≤1% 容差）不合格即报错不写盘；"
            "行政归属与海陆判定相互独立，同一市可陆海并存。")

# ── 第 2 页：取数拆分 ───────────────────────────────────────────
s = new_slide("（二）程序适配①：取数拆分——陆风按市、海风成组",
              "风电读取格点数据时按海陆判定拆成两条取数路径：陆格点按市聚合成陆风，"
              "请求范围内全部海格点合并为单一区域海风单元 sea；两类序列分别送入风电物理模型。")
add_cards(s, 2.1, 2.15, [
    ("01", "陆风", [
        "按市分组，各市只聚合自己的陆格点",
        "产出各市城市级风速序列",
        "口径与拆分前一致，仅剔除海点",
    ]),
    ("02", "海风", [
        "请求单元的海格点不分市",
        "全部海格点取并集，聚合为一组",
        "产出区域海风风速序列（sea 单元）",
    ]),
    ("03", "纯海单元", [
        "无陆格点的单元（如三沙、舟山）",
        "跳过陆风计算并输出告警",
        "其海格点仍并入海组，格点不丢",
    ]),
])
flow_box(s, 0.6, 5.0, 2.5, 0.86, ["请求计算单元", "（地市列表）"])
add_arrow(s, 3.18, 4.72)
add_arrow(s, 3.18, 5.9)
flow_box(s, 3.7, 4.55, 4.35, 0.72, ["陆格点：按市分组 → N 组城市风速（陆风）"], bold_first=False)
flow_box(s, 3.7, 5.6, 4.35, 0.72, ["海格点：取并集 → 1 组区域海风风速（sea）"], bold_first=False)
add_arrow(s, 8.13, 4.72)
add_arrow(s, 8.13, 5.9)
flow_box(s, 8.65, 5.0, 4.05, 0.86, ["风电物理模型", "（算法零改动）"])
add_note(s, "注：光伏不做海陆拆分，仍按单元聚合全部格点；拆分只发生在取数层，"
            "场景生成与物理模型算法零改动。")

# ── 第 3 页：请求、计算与输出的海风贯通 ─────────────────────────
s = new_slide("（三）程序适配②：请求、计算与输出的海风贯通",
              "海风作为一个独立计算单元贯穿请求校验、并行计算与结果输出；对模型而言，"
              "sea 与普通城市单元无差别。")
add_cards(s, 2.1, 3.35, [
    ("01", "请求适配", [
        "新增平级键 sea_configs：region=\"sea\"、capacity_mw、turbines",
        "单元含海格点必传——防海风出力静默丢失",
        "无海格点传了报错",
        "与 city_configs 都缺省时，填服务端默认海风配置",
        "（20 MW、6MW 机组、切变指数 0.70）",
    ]),
    ("02", "计算适配", [
        "sea 作为独立任务键，与各市并列进同一并行池",
        "温度、气压数据与陆风同组读取",
        "汇总时海风装机计入总装机，出力计入省级出力",
    ]),
    ("03", "输出适配", [
        "出力表自动增列 sea_power_mw（城市列之后、末位）",
        "返回 cities 含 \"sea\"，总装机含海风装机",
        "文件名 onshore_wind_power_ → wind_power_",
        "无海请求输出完全不变",
    ]),
])
strip = add_rect(s, 0.6, 5.75, 12.13, 0.62, RGBColor(0xE8, 0xF0, 0xFA), RGBColor(0xE8, 0xF0, 0xFA))
add_text(s, 0.8, 5.75, 11.73, 0.62,
         [dict(runs=[("对算法而言 sea 只是一个普通单元键 —— 请求、取数、并行、汇总均在业务层完成，算法包零改动",
                      13, True, ACCENT)], align=PP_ALIGN.CENTER)],
         anchor=MSO_ANCHOR.MIDDLE)
add_note(s, "注：以上均为业务层适配；有海请求才增列、改名，纯陆请求输出与此前完全一致。", y=6.62)

out = r"output\海陆风电划分逻辑.pptx"
prs.save(out)
print("saved:", out, "slides:", len(prs.slides._sldIdLst))
