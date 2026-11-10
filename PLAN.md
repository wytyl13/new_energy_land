# 计划与进度

## 当前状态
- 正在做：P10 全部完成（P10.6/P10.7 待用户核对）；P12 待核对
- 挂起：提交 P6.2~P6.5（P6.4 已完成）
- 下一步：evaluate=true fixture 覆盖缺口
- 阻塞：无

## 待决定
- Q-01 原 skill `api-json-format` 里定稿格式的去处 → D-27
- Q-02 前端气象数据传递机制：文件上传 / 路径引用（风速 26 年约 170 MB，JSON 不可内嵌）
- Q-03 格点清单给前端的方式：静态文件 / GET 接口

## 步骤
| 编号 | 步骤 | 状态 | 验证结果 |
|---|---|---|---|
| P1 | 重构 skills 与 CLAUDE.md | 已完成 | 见本次提交说明 |
| P2 | DESIGN.md 迁移：算法设计〔草案〕四节 + 流程图 | 已完成 | 内容自源码逐模块核对；图 `docs/数据流转网络图.png` 已生成 |
| P3 | 全国格点地市归属表生成（独立于算法包的数据工具） | 已完成 | `make_national_grid_assignment.py` → `全国格点地市归属.csv`（15440 点/370 单元/33 省，澳门无格点）；海南 47 个 inside 点与既有表逐点比对城市 100% 一致 |
| P4 | 单元名匹配规则 + 默认归属表切全国表 + variable_prefixes（D-28/D-29） | 已完成 | 用户核对通过；风电 5/5、光伏 4/4 自测 PASS；DESIGN 该节转〔已确认〕 |
| P5 | 文档重构：DESIGN 两部分结构 + 决策迁移 DECISIONS（D-30~D-39）+ 新建 API.md + 原始资料入 docs/ref/ | 已完成 | 本轮汇报 |
| P6.1 | 抽取 `biz/`：request/assignment/runner，输出表组装移入；算法包暂不动 | 已完成 | 风电 5/5 PASS（全省回归 311s）、光伏 4/4 PASS；run.py 与路由导入冒烟通过 |
| P6.2 | 算法包去 wind/solar 子包按阶段平铺 + 模型类 forward（D-42~D-46）+ 参数集中 config/constants（D-48） | 已完成 | 基准口径下场景总量 1e-11；旧码/新码默认口径同挂 3.782e-03（光伏）、8.777e-02（风电）→ 非重构回归（D-47 口径差异）；光伏自测 4/4 PASS（出力总量 1e-3，D-47）；风电 2~5 PASS、1 为 Q-05 基准问题；参数集中前后偏差逐字不变 |
| P6.3 | 回归：`.refactor_baseline/` 风电 5/5、光伏 4/4 结果比对 + 自测 | 已完成 | 风电 5/5 PASS（单市轻量版 39s，基准按 D-49 当前口径重生成冻结，总量 1e-6/逐元素 1e-4）；光伏 4/4 PASS（基准口径传参 D-47，出力总量 1e-3） |
| P6.4 | 文档同步：DESIGN「模块对应」「对象分类表」对齐目标结构、流程图重跑 | 已完成 | DESIGN 全文按现状重写（2026-10-30）：模块对应改 api/biz/new_energy 三层实况表、工程实现按对象重组（请求匹配/归属取数/风电模型 wind.py/光伏模型/物理出力/输出组装）、单元匹配更新 D-53、归属仅全国表 D-54、变量表与输出文件名补位置；算法流程未变，流程图 PNG 不需重跑 |
| P6.5 | 校验随配置构造走（D-50）：机组配置类进算法层 + request 瘦身 + Runner 类 | 已完成 | request.py 419→346 行（JSON 构造器在内）、config.py 49→128 行；风 5/5 + 光 4/4 自测 PASS（基准回归逐字一致）；run.py/路由/Runner 导入冒烟通过 |
| P7.1 | biz/assignment 重构为 Assignment 类（仅全国表，D-54）+ 单元测试 | 已完成 | 用户重写实现（向量化键构造/单次分组）；调用方已切类接口；tests/biz/test_assignment 3 属性 PASS（真实全国表 426 单元）；风自测 5/5 PASS；光伏 case 1 待基准重生成 |
| P7.2 | biz/years 单元测试（真实数据）+ YearsPlan 类重构 | 已完成 | 用户主导逐步重写（步骤编号/_scan/_longest_run）；tests/biz/test_years 3 属性 PASS（2000-2025） |
| P7.3 | biz/export 单元测试 + 删 `__main__` | 已完成 | tests/biz/test_export 3/3 PASS（手工造 WindResult + fixture，不走算法）；光伏自测 2~4 PASS 确认无影响 |
| P7.4 | biz/reader 单元测试 + `_wind` 温压换算平铺 + 删 `__main__` | 已完成 | tests/biz/test_reader 3/3 PASS（真实数据 26 年）；风自测 5/5 PASS |
| P7.5 | biz/grids 重构 GridData 类（全模块进类，region_center→center）+ 单元测试 | 已完成 | 用户重写结构采纳（单循环就地聚合/内联/len!=1）+ 四修正（docstring/is not None/hint/删过程注释）；tests/biz/test_grids 3/3、风 5/5、光 2~4 PASS；reader 参数墙删除，归属表 3 遍→1 遍 |
| P7.6 | biz/schema 单元测试 + 注释对齐 D-54（426 单元/仅全国表）+ 删 `__main__` | 已完成 | tests/biz/test_schema 3/3 PASS（构造/默认机组 18 单元/非法输入 4 例报错） |
| P7.7 | biz/runner 单元测试（request 的 D-29 四情形 + 光伏经纬度补全；result/forward 由风光自测覆盖） | 已完成 | tests/biz/test_runner 4/4 PASS（只调 request 不跑算法，秒级） |
| P7.8 | new_energy/history 删除（一行转调薄包装）：pipeline 阶段首项换 lambda 直接调 beijing_aligned + 删重复 import | 已完成 | tests/new_energy/test_time_axis 3/3 PASS；风自测 5/5、光伏 2~4 PASS（case 1 已知待基准重生成）；README.md:111 残留旧类名待 README 精简一并处理 |
| P7.9 | new_energy/classify 删除：分类逻辑折入 pipeline（`_classify_years` 普通函数 + `_wind_metric`/`_solar_metric` 具名函数），阶段元组 4 项→3 项，模型属性同步 | 已完成 | import 冒烟通过；风自测 5/5、光伏 2~4 PASS（case 1 已知）；分类逻辑待 P7 pipeline 测试覆盖 |
| P7.10 | 重构前冻结基准：光伏基准按生产口径（全国表+显式经纬度）重生成 + md5 更新，自测基准口径切换（D-47 旧口径作废）；风基准不变 | 已完成 | 光自测 4/4 PASS（同码同口径，偏差 0）；风自测 5/5 PASS（本会话）；重写回归以本套基准为准 |
| P7.11 | new_energy/wind.py 从规格重写（垂直切片）：五步普通函数、(年数, 8760) 装箱 + MONTH_STARTS 查表、常数具名、行为类全废 | 已完成 | 与旧 WindPowerModel 同输入对算：单市、两市（进程池）全量逐位一致 0.0；逐级二分（特征/事件/标准/φ/基础场景/校准）全 0；evaluate 路径未覆盖（fixture 未开）；后续精化：校准链收为 Generator 私有方法（_quantile_map/_calibrate_levels/_calibrate_quantiles/_insert_events）+ 死守卫删除/掩码直取参考矩阵/权重单算，合成数据对算逐位一致（3 场景/117 行方案/φ 全同）；再合入用户版结构：打分/写入抽为 _score_candidate/_apply_event + zip 月遍历 + inf 初始化最优 + g_mean 单算，对算仍逐位一致；按 write-python-code 规则补全私有方法 Args/Returns docstring + 步骤编号注释 + train_mask 同表达式合并，对算仍逐位一致；事件库改长表：EventLibrary 类废为模块级函数 event_details 返回 (E,5) 明细（列=年行号/类型/起点/时长/月份，风速值不复制），_insert_events 三处消费改现取（布尔筛行/行数÷年数/train_grid 切片），合成数据对算逐位一致（3 场景/358 事件/φ 全同）+ 风自测 5/5 PASS；再改明细首列年行号→年份（行号消费端 searchsorted 现取，event_cases 循环外单算），对算仍逐位一致 + 风自测 5/5 PASS；再整理为 EventDetails 类（(E,5) 明细 + pool/n_case_years/segment 现取方法，cases 构造时一次算好；_insert_events 7 参数→4），风自测 5/5 PASS（冻结基准逐字一致）；BaseScene 步骤8 拆为 8 相加/9 截负两步（表达式不变），风自测 5/5 PASS；BaseScene 与 Generator 拆平级：Generator 不再组装 base（签名加 base 参数、不返回 φ），WindModel 组装四个平级子模块、forward 先 4a base 后 4b generate，风自测 5/5 PASS（冻结基准逐字一致）；Generator 返回改裸 ndarray (3, 8760)（行序同 RESOURCE_YEAR_TYPES），装箱 {case: Series} 挪至 forward 一行，风自测 5/5 PASS；评估封装 Evaluate 类（__init__ 存开关/train_years，__call__ 转调 evaluate_city），forward 步骤6 一行调用，风自测 5/5 PASS（evaluate 路径仍无覆盖）；Physics 拆单城市（场景风速+输出年温压+机组配置 → {case: MW}），区域汇总（逐城市出力累加+容量加权风速，城市累加顺序冻结）移 run_wind，中途结构化补丁锚点撞名删错段已修复（forward/run_wind 头自会话记录复原），修复后与旧 WindPowerModel 合成数据（12 年×2 市）对算逐位一致 0.0（scenarios/classes/power 全字段），风自测 5/5 PASS；physics 单元计算移入 forward（签名加 temp/pressure/unit、步骤7 重排+步骤8 物理出力、返回 7 元组含逐单元出力），run_wind 只剩并行分发+区域汇总，临时接线 biz 跑风自测 5/5 PASS（接线已还原）；Generator 精化（用户确认 2026-11-01）：_calibrate_quantiles 删方法内联 __call__ 步骤3、选点循环抽 _pick_position、删 pool 空死守卫（count 恒 0 空转，rng 流不变）、plans 全链改 list[dict]（Evaluate 内现转 DataFrame，plan_by_case 新旧构造合成对拍等价）、注释/维度标注润色；临时接线 biz 风自测 5/5 PASS（冻结基准逐字一致）；MONTH_STARTS/month_of/装箱 year_grid 上移 utils/time_axis（风/光共用，风自测 5/5 PASS 不变）；evaluate 路径仍无 fixture 覆盖 |
| P7.12 | new_energy/solar.py 从规格重写（垂直切片，镜像 wind.py）+ 接线 biz + 删旧类 | 已完成 | 与旧 SolarPowerModel 真实数据对算：三 case 场景/逐市出力/区域 MW/pu 全部 max|d|≤6.1e-12（浮点末位级），分类逐位一致；biz 接线 run_solar/run_wind；光自测 4/4 PASS（冻结基准）、风自测 5/5 PASS；删 pipeline.py/events.py/generate.py，power.py 瘦身为 PowerResult（weather_climatology/CLIMATOLOGY_INDEX 移入 solar.py），tests/biz/test_export 改 import new_energy.wind；DESIGN（模块对应/对象分类表/光伏模型节/物理出力节/维度流转 stage 1·6）与 README 模块引用同步；test_export 3/3、test_runner 4/4、test_time_axis 3/3 PASS。行为差异记录：事件插入方案行旧实现产出即弃、不再产出；单市不再走进程池捷径（joblib 统一，风同） |
| P8 | 三层代码分节注释（model.py 三线 ── 风格，25 处）+ 流程图按「维度流转」重做 | 已完成 | 横线行全库与 model.py 逐字一致（77 个 ─）；import 冒烟（wind/solar/biz.runner/api.server.main_server）+ test_export 3/3 PASS；`docs/维度流转图.png` 替代数据流转网络图.png（主链一条 = 维度总流转式，框上标输出维度，温压/温度虚线弧直通⑥），DESIGN「流程图」节同步 |
| P9 | 归属表增海陆判定列（D-55；DECISIONS 补录 D-54）：一次性追加 + 生成器透传（含 lineterminator 钉 LF） | 已完成 | 追加结果与 HEAD 逐行比对仅末列差异（海 85/陆 15355）；生成器重跑（本地边界缓存）产物与追加结果字节级一致；风自测 5/5、test_assignment 3/3 PASS；Assignment 不消费新列 |
| P10.1 | Assignment 存储翻转：原始表 DataFrame 存一份 + coords/land_coords/sea_coords/centers 方法（D-56），grids/runner 调用行同步 + 单元测试 | 已完成 | test_assignment 4/4、test_grids 3/3、test_runner 4/4、test_export 3/3；风自测 5/5、光自测 4/4 PASS（solar_selftest 一处 centers[CITY] 改方法调用）；行为不变（纯存储翻转） |
| P10.2 | GridData 海陆拆分能力（sea_split 开关默认关：城市只聚陆格点 + 区域海组 SEA_UNIT 一条、纯海单元告警跳过；reader 切换挪 P10.4，因 "sea" 键会 KeyError city_configs）+ 单元测试 | 已完成 | test_grids 4/4（新增 test_sea_split：海组产出/全陆城市不变/含海城市变/纯海单元属性断言，三沙海点列不在海南数据故不跑 forward）、test_assignment 4/4；风自测 5/5 PASS（默认路径行为不变） |
| P10.3 | request/schema 解析平级键 sea_configs（D-57：单对象 region/capacity_mw/turbines；有海缺配/无海却配报错；两者都缺省才填默认海风 default_sea_config_wind.json）+ Runner 海风配置核对 + 单元测试 | 已完成 | test_runner 5/5（新增 test_sea_configs 六断言）、test_schema 3/3；风自测 5/5（默认请求填默认海风后用例 3 通过）、光自测 4/4 PASS（schema 共用层无影响）；模型侧未消费 sea_configs（P10.4 接线） |
| P10.4 | Runner：reader._wind 切 sea_split=True + 海风独立任务键 sea 进并行池（city_configs 补 sea 键，温压同组），省汇总含海；export 总装机含海 + 文件名 onshore_wind_power→wind_power（D-58，算法包零改动——run_wind 按机组表通用汇总） | 已完成 | test_export 3/3、test_runner 5/5、test_grids 4/4；风自测 5/5（海口纯陆路径冻结基准逐字一致）；一次性儋州+海全流程：sea_power_mw 末位列/cities 含 sea/总装机 57.15=37.15+20/省出力=陆+海逐元素一致/海列界内（年总量 58503 MWh，容量因子 33%）；光自测 4/4 PASS（export 共用层无影响） |
| P10.5 | 文档同步（D-57/D-58/D-59 落 API.md；DESIGN 已随各步同步） | 已完成 | API.md 七处：标题海陆风电、风电节海陆拆分描述、传参加 sea_configs（含默认/报错规则）、返回改 wind_power_ 文件名 + cities/总装机/sea_power_mw 列注释、光伏节 DNI/容配比描述、传参加 dc_ac_ratio、返回加 DNI 列；列名/文件名/默认值逐项对 export.py、config.py 核对 |
| P10.6 | 结构查询接口（省名 → 前端要填的配置结构，D-61）：Assignment 增 units_of_province + biz/structure.py + wind/solar 路由各加 GET /structure + API.md 登记 | 已完成 | tests/biz/test_structure 3/3 PASS（海口 4陆0海/儋州 4陆1海/光伏全单元无 sea 键）；实调海南：风电 18 城市 + sea_configs{needed,grid_count 6} + 纯海名单[三沙]、光伏 19 单元；坏省名报错 400 口径；路由注册与 main_server 装配冒烟通过；青海省湖点计海（6 点）与 D-55/runner 一致 |
| P10.7 | 风自测海风用例 + 冻结海风基准 + 光伏回归（陆风基准不重生成——P10.4 已验证现基准与当前代码逐字一致，重生成无信息量） | 已完成 | wind_sea 用例 6 PASS（40.8s：cities 含 sea/末列 sea_power_mw/总装机 57.15/省=陆+海逐元素/海列界内/对基准 1e-6·1e-4）；新基准 BASE_wind_sea_正常资源年.csv 冻结（8784×6，sea 年总量 58503.1 MWh 与 P10.4 一次性验证一致）；风自测 6/6 PASS；solar_danzhou.json 恢复统一 1.2（用户试验值 1.8 撤销）后光伏 4/4 PASS |
| P11 | 光伏算法同步（同事 solar_update/ 四文件，D-59）：POA 分解式 DNI=max(GHI−DHI,0)/cos z、直流侧缩放 capacity_mw×dc_ac_ratio、交流封顶 [0,capacity_mw]、DNI 输出列 + 请求可选字段 dc_ac_ratio（默认 1.2）；光伏基准重生成 | 已完成 | config/result 按同事版抄、solar.py 整文件替换（同源基底）+补 Physics/_poa Returns docstring、export 仅合 DNI 两段（保 P10.4 风电改动）；基准重生成：场景 3 文件逐字节不变（证实仅物理出力层变）、power 3 文件更新+md5；光自测 4/4（新基准偏差 0）、test_export 3/3、风自测 5/5 PASS；DNI 列暂无基准回归覆盖（可扩展）；solar_update/ 目录保留未删 |
| P12 | dc_ac_ratio 改组件级（D-60，Q-04 终结；同事 new_update/ 核对=与 P11 已同步内容同批，无需再同步）：字段 SolarUnit→SolarComponent、solar.py 缩放改组件循环内（scale=capacity_mw×该组件容配比÷全部组件额定合计）、API.md/DESIGN 四处同步 | 已完成 | 光自测 4/4（默认 1.2 逐组件与单元级逐位一致，冻结基准不变）；test_schema 3/3、test_runner PASS；层级检查（组件级可传/0 报错/单元级旧位置 TypeError）；一次性 1.2/1.3 运行 53.8s 成功，年总量 +488 MWh（+0.024%），交流封顶 955.21 生效 |

## 待办
（无）
