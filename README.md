# 背景

> 本项目根据海南 ERA5 气象格点数据生成新能源出力场景：读入逐年格点风速/辐照度/温度/气压，按地市聚合后生成三类资源年（偏小/正常/偏大）场景，再经物理模型转为全省出力。输入为统一 JSON 配置（机组信息与数据目录可缺省走默认），输出为出力 CSV 文件 + JSON 表格数据双份。风电、光伏流程均已完成。

# Quick Start

## 源码下载和环境安装

```bash
git clone https://github.com/wytyl13/new_energy_land.git
cd new_energy_land
conda create -n newenergy_dgzy python=3.10
conda activate newenergy_dgzy
pip install -r requirements.txt
```

> 原始气象数据（1.9G）不入库：克隆后需将海南 ERA5 格点数据放到 `data/hainan_era5_grid_csv/`（子目录结构见下文风电/光伏"输入"节），否则计算因缺数据文件报错。

## 核心算法维护与版本更新

> 采用 **Fork + Pull Request** 模式：协作者不需要仓库权限，所有改动经仓库所有者审核合并后才进入 `main`。

```bash
# 0. 网页 Fork：打开 github.com/wytyl13/new_energy_land → 右上角 Fork 到自己账号（一次性）

# 1. 首次：克隆自己的 Fork + 配置身份 + 关联上游
git clone https://github.com/你的账号/new_energy_land.git
cd new_energy_land
git config user.name "姓名"
git config user.email "邮箱"
git remote add upstream https://github.com/wytyl13/new_energy_land.git

# 2. 每次开发前：同步上游最新代码（仓库只有 main 分支，无 dev）
git checkout main
git pull upstream main
git push origin main

# 3. 建功能分支开发（核心算法代码在 new_energy/ 下）
git checkout -b feature/algo-update
git add new_energy/
git commit -m "feature(algo): 描述改了什么"

# 4. 推送到自己的 Fork
git push origin feature/algo-update

# 5. 网页开 Pull Request：从 你的账号:feature/algo-update → wytyl13/new_energy_land:main
#    仓库所有者审核后点 Merge

# 6. 合并后同步并清理
git checkout main && git pull upstream main && git push origin main
git branch -d feature/algo-update
git push origin --delete feature/algo-update
```

## 运行风电全流程（CLI 单脚本）

```powershell
# 仓库根目录执行；配置模板见 .refactor_baseline/config.json（全省）与 tests/fixtures/wind_minimal.json（最简，全走默认）
conda activate newenergy_dgzy
python run.py wind tests/fixtures/wind_minimal.json
```

> 输出说明见 [# 风电 / 输出](#输出)。单市约 20 秒，全省约 4 分钟。

## 运行光伏全流程（CLI 单脚本）

```powershell
# 仓库根目录执行；配置模板见 tests/fixtures/solar_danzhou.json
conda activate newenergy_dgzy
python run.py solar tests/fixtures/solar_danzhou.json
```

> 输出说明见 [# 光伏 / 输出](#输出-1)。儋州单市约 1 分钟。

## 启动 HTTP 服务

```powershell
# 仓库根目录执行
conda activate newenergy_dgzy
python -m api.server.main_server --port 9998   # 端口默认 9998
```

> 端点与请求/返回示例见 [# 接口](#接口)。同步阻塞式：单市几十秒，全省约 4 分钟。

## 自测

```powershell
python -m tests.wind_run_selftest   # 风电：全省回归约 4 分钟 + 单市一致性 + 默认值 + 非法请求
python -m tests.solar_selftest      # 光伏：儋州全流程回归约 1 分钟 + 默认值 + 非法请求
```

> 回归判据与冻结基准容差一致（风电：总量 ≤1e-6、逐元素 ≤1e-4；光伏：场景与出力全量比对）。请求样例见 `tests/fixtures/`。

# 架构

> 三层单向依赖 `api → biz → new_energy`：算法层只做计算（内存序列进、结果对象出，不碰文件），业务层负责校验、取数、表组装与写文件，服务层与 CLI 均走业务层唯一入口。

```
CLI      run.py              python run.py wind|solar 配置.json（本地调试，薄壳）
服务层   api/                HTTP 透传：JSON dict → biz.runner.run，不重复校验
  ↓
业务层   biz/                唯一入口 run(kind, source)：校验 → 取数 → 算法调用 → 表组装 → 写 CSV + 结果 JSON
         request.py           统一 JSON 装载与字段校验、默认路径
         assignment.py        格点 CSV + 归属表 → 各地市气象序列；历史年份解析
         runner.py            全流程组织、冻结列序表组装、双份写出
  ↓
算法层   new_energy/         只做计算，全程内存；阶段平铺，风/光为两组模块组合（D-46）：
         pipeline.py            WindPowerModel/SolarPowerModel：__init__ 组装各阶段，forward 顺序执行
         config.py              WindConfig/SolarConfig 配置类（冻结常数在 constants.py 不进配置）
         {history,classify,events,generate,power,evaluate}.py
                              各计算阶段（HistoryBuilder/ResourceYearClassifier/Wind|Solar各Stage）
         utils/time_axis.py   逐年小时时间轴与闰日处理
         constants.py         冻结随机种子与资源年类型
```

| 模块                                                                    | 职责                                              |
| :---------------------------------------------------------------------- | :------------------------------------------------ |
| [run.py](run.py)                                                        | CLI 入口（本地调试，薄壳）                        |
| [biz/request.py](biz/request.py)                                        | 统一 JSON 装载与字段校验、默认路径                |
| [biz/assignment.py](biz/assignment.py)                                  | 格点 CSV + 归属表 → 各地市气象序列；历史年份解析 |
| [biz/runner.py](biz/runner.py)                                          | 唯一入口：校验+取数+算法调用+表组装+双份写出     |
| [new_energy/utils/time_axis.py](new_energy/utils/time_axis.py)          | 逐年小时时间轴与闰日处理                          |

> 单元层各模块的职责见下方风电/光伏全流程表。

# 风电

## 全流程

> 统一 JSON 校验+默认值 → 格点风速 → 地市历史 → 资源年分类 → 事件库 → 三类场景 → 物理出力 → 出力 CSV + 结果 JSON。历史年份解析规则见下方"输入"节。

| 步骤           | 输入                                                                   | 中间处理                                                                                                                | 输出                                                                              | 模块                                               |
| :------------- | :--------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------- | :------------------------------------------------- |
| 0 校验与默认值 | 统一 JSON（文件路径或 dict）                                           | 必填/类型/未知字段校验，重复地市与归属表核对；`data_dir`/`city_configs` 缺省填服务端默认                            | 规范化配置（内存）                                                                | [biz/request.py](biz/request.py)                    |
| 1 读风速格点   | `data_dir`（含 wind/temperature/pressure 子目录）+ 格点归属表 [1][1] | 按地市严格边界内格点求均值，逐年拼接（UTC 口径）                                                                        | {地市键: 逐年风速 Series}                                                         | [biz/assignment.py](biz/assignment.py)              |
| 2 历史构建     | 步骤 1 的地市风速                                                      | UTC+8 转北京时间、对齐连续小时并插值补齐、删 2 月 29 日                                                                 | 地市风速历史序列（默认 2000–2025）                                               | [history.py](new_energy/history.py)            |
| 3 资源年分类   | 步骤 2 的历史（训练年，默认 2000–2024）                               | 逐年平均风速立方，按 P25/P75 分位分三类                                                                                 | 每年一行：year、立方均值、资源年类型                                              | [classify.py](new_energy/classify.py)          |
| 4 事件库与标准 | 训练年历史 + 步骤 3 分类                                               | P10/P90 阈值提取持续 ≥3h 的低风/大风事件；统计分年景分月年均次数                                                       | 事件表 + 事件序列字典 + 年均次数标准表                                            | [events.py](new_energy/events.py)              |
| 5 场景生成     | 训练年历史 + 步骤 3/4 结果                                             | GPR 拟合均值 + AR(1) 残差采样 → 逐月水平/软分位数校准 → 按标准插入极端事件（坡度过渡）→ 全局经验分位数映射、均值回拉 | 三类资源年 × 8760 小时风速场景（场景轴年，默认 2025）+ 插入方案 + 残差 lag1 系数 | [generate.py](new_energy/generate.py)          |
| 6 时间轴重排   | 步骤 5 的场景 + 输出年份                                               | 场景轴年份重排到输出年份，闰年补 2 月 29 日（时间插值）                                                                 | 输出年份轴的三类场景                                                              | [pipeline.py](new_energy/pipeline.py)          |
| 7 物理出力     | 步骤 6 场景 + 输出年温度/气压格点[1][1] + 地市机组配置                 | 轮毂高度切变外推、温度气压修正空气密度、分段立方功率曲线；多机型按容量比例叠加后等比缩放到配置装机                      | 地市出力、全省出力表（容量加权平均风速、出力 MW/pu）                              | [power.py](new_energy/power.py)                |
| 8 写文件与返回 | 步骤 7 的全省出力表                                                    | 按`scenario.resource_year_type` 选一类写出 CSV；同时组装结果 JSON                                                     | 出力 CSV + 结果 JSON（见下方输出节）                                              | [biz/runner.py](biz/runner.py)                      |
| 可选 评估      | 训练年历史 + 步骤 3/5 结果                                             | 场景与历史同类年对比：均值/立方均值误差、持续曲线 nRMSE、自相关、爬坡分位数、事件统计                                   | 指标表（内存返回，配置`evaluate: true` 开启）                                   | [evaluate.py](new_energy/evaluate.py)          |

## 输入

**数据文件**：

```
data_dir/                              # 缺省用服务端 data/hainan_era5_grid_csv
├── wind/                              # 风速格点，逐年一份（历史年份范围见下方规则）
│   ├── hainan_wind_2000.csv
│   └── … hainan_wind_2025.csv
├── temperature/                       # 温度格点，只需输出年份（空气密度修正）
│   └── hainan_temperature_{输出年}.csv
└── pressure/                          # 气压格点，只需输出年份
    └── hainan_pressure_{输出年}.csv
```

格点 CSV 通用格式：`time` 列（UTC 口径）+ 各格点数值列；格点列名形如 `前缀_lat_纬度_lon_经度`，前缀按类型为 `wind_speed_100m` / `irradiance_w_m2` / `temperature_2m_k` / `surface_pressure_pa`；命中归属表的单元，格点列须覆盖该单元全部严格边界内格点。缺年份文件、缺格点列、前缀不符 → `ValueError` 并指明文件/单元/坐标。

**配置项**（统一 JSON，`city_configs` 为数组格式，对象含 `city` 字段）：

| 键                                                  | 必填 | 默认                   | 含义                                                       |
| :-------------------------------------------------- | :--- | :--------------------- | :--------------------------------------------------------- |
| `scenario.year` / `scenario.resource_year_type` | 是   | —                     | 输出年份、写出哪一类资源年                                 |
| `output_dir`                                      | 是   | —                     | 出力 CSV 写入目录                                          |
| `city_configs`                                    | 否   | 全省 18 地市机组[2][2] | 各计算单元装机与机型配置；重复单元报错，可只传部分单元     |
| `data_dir`                                        | 否   | 服务端数据目录         | 原始气象数据目录（含 wind/temperature/pressure）           |
| `assignment_file`                                 | 否   | 服务端归属表[1][1]     | 格点归属表（全国表，可换传海南旧表）                       |
| `variable_prefixes`                               | 否   | 服务端前缀常量         | `{kind: 变量前缀}`，声明数据列名前缀（防 10m/100m 混装） |
| `loss_factor`                                     | 否   | 1.0                    | 综合损耗系数                                               |
| `output_city_power`                               | 否   | true                   | 输出表是否附带各地市出力列                                 |
| `evaluate`                                        | 否   | false                  | 是否计算场景评估指标                                       |

**计算单元与匹配规则**（不区分省/市/区域，D-29）：单元名全部命中归属表 → 各单元格点由归属表给出；多单元且任一未命中 → `ValueError`；恰好 1 个单元未命中 → 合法自定义区域，格点 = 传入数据中该类型声明前缀的全部格点列，单元名作输出列键。单元名键格式 = 行政区划码_城市名（DataV `areas_v3` 口径，如 `460100_海口市`）。范围只由 `city_configs` 的键决定（传谁算谁），聚合列（`province_*`）= 所传单元之和/容量加权，列名不随范围变；返回 JSON 的 `cities` 字段标明本次实际计算范围。

机型参数（`turbines` 列表的字段）：

| 字段                                             | 含义（单位）                                  |
| :----------------------------------------------- | :-------------------------------------------- |
| `model` / `count`                            | 机型名称、台数（台）                          |
| `rated_power_mw`                               | 单机额定功率（MW）                            |
| `hub_height_m`                                 | 轮毂高度（米）                                |
| `cut_in_m_s` / `rated_m_s` / `cut_out_m_s` | 切入/额定/切出风速（m/s）                     |
| `efficiency`                                   | 机组效率系数（默认 1.0）                      |
| `wind_shear_exponent`                          | 风切变指数（默认 0.15，100 米外推到轮毂高度） |

多机型出力按 `count × rated_power_mw` 占比叠加后，等比缩放使合计等于该地市 `capacity_mw`。

**算法内部常数**（不进配置，代码内冻结，改动即改结果）：分类分位 P25/P75、事件分位 P10/P90、事件最短持续 3h、校准强度、随机种子。**历史年份**：期望窗口为服务端常量 `DEFAULT_YEARS`（[biz/assignment.py](biz/assignment.py)，默认 2000–2025 两端含），实际使用范围=窗口∩数据目录实际年份，运行时自动解析（连续段校验、逐年行数校验闰 8784/平 8760、年份数下限 12）。

## 输出

`onshore_wind_power_{年份}_{资源年类型}.csv`（列集冻结，结果比对基准件），逐小时一行（闰年 8784 行）：

| 列                                            | 含义（单位）                                         |
| :-------------------------------------------- | :--------------------------------------------------- |
| `time`                                      | 北京时间整点时刻                                     |
| `province_wind_speed_m_s`                   | 所配置地市容量加权平均风速（m/s）                    |
| `province_power_mw` / `province_power_pu` | 所配置地市出力合计（MW / 标幺值）                    |
| `{地市键}_power_mw` × N                    | 各地市出力（MW），`output_city_power: true` 时附带 |

同时返回结果 JSON（结构见 [# 接口 / 返回](#返回)）。

# 光伏

## 全流程

> 统一 JSON 校验+默认值 → 格点辐照度 → 地市历史 + 理想辐照度 → 资源年分类 → 低辐照事件库 → 三类场景 → 物理出力 → 出力 CSV + 结果 JSON。历史年份解析规则同 [# 风电 / 输入](#输入)（光伏取 solar∩temperature）。

| 步骤                       | 输入                                                           | 中间处理                                                                                                                                                               | 输出                                                     | 模块                                                                                  |
| :------------------------- | :------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------- | :------------------------------------------------------------------------------------ |
| 0 校验与默认值             | 统一 JSON（文件路径或 dict）                                   | 必填/类型/未知字段校验，组件`count` 须为正整数、`rated_power_mw` 须为正；`data_dir`/`city_configs` 缺省填服务端默认                                            | 规范化配置（内存）                                       | [biz/request.py](biz/request.py)                                                       |
| 1 读辐照度格点             | `data_dir`（含 solar/temperature 子目录）+ 格点归属表 [1][1] | 按地市严格边界内格点求均值，逐年拼接（UTC 口径）                                                                                                                       | {地市键: 逐年辐照度 Series}                              | [biz/assignment.py](biz/assignment.py)                                                 |
| 2 历史与理想辐照度         | 步骤 1 的地市辐照度                                            | UTC+8 转北京时间、对齐连续小时并插值补齐、删 2 月 29 日；按地市格点平均经纬度计算水平面理想辐照度                                                                      | 辐照度/理想辐照度历史（默认 2000–2025）                 | [history.py](new_energy/history.py) / [generate.py](new_energy/generate.py) |
| 3 资源年分类               | 步骤 2 的历史（训练年，默认 2000–2024）                       | 逐年累计辐照量，按 P25/P75 分位分三类                                                                                                                                  | 每年一行：year、累计辐照量、资源年类型                   | [classify.py](new_energy/classify.py)                                            |
| 4 事件库与标准             | 训练年历史 + 理想辐照度 + 步骤 3 分类                          | 核心日照时段（理想>200 W/m2）识别晴空指数 Kt P10、持续 ≥3h 的低辐照事件；统计分年景分月年均次数                                                                       | 事件表 + 事件序列字典 + 年均次数标准表                   | [events.py](new_energy/events.py)                                                |
| 5 场景生成                 | 训练年历史 + 理想辐照度 + 步骤 3/4 结果                        | 理想-实际差作为辐照损失，GPR 拟合季节/日变化 + 日内 AR(1) 残差抽样 → 45% 温和分位数校准 → 按标准插入低辐照事件（边界 3h 渐变）→ 月累计能量校正（系数限 0.70–1.60） | 三类资源年 × 8760 小时辐照度场景（场景轴年，默认 2025） | [generate.py](new_energy/generate.py)                                            |
| 6 物理出力                 | 步骤 5 场景 + 训练年温度气候态 + 地市组件配置                  | 水平面→组件倾斜面辐照度（POA）、NOCT 组件温度、功率温度系数；多组件按容量占比叠加                                                                                     | 地市出力、区域出力表（MW/pu）                            | [power.py](new_energy/power.py)                                                  |
| 7 时间轴重排、写文件与返回 | 步骤 6 出力表 + 输出年份                                       | 场景轴年份重排到输出年份，闰年补 2 月 29 日（时间插值）；按`scenario.resource_year_type` 选一类写出 CSV；同时组装结果 JSON                                           | 出力 CSV + 结果 JSON（见下方输出节）                     | [pipeline.py](new_energy/pipeline.py) / [biz/runner.py](biz/runner.py)            |

## 输入

**数据文件**（格点 CSV 格式同 [# 风电 / 输入](#输入)）：

```
data_dir/                              # 缺省用服务端 data/hainan_era5_grid_csv
├── solar/                             # 辐照度格点，逐年一份（历史年份范围同风电规则）
│   ├── hainan_solar_2000.csv
│   └── … hainan_solar_2025.csv
└── temperature/                       # 温度格点，逐年（组件温度用训练年逐小时气候平均）
    └── hainan_temperature_2000.csv … hainan_temperature_2025.csv
```

**配置项**（与风电共用骨架，公共键见 [# 风电 / 输入](#输入) 配置表；差异如下）：

| 键               | 必填 | 默认               | 含义                                   |
| :--------------- | :--- | :----------------- | :------------------------------------- |
| `city_configs` | 否   | 儋州区域机组[3][3] | 各计算单元装机与组件配置；重复单元报错 |
| `loss_factor`  | 否   | 1.0                | 汇集、逆变及线路等综合损耗系数         |
| `albedo`       | 否   | 0.2                | 地面反照率                             |

**计算范围**：与风电同规则（单元名匹配规则见 [# 风电 / 输入](#输入)），范围只由 `city_configs` 的键决定，`区域总出力_*` = 所配置单元之和；未命中归属表的单单元经纬度缺省取传入数据格点坐标均值（`region_center`）。

组件参数（`components` 列表的字段）：

| 字段                           | 含义（单位）                   |
| :----------------------------- | :----------------------------- |
| `name`                       | 组件名称                       |
| `count`                      | 组件等效台数（正整数）         |
| `rated_power_mw`             | 单台额定功率（MW，正数）       |
| `tilt_deg` / `azimuth_deg` | 组件倾角、方位角（度）         |
| `noct_c`                     | 组件额定工作温度（℃）         |
| `gamma_p_per_c`              | 功率温度系数（1/℃）           |
| `efficiency`                 | 组件或设备效率系数（默认 1.0） |

多组件出力按 `count × rated_power_mw` 配比缩放到地市 `capacity_mw` 后叠加。地市的 `latitude`/`longitude`（可选，缺省=归属表严格边界内格点平均坐标，即 `city_centers`，与场景生成同口径）用于倾斜面辐照度计算（POA），须同时提供或同时缺省。

**算法内部常数**（不进配置，代码内冻结，改动即改结果）：昼夜阈值 50 W/m2、事件核心日照阈值 200 W/m2、晴空指数分位 P10、事件最短持续 3h、分位数校准强度 0.45、月度修正系数限 0.70–1.60、GPR 抽样 900 点、随机种子。**历史年份**：解析规则同风电（光伏取 solar∩temperature 目录交集）。

## 输出

`pv_power_{年份}_{资源年类型}.csv`（列集与旧版区域汇总一致），逐小时一行：

| 列                                    | 含义（单位）                                           |
| :------------------------------------ | :----------------------------------------------------- |
| `时间`                              | 北京时间整点时刻                                       |
| `{地市键}_总出力_MW` × N           | 各地市总出力（MW），`output_city_power: true` 时附带 |
| `区域总出力_MW` / `区域总出力_pu` | 所配置地市出力合计（MW / 标幺值）                      |

同时返回结果 JSON（结构见 [# 接口 / 返回](#返回)）。

> 组件级中间量（各组件 POA/组件温度等）如需对外输出再按需补。回归基准件见 `.refactor_baseline/`。

# 接口

> http://127.0.0.1:9998/（启动见 Quick Start）。同步阻塞式，一个请求等到算完直接返回结果（无后台任务）。
> 参数与默认值同上文各"输入"节配置表：`scenario`/`output_dir` 必填，其余可选缺省走服务端默认；归属表、分位阈值、校准强度、随机种子服务端冻结不暴露；历史年份不进请求（解析规则见 [# 风电 / 输入](#输入)）。
> 统一返回体 `{"success", "message", "data", "timestamp"}`，HTTP 状态码语义化：参数/数据错 400（`message` 指明字段/文件/地市）、未捕获异常 500；响应超 1KB 自动 gzip，跨域全放开（`allow_origins=["*"]`）。

## GET /api/health

> 健康检查，无参纯读

> **return**

```json
{"status": "ok", "message": "服务正常", "timestamp": "2026-10-18T18:55:00.060230"}
```

## POST /api/wind/run

> 风电出力场景计算；单市几十秒，全省约 4 分钟（反代部署时 `proxy_read_timeout` 设 ≥ 300s）。请求体即统一 JSON，校验与默认值在 pipeline 入口一次完成

> **传参**

```json
{
  "data_dir": "E:\\...\\hainan_era5_grid_csv",      // 可选，默认服务端数据目录（需含 wind/ + 输出年份 temperature/、pressure/）
  "output_dir": "E:\\...\\result",                   // 必填，出力 CSV 写入目录
  "scenario": {
    "year": 2024,                                    // 必填，输出年份（场景重排到该年时间轴，闰年 8784 小时）
    "resource_year_type": "正常资源年"                // 必填，偏小/正常/偏大资源年之一
  },
  "city_configs": [                                  // 可选，默认全省 18 地市
    {
      "city": "460100_海口市",                        // 必填，行政区代码_地市名，须在归属表内，重复报错
      "capacity_mw": 22.29,                          // 必填，装机容量（MW）
      "turbines": [                                  // 必填，机组列表
        {
          "model": "6MW机组",                         // 机型名称
          "count": 1,                                 // 台数
          "rated_power_mw": 6,                        // 单机额定功率（MW），须为正
          "hub_height_m": 140,                        // 轮毂高度（m），100m 风速切变外推至此
          "cut_in_m_s": 3,                            // 切入风速（m/s）
          "rated_m_s": 11,                            // 额定风速（m/s）
          "cut_out_m_s": 25,                          // 切出风速（m/s）
          "efficiency": 0.94,                         // 功率曲线效率系数
          "wind_shear_exponent": 0.15                 // 风切变指数（幂律外推）
        }
      ]
    }
    // ...
  ],
  "loss_factor": 1.0,                                // 可选，默认 1.0，综合损耗系数
  "output_city_power": true,                         // 可选，默认 true，输出表是否附带各地市出力列
  "evaluate": false                                  // 可选，默认 false，是否计算场景评估指标（仅服务端计算，不进返回体）
}
```

> **return**

```json
{
  "success": true, "message": "",
  "data": {
    "output_file": "...\\onshore_wind_power_2024_正常资源年.csv",  // 出力 CSV 路径
    "elapsed_s": 13.2,                                             // 全流程耗时（秒）
    "scenario": {"year": 2024, "resource_year_type": "正常资源年"},  // 本次场景设定（回显）
    "cities": ["460100_海口市"],                                    // 本次实际计算范围（传谁算谁）
    "total_capacity_mw": 22.29,                                    // 本次计算范围总装机
    "power_table": {                                               // 与 CSV 同列同值，列名→8760/8784 长数组
      "time": ["2024-01-01 00:00:00", "..."],
      "province_wind_speed_m_s": [7.31, "..."],
      "province_power_mw": [8.02, "..."],
      "province_power_pu": [0.360, "..."],
      "460100_海口市_power_mw": [8.02, "..."]
    }
  },
  "timestamp": "2026-10-18T18:55:23.1"
}
```

> **return: 参数/数据错误（HTTP 400）**

```json
{"success": false, "message": "请求缺少必填字段 output_dir", "data": null, "timestamp": "..."}
```

## POST /api/solar/run

> 光伏出力场景计算，与风电同构；默认儋州约 30 秒

> **传参**（与风电差异：city 内为 `components` + `latitude`/`longitude`，另有 `albedo`，无 `evaluate`）

```json
{
  "data_dir": "E:\\...\\hainan_era5_grid_csv",      // 可选，默认服务端数据目录（需含 solar/ + temperature/ 历史年份）
  "output_dir": "E:\\...\\result",                   // 必填，出力 CSV 写入目录
  "scenario": {
    "year": 2025,                                    // 必填，输出年份
    "resource_year_type": "正常资源年"                // 必填，偏小/正常/偏大资源年之一
  },
  "city_configs": [                                  // 可选，默认儋州
    {
      "city": "460400_儋州市",                       // 必填，行政区代码_地市名
      "capacity_mw": 955.21,                         // 必填，装机容量（MW）
      "latitude": 19.56,                             // 可选，默认=地市格点中心纬度，算 POA 用
      "longitude": 109.58,                           // 可选，默认=地市格点中心经度，算 POA 用
      "components": [                                // 必填，组件列表，count×rated 配比缩放到地市装机
        {
          "name": "常规组件",                         // 组件名称
          "count": 3,                                 // 组件等效台数（正整数）
          "rated_power_mw": 200.0,                    // 单台额定功率（MW，正数）
          "tilt_deg": 18.0,                           // 安装倾角（度）
          "azimuth_deg": 180.0,                       // 方位角（度，180 = 正南）
          "noct_c": 45.0,                             // NOCT 额定工作温度（℃），算组件温度用
          "gamma_p_per_c": -0.0040,                   // 功率温度系数（/℃）
          "efficiency": 1.0                           // 组件效率系数
        },
        {"name": "低温升高效组件", "count": 2, "rated_power_mw": 200.0, "tilt_deg": 15.0, "azimuth_deg": 180.0,
         "noct_c": 42.0, "gamma_p_per_c": -0.0035, "efficiency": 1.0}
      ]
    }
  ],
  "loss_factor": 0.90,                               // 可选，默认 1.0，汇集、逆变及线路等综合损耗系数
  "albedo": 0.20,                                    // 可选，默认 0.2，地面反照率
  "output_city_power": true                          // 可选，默认 true，输出表是否附带各地市出力列
}
```

> **return**

```json
{
  "success": true, "message": "",
  "data": {
    "output_file": "...\\pv_power_2025_正常资源年.csv",  // 出力 CSV 路径
    "elapsed_s": 22.9,                                    // 全流程耗时（秒）
    "scenario": {"year": 2025, "resource_year_type": "正常资源年"},  // 本次场景设定（回显）
    "cities": ["460400_儋州市"],                          // 本次实际计算范围（传谁算谁）
    "total_capacity_mw": 955.21,                          // 本次计算范围总装机
    "power_table": {                                      // 与 CSV 同列同值，列名→8760/8784 长数组
      "时间": ["2025-01-01 00:00:00", "..."],
      "460400_儋州市_总出力_MW": [0.0, "..."],
      "区域总出力_MW": [0.0, "..."],
      "区域总出力_pu": [0.0, "..."]
    }
  },
  "timestamp": "2026-10-18T18:55:23.1"
}
```

## 业务层直调（CLI 与 HTTP 同路）

```python
from biz.runner import run

response = run("wind", payload_dict)   # 光伏: run("solar", ...)；或传 JSON 文件路径: run("wind", Path("config.json"))
# response 即上方 return 中 data 的内容（去掉 success/message/timestamp 包装）
```

# Reference

<!-- 数据文件 -->

[1]: ./data/全国格点地市归属.csv
<!-- 服务端默认机组配置 -->

[2]: ./biz/default_city_configs_wind.json
[3]: ./biz/default_city_configs_solar.json
