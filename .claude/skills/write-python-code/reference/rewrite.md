# 重写案例：函数版改类版（biz/assignment.py，2026-10-29）

任务：归属表模块从 4 个函数 + lru_cache 重写为 Assignment 类。反面版沿用了旧函数形状——旧代码里 coords 和 centers 本是两个独立函数（各自 groupby），搬进 `__init__` 后重复原样保留，只是换了包装。

## 反面（搬运：键构造 3 处、groupby 2 遍、缺列报一个停一个）

```python
for col in ("city_code", "city", "assignment_method", "中心纬度_N", "中心经度_E"):
    if col not in table.columns:
        raise ValueError(f"归属表缺少列 {col}: {assignment_csv}")
inside = table.loc[table["assignment_method"].eq("inside")]
self.keys = {f"{int(code)}_{city}" for code, city in zip(table["city_code"], table["city"])}
self.coords = {
    f"{int(code)}_{city}": list(zip(block["中心纬度_N"].astype(float), block["中心经度_E"].astype(float)))
    for (code, city), block in inside.groupby(["city_code", "city"], sort=True)
}
self.centers = {
    f"{int(code)}_{city}": (float(block["中心纬度_N"].mean()), float(block["中心经度_E"].mean()))
    for (code, city), block in inside.groupby(["city_code", "city"], sort=True)
}
```

问题：`f"{int(code)}_{city}"` 同一表达式写 3 遍；同一 inside 数据分组 2 遍；缺列校验只报第一个遇到的。

## 正面（重写：键构造 1 处、groupby 1 遍、缺列一次报全）

```python
REQUIRED_COLS = {"city_code", "city", "assignment_method", "中心纬度_N", "中心经度_E"}
COORD_COLS = ["中心纬度_N", "中心经度_E"]

if missing := self.REQUIRED_COLS - set(table.columns):
    raise ValueError(f"归属表缺少列 {missing}: {assignment_csv}")
unit_keys = table["city_code"].astype(int).astype(str) + "_" + table["city"]   # 向量化构造一次
table[self.COORD_COLS] = table[self.COORD_COLS].astype(float)                  # 类型转换一次
self.keys = set(unit_keys)
inside = table.loc[table["assignment_method"].eq("inside")]
self.coords, self.centers = {}, {}
for key, block in inside.groupby(unit_keys.loc[inside.index]):                 # 单次分组出两个结果
    lats, lons = block["中心纬度_N"], block["中心经度_E"]
    self.coords[key] = list(zip(lats, lons))
    self.centers[key] = (float(lats.mean()), float(lons.mean()))
```

要点：

- 单元键向量化构造一次，keys 与分组共用（同一事实只写一处）
- 一次 groupby 循环同时产出 coords 和 centers
- walrus + 集合差一次报出全部缺失列，报错信息更有用

# 重写案例：照搬旧结构（biz/grids.py，2026-10-29）

任务：load_city_series 函数改 GridData 类。反面版换了外壳，旧函数划分、
旧两段式循环、旧报错分支原样搬进类。

## 反面（搬运：两段式循环 + 中转变量 + 单调用方函数照旧存在）

```python
cols_by_unit: dict[str, list[str]] = {}
if single_unit is None:
    for key, coords in self.coords_by_city.items():
        cols_by_unit[key] = [self._match_column(...) ...]   # 旧函数保留，仍只此一处调用
else:
    ...
for key, cols in cols_by_unit.items():                      # 第二个循环统一求均值
    values = ...
```

```python
def find_grid_file(...):
    if not files:
        raise ValueError(...)          # 旧的两个报错分支照抄
    if len(files) > 1:
        raise ValueError(...)
```

问题：收集与处理拆两段、`cols_by_unit` 中转，都是旧代码形状，不是行为需要；
单调用方函数照旧存在并配上完整 docstring（给多余结构写说明）。

## 正面（重写：单循环就地完成 + 内联 + 单条件）

```python
for unit in pieces:
    if single_unit:
        cols = single_columns          # 未命中单元，列清单首年取定
    else:
        cols = [...]                   # 坐标匹配就地内联
    values = df[cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
    pieces[unit].append(...)

def _get_file(self, kind: str, year: int) -> Path:
    files = list((self.grid_dir / kind).glob(f"*{kind}_{year}.csv"))
    if len(files) != 1:
        raise ValueError(f"预期 1 个 ...，实际匹配到: {[f.name for f in files]}")
    return files[0]
```

要点：

- 搬进来的每个函数、分支、中间变量单独问"删掉它行为不变吗"，不变就删
- 行为是"恰好 1 个"这类单一约束，一个条件表达，报错带实际匹配列表，0 个/多个自然覆盖
