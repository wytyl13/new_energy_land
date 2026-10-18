# DESIGN

> 本项目唯一设计与规划文件：设计（§1–§5 冻结区）+ 规划进度（§6）+ 决策（§7 只增）。
> 运行说明、模块速查等稳定说明见 `README.md`，此处不重复。

## 1. 定位

> 读海南 ERA5 气象格点数据（风速/辐照度/温度/气压），生成三类资源年（偏小/正常/偏大）的新能源出力场景，经物理模型转为全省/区域逐小时出力。

- 输入：逐年格点气象 CSV + 格点归属表 + 装机配置
- 输出：出力 CSV 文件 + JSON 表格数据（双份）

## 2. 需求

> 已确认的需求；改本区先在 §7 留痕。

**风电**

- 全省陆上风电出力场景生成：格点风速 → 地市历史 → 资源年分类 → 极端事件 → 三类场景 → 物理出力（多机型叠加、空气密度修正、切变外推）
- 服务层接口：统一 JSON 直传 pipeline，缺省走默认值

**光伏**

- 区域光伏出力场景生成：格点辐照度 → 历史 + 理想辐照度 → 分类 → 低辐照事件 → 三类场景 → 物理出力（POA、组件温度修正、多组件叠加）
- 服务层接口：同风电形态

**共性**

- 训练集固定 2000–2024 年，场景生成固定在 2025 年 8760 小时轴，重排到输出年份
- 回归对齐冻结基准件（`.refactor_baseline/`）
- 缺省默认：不传机组/数据目录时用服务端默认（数据目录=`data/hainan_era5_grid_csv`；机组：风电=全省 18 地市、光伏=儋州）
- 结果双份返回：出力 CSV 写入 `output_dir` + JSON 表格数据返回

## 3. 数据设计

> 输入/输出/配置的格式与口径；详细列表见 README 各"输入/输出"节。

- **格点气象 CSV**：`data/hainan_era5_grid_csv/{wind,solar,temperature,pressure}/hainan_{类型}_{年}.csv`，UTC 口径，time + 各格点列
- **格点归属表**：`data/海南地市分区/海南共同80格点_地市归属.csv`，地市 → 严格边界内格点列名（服务端写死，与算法版本绑定）
- **统一 JSON 格式（风光共用骨架）**：`city_configs` 为数组（对象含 `city` 字段），重复地市报错；必填 `scenario.year`、`scenario.resource_year_type`、`output_dir`；可选及默认：`data_dir`（服务端默认数据目录）、`city_configs`（风电=全省 18 地市、光伏=儋州）、`loss_factor` 1.0、`albedo` 0.2（光伏）、`output_city_power` true、`evaluate` false（风电）；city 内部按域分化（风电 `turbines[]`，光伏 `components[]`+`latitude`/`longitude`）
- **计算范围规则**：范围只由 `city_configs` 的键决定（传谁算谁）；聚合列（`province_*`/`区域总出力_*`）= 所传地市之和/容量加权，**列名不随范围变**，范围靠返回 `cities` 字段自曝；`data_dir` 只决定数据来源，须涵盖所传全部地市，缺文件/缺格点列/地市不在归属表均报错
- **算法内部常数**（不进配置，代码内冻结，改动即改结果）：训练年份 2000–2024、分位阈值 P25/P75、P10/P90、事件最短持续 3h、校准强度、随机种子
- **输出列集冻结**：风电 `time, province_wind_speed_m_s, province_power_mw, province_power_pu, {地市键}_power_mw×N`；光伏 `时间, {地市键}_总出力_MW×N, 区域总出力_MW, 区域总出力_pu`——列集是结果比对基准，改列即破坏回归
- **返回 JSON 骨架（风光共用）**：`output_file`、`elapsed_s`、`scenario`、`cities`（实际计算范围）、`total_capacity_mw`、`power_table`（列名→8760/8784 长数组，与 CSV 同列同值）

## 4. 结构设计

> 算法包 `new_energy` 分层，依赖单向、禁止反向 import；风电/光伏共用公共层。

```
入口层   wind/run_wind.py       CLI（本地调试）：JSON 文件路径 → pipeline
         solar/run_solar.py     同上（光伏）
         （HTTP 服务层直接传 JSON dict 给 pipeline，不经 CLI）
  ↓
组织层   wind/pipeline.py       唯一入口：识别 JSON 文件/dict → 校验+默认值 → 全流程 → WindResult
         solar/pipeline.py      同上（光伏）→ SolarResult
  ↓
单元层   wind/{history,classify,events,generate,power,evaluate}.py
         solar/{history,classify,events,generate,power}.py
                                每个计算步骤一个模块，互不知道彼此
  ↓
公共层   common/                格点读取（city_weather）、时间轴（time_axis）、统一请求解析（request）
```

- 原 `wind/run.py` 接口层随配置统一删除：`from_request` 的校验逻辑并入 pipeline 入口
- 旧部署脚本（`陆上全省风电出力部署/`、`光伏出力部署/`）保留作参照，不再演进
- 回归基准件在 `.refactor_baseline/`（风电全省 CSV + 光伏儋州输出）

## 5. 接口设计

> 对外唯一入口 = pipeline 的 JSON 接口；CLI 与 HTTP 服务层同一路。

**统一 JSON（风光共用骨架，city 内部按域分化）**

- pipeline 入参两种形态：JSON **文件路径**（CLI 用）或 JSON **dict**（服务层用），pipeline 内部识别
- 校验与默认值填充在 pipeline 入口完成，是全项目唯一校验点
- 字段归属：必填 `scenario.year`/`scenario.resource_year_type`/`output_dir`；可选（默认）`data_dir`、`city_configs`、`loss_factor`、`albedo`、`output_city_power`、`evaluate`；服务端写死：归属表（默认）、训练年份/阈值/种子（冻结常数）

**返回（写文件 + JSON 双份）**

- 出力 CSV 写入 `output_dir`（列集冻结，回归基准）
- 同时返回 JSON：`{"output_file", "elapsed_s", "scenario", "cities", "total_capacity_mw", "power_table"}`，`power_table` 为列名→数组的字典，与 CSV 同列同值

**错误约定**

- 全包禁止 try/except 吞错；请求/数据类错误一律 `ValueError`（消息指明字段/文件/地市），缺数据文件也统一转 `ValueError`
- 服务层映射：`ValueError` → 400（返回错误信息），其它异常 → 500

**HTTP（规划，未开工）**

- 同步阻塞式 `POST /api/wind/run`、`POST /api/solar/run`：请求体即统一 JSON dict，返回即上述返回 JSON
- 编写规则按 skill `service-api`（18 条之一）：`make_response` 返回体、语义化状态码、算法包只调唯一入口、`asyncio.to_thread` 包同步算法
- 耗时参考：单市几十秒，全省约 4 分钟，超时按此设置

## 6. 实施规划与进度

> 宏观步骤表；唯一高频更新区。

| 步骤                                                                    | 状态                     | 验证结果                                                            |
| :---------------------------------------------------------------------- | :----------------------- | :------------------------------------------------------------------ |
| 风电：旧部署重构进`new_energy/wind/`                                  | 已完成                   | 全省回归与冻结基准一致（`tests/wind_run_selftest.py`，约 4 分钟） |
| 光伏：旧部署重构进`new_energy/solar/`，两步文件接力合并为内存单次调用 | 已完成                   | 儋州回归与旧部署脚本一致（`tests/solar_selftest.py`，约 30 秒）   |
| 风电：服务层接口`wind/run.py`（from_request + run）                   | 已完成（随配置统一将并入 pipeline 后删除） | 接口自测通过（单市/字典数组等价/非法请求校验）                      |
| 配置统一：pipeline 直收统一 JSON（文件/dict）、默认值、双份返回、错误约定——风电 | 已完成                   | 四用例全过：全省回归对基准总量≤1e-6/逐元素≤1e-4、单市一致性、默认值填充、12 项非法请求（`tests/wind_run_selftest.py`） |
| 配置统一：光伏同模式改造                                               | 已完成                   | 三用例全过：儋州三景全量回归对冻结基准（场景+出力，总量≤1e-6）、默认值/文件dict等价、12 项非法请求（`tests/solar_selftest.py`，约 1 分钟） |
| HTTP 服务层（`POST /api/*/run`）                                      | 已完成                   | 冒烟四项通过（`api/server/main_server.py`，端口 9998）：health 200、缺 output_dir→400 带字段名、光伏儋州真实计算 23s 返回全量 power_table、风电单市 13s 返回 8760×5 |
| 收口：README 按`readme-structure` 定稿                                | 已完成                   | 统一 JSON 输入/双份返回/默认值/错误约定已同步进 README，章节按规则重排 |

**当前进行到**：配置统一、回归、HTTP 服务层（9998 端口）、README 同步均已完成；无待办。

## 7. 决策记录

> 每个拍板一行；只增。2026-10-18 建 DESIGN.md 时自 README 与代码状态回溯补记，早期条目日期不详，标"回溯"。

| 日期       | 问题                                                      | 选择                                                                 | 理由                                                 |
| :--------- | :-------------------------------------------------------- | :------------------------------------------------------------------- | :--------------------------------------------------- |
| 回溯       | 风电/光伏旧部署脚本如何演进                               | 重构为分层包`new_energy`（接口/入口/组织/单元/公共五层，依赖单向）  | 两流程共用公共层，回归可自动化对照冻结基准           |
| 回溯       | 光伏场景生成与物理模型两步文件接力                        | 合并为一次内存调用，中间结果不落盘                                   | 少一次 I/O，消除中间文件口径漂移                     |
| 回溯       | 训练年份、分位阈值、校准强度、随机种子是否暴露配置        | 代码内冻结，不进配置、不进接口                                       | 改动即改结果；对外暴露面最小                         |
| 回溯       | 旧配置键`project_root`/`scene_root`                   | 取消/废弃                                                            | 接口传绝对路径；场景改为现场生成，无现成场景目录可读 |
| 回溯       | 光伏旧部署的地市明细/摘要/评估图等附加产物                | 不随迁移保留                                                         | 一次性附加产物，需要时再按需补                       |
| 2026-10-18 | 设计/进度内容全在 README，CLAUDE.md 规定的 DESIGN.md 缺失 | 建 DESIGN.md 为唯一设计/进度文件，内容自 README 与代码状态回溯       | 对齐文档分工；README 回归稳定说明                    |
| 2026-10-18 | 配置三种表示并存（旧 dict / 请求体 / run() 手工互转），且服务层需要默认值与 JSON 表格返回 | 删接口层`run.py`，pipeline 成为唯一入口，直收统一 JSON（文件路径或 dict）；统一 JSON=请求体格式（数组`city_configs`，可查重复地市）；缺省默认：`data_dir`=服务端数据目录、`city_configs`风电=全省 18 地市/光伏=儋州；`output_dir` 必传；结果双份（CSV + JSON `power_table` 列名→数组）；错误约定：请求/数据错误一律`ValueError`上抛，服务层`ValueError`→400、其它→500 | 一种格式一种校验，CLI 与服务同路；聚合列名不随范围变，靠返回`cities`自曝范围；数组格式可查 JSON 字典查不了的重复地市 |
| 2026-10-18 | HTTP 服务层编写规范如何定 | 自旧项目 industrial_vision 提取整理为通用 skill `service-api`（skills 17→18 条）：目录分层（api/core/算法包三块、依赖单向）、schema 三处分工、算法包只调唯一入口、POST 风格方法选择、`make_response` 返回体；错误风格弃源码的"一律 200 code 在 body"，改语义化状态码（400/404/409/500）+ 同款 body | 复用已验证的服务规范；语义化状态码利于监控告警与前端拦截器，与 §5 已定的 ValueError→400 约定一致 |
| 2026-10-18 | 计算接口是否后台任务化（task_id+轮询） | 否决后台任务提案，保持同步阻塞：一个请求等到算完直接返回结果 JSON；请求体按 skill 3.3 透传例外不走 Pydantic（校验唯一在 pipeline 入口） | 接口最少、前端一次请求拿全量；无 DB 无处存任务表；部署侧调大代理超时（nginx proxy_read_timeout≥300s）即可覆盖全省 4 分钟 |
