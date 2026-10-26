# 数据库、manager 与 service（有数据库的项目适用）

- 全异步：`create_async_engine` + `AsyncSession`（asyncpg）
- 表模型：SQLAlchemy 2.0 声明式，统一 Base
- service 固定三个属性：`self.logger = logging.getLogger(类名)`、`self.model = 表模型`、`self.sql_provider = SqlProvider(model=..., sql_config=...)`
- service 增删改查固定写法：
  - create 用 `model_dump(exclude_none=True)`
  - update 用 `model_dump(exclude_unset=True)`
  - 单条查询按条件 dict
  - 列表分页 `page/page_size` + `order_by(create_time.desc())`
- manager 在构造函数注入所需 service（及其他 manager），`self.logger` 同上
- manager 只做四类事：跨 service 的流程组织；在 SQL 端做聚合统计（不把全量明细读进内存）；后台长任务（`run_xxx_background`）；存储路径转预签名 URL
