---
name: build-fastapi-service
description: 搭建或修改 FastAPI 服务层时使用，定义目录分层、算法包接入、接口和参数、返回值、异常映射、数据库访问、前后端 JSON 格式。当用户说给 pipeline 包一层 HTTP、加一个 POST 接口、异常怎么映射状态码、加个配置下发接口格式、categories 改数组吧、曲线数据怎么组装返回时使用。
---
# 搭建 FastAPI 服务

核心算法内部怎么写不归这里。本项目已定的接口格式在项目根目录 API.md。

## 1. 目录与分层

`api/` 与 `core/` 是服务层的固定目录结构；核心算法独立成包，位置不限，不放进 core：

```
api/
  server/xxx_server.py   FastAPI app：lifespan、中间件、路由注册、健康检查、全局异常
  routers/xxx_router.py  APIRouter(prefix="/api/xxx", tags=["Xxx"])，路由只做转发
  schema/xxx_schema.py   HTTP 请求体模型
  dependencies.py        依赖工厂：manager、service、重资源单例
core/
  manager/xxx_manager.py 业务组织：组合多个 service 和算法入口、后台长任务、聚合统计
  service/xxx_service.py 单表增删改查（不含跨表逻辑）
  table/xxx.py           表模型、内嵌 JSONB 结构、状态枚举
  provider/sql_provider.py 通用增删改查
  schema/xxx_dto.py      Create/Update/Filter/Response DTO
<算法包>/                核心算法，独立包，不依赖 api 和 core
```

- 依赖单向：router → manager → service → provider → table；router 或 manager → 算法包唯一入口
- 算法包不反向 import 服务层；router 不直接访问数据库；跨表、跨服务、调算法的组合放 manager，service 只管单表

## 2. 算法包接入

1. 算法包不感知服务层：不 import fastapi、不接收 Request、返回普通 dict
2. 服务层只调算法包唯一入口（同步函数：请求 dict 进，结果 dict 出），不 import 算法内部模块；算法内部重构不影响服务层
3. 参数校验和默认值填充在算法入口内一次完成；服务层不重复校验、不拼算法内部参数，能透传就直接透传
4. 算法出错抛异常并带上下文，不吞错、不用返回码表示失败；用异常类型区分类别（映射见 reference/errors.md）
5. async 路由里用 `await asyncio.to_thread(入口, 请求)` 调同步算法；只调一个算法、没有组合需要时不建 manager，路由直接调

## 按任务读

| 任务 | 读 |
| :--- | :--- |
| 加或改接口、参数、返回值、schema、跨域 | reference/api.md |
| 异常映射、报错信息 | reference/errors.md |
| 接数据库、写 manager 和 service | reference/database.md |
| 定前后端 JSON 格式 | reference/json-contract.md |
