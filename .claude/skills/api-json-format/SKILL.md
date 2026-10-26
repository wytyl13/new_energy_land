---
name: api-json-format
description: 前后端 JSON 交互契约。数组 vs 对象的判别原则（标签/地址），config 表单下发与提交、categories、曲线、views 四块格式。与前端对接、新增或修改接口数据格式时读。
---

# 前后端 JSON 交互契约

## 1. 判别原则（先定形态再写接口）

- **名字是地址**（前端要按键改值、要回传后端）→ **对象**，键即路径
- **名字是标签**（只读展示，画完不动）→ **数组**，条目自带 name
- 一句话：画图的一律数组，填表的一律对象
- 数组永远不当顶层返回值，外面套统一响应壳（skill `service-api` §6）
- 语法红线：双引号、无尾逗号、无注释；空单元格为 null，数值不转字符串

## 2. 四块契约（2026-10-21 用户定稿，留痕见 DESIGN.md §7）

### config 下发（渲染用，POST /api/datasets 返回）

```
[{group_name, label, data: [{field_name, label, value[, options]}]}]
```

- units 组的 data 为子组 `[{sub_group_name, label, data: [字段]}]`，最深三层
- 控件按 value 类型定：布尔=开关、有 options=下拉、数字=数字框、字符串=文本框
- 结构键（非标量 value，如 wind.classes）不下发，前端不可见不可改
- 生成位置：`service/defaults.py build_form`（组/子组中文标题在 GROUP_TITLES）

### config 提交（POST /api/jobs）

```
{"datasetId": "...", "config": {组: {键: 值}}}    # units 三层；改哪个传哪个
```

- 裸嵌套对象 = `merge_config` 原生格式，不回传渲染树、不回传 label/options
- 深合并语义：没传的键用默认值/收资浮出值；全量与精简（改动集）都合法

### categories（POST /api/datasets 返回，模版数据菜单）

```
[{group_name, data: [{sub_group_name, tables: [{sheet_name, columns, rows}], curve?}]}]
```

- 键名与 config 树同轨（group_name/sub_group_name/data），判别词统一：有 data=分组、有 tables=表格节点、有 curve=懒加载入口
- **单轨中文名，不配 label**：categories 纯只读不回传，名字是标签不是地址（config 配 label 是因 group_name 为英文机器键）
- 无细分大类（送电协议）无 data，本级直接挂 tables/curve
- curve 元数据 `{sheet_name, filterBy, match[, wholeSheet]}`；点行 → GET /api/datasets/{id}/curves/{sheet_name}?{filterBy 键=行值}
- 生成位置：`service/tree.py build_categories`

### 曲线（GET /api/datasets/{id}/curves/{sheet}）

```
{sheet_name, index: [...], data: [{column_name, data: [...]}]}
```

### views（solution.json / 结果端点）

```
[{sheet_name, index: [...], series_data: [...], lines_data: [...]}]
```

- series_data 每条一功率序列（可堆叠），lines_data 为负荷参照线
- 生成位置：`service/sheet_views.py build_sheet_views`

## 3. 改格式时

- 同步更新两处契约断言：`tests/test_api_upload.py`、`tests/test_api_job.py`
- 格式变更须用户定稿，并在 DESIGN.md §7 决策记录留痕后生效
