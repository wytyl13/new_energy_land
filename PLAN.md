# 计划与进度

## 当前状态
- 正在做：P7 暂停——用户将从头重写 new_energy 算法包（目标结构：行为类全删改普通函数 run_wind/run_solar + 风光共用数学函数，之后 numpy 化）
- 挂起：P6.4 文档同步（DESIGN 模块对应节待重写目标结构后一并做）→ 提交 P6.2~P6.5
- 下一步：重写后用 风自测 5/5 + 光自测 4/4 + `.refactor_baseline/` 做回归
- 阻塞：无
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
| P6.4 | 文档同步：DESIGN「模块对应」「对象分类表」对齐目标结构、流程图重跑 | 未开始 | — |
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

## 待办
- [ ] README.md 精简为只写怎么用；接口说明指向 API.md
- [ ] `api/server/main_server.py` 跨域改为 `allow_credentials=False`（当前 `"*"` 和 `True` 同时用）
