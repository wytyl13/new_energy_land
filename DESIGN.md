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

- 历史年份按数据目录解析（默认期望窗口 2000–2025，规则见 §3）：训练集=窗口内除最后一年，场景生成固定在最后一年 8760 小时轴（日历模板），重排到输出年份
- 回归对齐冻结基准件（`.refactor_baseline/`）
- 缺省默认：不传机组/数据目录时用服务端默认（数据目录=`data/hainan_era5_grid_csv`；机组：风电=全省 18 地市、光伏=儋州）
- 结果双份返回：出力 CSV 写入 `output_dir` + JSON 表格数据返回

## 3. 数据设计

> 输入/输出/配置的格式与口径；详细列表见 README 各"输入/输出"节。

- **格点气象 CSV**：`data/hainan_era5_grid_csv/{wind,solar,temperature,pressure}/hainan_{类型}_{年}.csv`，UTC 口径，time + 各格点列
- **格点归属表**：`data/海南地市分区/海南共同80格点_地市归属.csv`，地市 → 严格边界内格点列名（服务端写死，与算法版本绑定）
- **统一 JSON 格式（风光共用骨架）**：`city_configs` 为数组（对象含 `city`），重复地市报错；字段归属与默认值见 §5；city 内部按域分化（风电 `turbines[]`，光伏 `components[]`+`latitude`/`longitude`）
- **计算范围规则**：范围只由 `city_configs` 的键决定（传谁算谁）；聚合列（`province_*`/`区域总出力_*`）= 所传地市之和/容量加权，**列名不随范围变**，范围靠返回 `cities` 字段自曝；`data_dir` 只决定数据来源，须涵盖所传全部地市，缺文件/缺格点列/地市不在归属表均报错
- **历史年份解析**（运行时按数据目录确定，`common/city_weather.resolve_years`）：期望窗口=服务端常量 `DEFAULT_YEARS`（`common/request.py`，默认 2000–2025 两端含）∩ 各流程实际 kind 年份（风电=wind，光伏=solar∩temperature）；取最大连续段，段外年份告警丢弃；段内逐年行数校验（闰年 8784/平年 8760，不符 `ValueError`）；年份数下限 12；训练年=除最后一年，场景轴年=最后一年（数据不参与统计，只当日历模板）
- **算法内部常数**（不进配置，代码内冻结，改动即改结果）：分位阈值 P25/P75、P10/P90、事件最短持续 3h、校准强度、随机种子
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
公共层   common/                格点读取与历史年份解析（city_weather）、时间轴（time_axis）、统一请求解析（request）
```

- 原 `wind/run.py` 接口层随配置统一删除：`from_request` 的校验逻辑并入 pipeline 入口
- 旧部署脚本（`陆上全省风电出力部署/`、`光伏出力部署/`）保留作参照，不再演进
- 回归基准件在 `.refactor_baseline/`（风电全省 CSV + 光伏儋州输出）

## 5. 接口设计

> 对外唯一入口 = pipeline 的 JSON 接口；CLI 与 HTTP 服务层同一路。

**统一 JSON（风光共用骨架，city 内部按域分化）**

- pipeline 入参两种形态：JSON **文件路径**（CLI 用）或 JSON **dict**（服务层用），pipeline 内部识别
- 校验与默认值填充在 pipeline 入口完成，是全项目唯一校验点
- 字段归属：必填 `scenario.year`/`scenario.resource_year_type`/`output_dir`；可选（默认）`data_dir`、`city_configs`、`loss_factor`、`albedo`、`output_city_power`、`evaluate`；服务端常量：归属表（默认）、历史年份期望窗口（`DEFAULT_YEARS`，实际范围随数据目录收窄，不进请求字段）；冻结常数：分位阈值、校准强度、随机种子

**返回（写文件 + JSON 双份）**

- 出力 CSV 写入 `output_dir`（列集冻结，回归基准）
- 同时返回 JSON：`{"output_file", "elapsed_s", "scenario", "cities", "total_capacity_mw", "power_table"}`，`power_table` 为列名→数组的字典，与 CSV 同列同值

**错误约定**

- 全包禁止 try/except 吞错；请求/数据类错误一律 `ValueError`（消息指明字段/文件/地市），缺数据文件也统一转 `ValueError`
- 服务层映射：`ValueError` → 400（返回错误信息），其它异常 → 500

**HTTP（已实现，`api/server/main_server.py`，端口 9998）**

- 同步阻塞式 `POST /api/wind/run`、`POST /api/solar/run`：请求体即统一 JSON dict，返回即上述返回 JSON
- 编写规则按 skill `service-api`；错误映射、gzip/CORS 与耗时参考见 README 接口节

## 6. 实施规划与进度

> 宏观步骤表；唯一高频更新区。

| 步骤                                                                    | 状态                     | 验证结果                                                            |
| :---------------------------------------------------------------------- | :----------------------- | :------------------------------------------------------------------ |
| 风电：旧部署重构进`new_energy/wind/`                                  | 已完成                   | 全省回归与冻结基准一致（`tests/wind_run_selftest.py`，约 4 分钟） |
| 光伏：旧部署重构进`new_energy/solar/`，两步文件接力合并为内存单次调用 | 已完成                   | 儋州回归与旧部署脚本一致（`tests/solar_selftest.py`，约 30 秒）   |
| 风电：服务层接口`wind/run.py`（from_request + run）                   | 已完成（随配置统一将并入 pipeline 后删除） | 接口自测通过（单市/字典数组等价/非法请求校验）                      |
| 配置统一：pipeline 直收统一 JSON（文件/dict）、默认值、双份返回、错误约定——风电 | 已完成                   | 四用例全过：全省回归（判据见 README 自测）、单市一致、默认值、非法请求（`tests/wind_run_selftest.py`） |
| 配置统一：光伏同模式改造                                               | 已完成                   | 三用例全过：回归对冻结基准、默认值等价、非法请求（`tests/solar_selftest.py`） |
| HTTP 服务层（`POST /api/*/run`）                                      | 已完成                   | 冒烟通过：health 200、缺参 400、风光真实计算返回全量 power_table（`api/server/main_server.py`，端口 9998） |
| 收口：README 按`readme-structure` 定稿                                | 已完成                   | 已按 `readme-structure` 重排并同步接口约定 |
| 历史年份动态化（详见 §7 同日决策）                                    | 已完成                   | 真实目录解析=2000–2025；风 4 光 3 用例全过，回归不变 |
| 常数收拢（详见 §7 同日决策）                                          | 已完成                   | 全库各只剩一处定义；风 4 光 3 用例全过，回归不变 |

**当前进行到**：上表全部已完成；无待办。

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
| 2026-10-18 | 配置三种表示并存（旧 dict / 请求体 / run() 手工互转），且服务层需要默认值与 JSON 表格返回 | 删接口层`run.py`，pipeline 直收统一 JSON（文件路径/dict，即请求体格式，数组`city_configs`可查重复地市）；缺省默认：`data_dir`=服务端目录、`city_configs`风电=18 地市/光伏=儋州、`output_dir` 必传；结果双份（CSV + JSON `power_table`）；错误约定：请求/数据错`ValueError`，服务层 400/其它 500 | 一种格式一种校验，CLI 与服务同路；聚合列名不随范围变，`cities` 自曝范围 |
| 2026-10-18 | HTTP 服务层编写规范如何定 | 自旧项目 industrial_vision 提取整理为通用 skill `service-api`（skills 17→18 条）：目录分层（api/core/算法包三块、依赖单向）、schema 三处分工、算法包只调唯一入口、POST 风格方法选择、`make_response` 返回体；错误风格弃源码的"一律 200 code 在 body"，改语义化状态码（400/404/409/500）+ 同款 body | 复用已验证的服务规范；语义化状态码利于监控告警与前端拦截器，与 §5 已定的 ValueError→400 约定一致 |
| 2026-10-18 | 计算接口是否后台任务化（task_id+轮询） | 否决后台任务提案，保持同步阻塞：一个请求等到算完直接返回结果 JSON；请求体按 skill 3.3 透传例外不走 Pydantic（校验唯一在 pipeline 入口） | 接口最少、前端一次请求拿全量；无 DB 无处存任务表；部署侧调大代理超时（nginx proxy_read_timeout≥300s）即可覆盖全省 4 分钟 |
| 2026-10-19 | 历史年份三常量（SOURCE/TRAIN/TARGET）写死 2000–2025，与数据目录实际内容脱节（wind/solar 实有 1950 起、temperature/pressure 2000 起、2026 为残年），且改口径要改两处代码 | 历史年份运行时解析（`resolve_years`）：期望窗口=`DEFAULT_YEARS`（2000–2025，改口径只改 `common/request.py`）∩ 目录实际年份（风电=wind，光伏=solar∩temperature），取最大连续段（段外告警），行数校验闰 8784/平 8760，年份下限 12；训练年=除最后一年、场景轴年=最后一年（`TARGET_YEAR`→`scenario_axis_year`，只当日历模板）；删全部写死默认年份 | 数据目录是年份事实来源；窗口钳制使口径变更须显式改常量；部分取代训练年份冻结决策（仍不进请求）；真实目录解析=2000–2025，回归不变 |
| 2026-10-19 | `RANDOM_SEED`/`CASE_NAMES`/`ENC` 三组常数多处重复定义（同一事实两到四处维护，易漂移） | 收拢进 `common/request.py` 唯一定义：`RANDOM_SEED`、`ENC`；`CASE_NAMES` 删除，统一用既有 `RESOURCE_YEAR_TYPES` | 口径/编码改动只改一处；值不变故回归不变（风 4 光 3 用例全过）；对外契约零变化 |
| 2026-10-19 | 新建 .py 文件的文件头无统一规则（用户有 VSCode 片段 pyheader 但仅限手工触发） | 新增通用 skill `code-header`（skills 18→19 条）：新建 .py 文件写 shebang + 编码声明 + `@Time/@Author/@File` 元信息块；@Time 取创建时刻（date 命令）、@Author 固定 weiyutao、@File 不带路径；模块说明并入同一 docstring；旧文件不回补 | 用户的文件头习惯固化为规则，AI 新建文件自动遵循；不回补旧文件避免无信息量 diff（用户明示要求创建该 skill） |
| 2026-10-19 | CLAUDE.md 文档分工段的 skill 数量"（18 条）"随新增 skill 过期（实际 19） | 删去数字，只留"通用项目规则在 `.claude/skills/`"（用户原话："筛掉数字吧"） | 数量每加一条 skill 就过期，删掉后不再需要同步；留痕按 doc-roles §3（非用户明示不可改区的明示修改） |
| 2026-10-19 | 存量 .py 文件无 code-header 文件头，新旧文件格式不一致 | 一次性批量回补：28 个有内容文件加 shebang+编码+`@Time/@Author/@File` 头（@Time 用 git 首次提交时间 2026/10/18 19:35，docstring 并入同一三引号块），3 个空 `__init__.py` 跳过；skill 第 3 条改为"默认不主动回补，明示时批量同步" | 用户明示要求同步（"直接用这个 code-header skill 去同步格式吧"）；纯注释/docstring 改动，风电 4 用例、光伏 3 用例回归全过 |
| 2026-10-19 | 文档冗长：同一事实多处复制、同构章节重复、理由/验证列铺过程 | `style-wording` 新增 §3"长度与去重"（同一事实只写一处、同构只写差异、理由与验证一行、示例最小集）；按新规则压缩 README 与 DESIGN 措辞（§6/§7 缩句、§5 HTTP 状态改"已实现"、§3 统一 JSON 条目指向 §5），事实与决策内容不变 | 用户："我希望你写的能简明扼要一些"，选定"扩充规则+精简现有文档" |
| 2026-10-19 | 接口示例 JSON 多个字段注释只写"可选，默认 X"漏用途（光伏 loss_factor/albedo/output_city_power、两处返回示例等，已补齐） | `readme-structure` §3.5 收紧：字段注释必含"必填/可选+默认值、用途"两项，传参与返回同等要求，同构端点差异字段也不例外 | 用户："把这个问题同步更新到对应的skills，下次不要再犯" |
| 2026-10-19 | 代码是否过度封装（用户以 request.py 函数数为例）；原则=最少代码完成复杂逻辑，不牺牲性能/效率/规范 | `code-function` 新增 §1"最少代码判据"（不写/复用/标准库/一行/最少实现五档，硬约束四条：不牺牲性能、校验与冻结约定照旧、封装须 ≥2 调用方或独立逻辑、删优先于加），原章节顺延；AST 全库扫描 30 个单调用方函数逐个核验均有独立逻辑，代码零改动 | 用户："能不写代码就不写代码……不能以牺牲程序的性能、效率和逻辑规范性为代价"，选定"集成+审一遍代码"；request.py 的 fail 有 53 处调用（编码"校验=ValueError"约定），保留 |
| 2026-10-21 | 光伏组件容量传参 `capacity_share`（合计须=1）与风电 `turbines` 的 count×rated 风格不一致 | 组件参数改为 `count` + `rated_power_mw`，出力按 count×rated 配比缩放到地市 `capacity_mw`（scale 同风电 power.py）；pipeline 校验 count 正整数、rated 为正，删合计=1 校验；默认配置/测试基准换算为 3×200+2×200 MW（等价 0.6/0.4） | 用户明示（"把 capacity_share 改为 count 和 rated_power_mw，其余不变"）；与风电及陆上全省部署风格统一，回归数值不变（`tests/solar_selftest.py` 三用例全过） |
| 2026-10-21 | 光伏 city 的 `latitude`/`longitude` 必填，多市配置手写易错且与部署脚本自动取格点中心不一致 | 两字段改可选：缺省=归属表严格边界内格点平均坐标（复用 `history.city_centers`，与场景生成同口径）；须同时提供或同时缺省，显式传入时范围校验不变；同日另修陆上全省光伏部署脚本闰年错位（2024 温度轴含 2/29，`[:8760]` 截断致 3 月起温度错一天，改为删闰日后对齐）并删死代码（未用的 read_city_weather/气压读取等） | 用户明示（"这两个字段设置为可选参数吧""上次说的一些bug也调整一下"）；solar_selftest 三用例全过 |
