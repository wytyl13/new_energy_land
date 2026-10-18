---
name: service-api
description: Service 编写规范。HTTP 服务层（FastAPI）的目录分层、schema 分工、算法包接入、接口与参数、返回值、异常处理、报错信息、manager/service 数据库访问。搭建或修改 FastAPI 服务层时读。
---

# Service 编写规范

边界：只管 HTTP 服务层（FastAPI）怎么写：目录分层、schema、接口、返回值、异常、
报错、manager/service 编排、算法包接入、数据库访问。核心算法内部规则归算法各自 skill。

## 1. 目录与分层

`api/` 与 `core/` 为服务层固定骨架；核心算法独立成包，位置不限、不进 core：

```
api/
  server/xxx_server.py   FastAPI app：lifespan、中间件、路由注册、健康检查、全局异常
  routers/xxx_router.py  APIRouter(prefix="/api/xxx", tags=["Xxx"])，薄路由
  schema/xxx_schema.py   HTTP 请求体模型
  dependencies.py        依赖工厂：manager/service/重资源单例
core/
  manager/xxx_manager.py 业务编排：组合多 service 与算法入口、后台长任务、聚合统计
  service/xxx_service.py 单表 CRUD（不含跨表逻辑）
  table/xxx.py           表模型 + 内嵌 JSONB 结构 + 状态枚举
  provider/sql_provider.py 通用 CRUD 提供者
  schema/xxx_dto.py      Create/Update/Filter/Response DTO
<算法包>/                核心算法，独立包，不依赖 api/core
```

依赖单向：router → manager → service → provider → table；router/manager → 算法包
唯一入口。算法包不反向 import 服务层；router 不直接碰 DB；
跨表/跨服务/算法编排放 manager，service 保持单表。

## 2. Schema 规范（三处分工，不混放）

1. `api/schema/*_schema.py` — HTTP 请求体：`BaseModel` + `Field(..., description="中文说明")`。
2. `core/schema/*_dto.py` — 数据层 DTO 四件：
   - Create：必填字段 + `Optional` 带默认值；
   - Update：全 `Optional`；
   - Filter：查询条件字段（模糊查询后缀 `_like`）；
   - Response：聚合统计等强类型返回结构（嵌套 Item 模型）。
3. `core/table/xxx.py` — 表模型内嵌：JSONB 字段的结果结构（Pydantic），
   状态机用 `@unique class Xxx(IntEnum)`，编码十位递增（10/20/30）。

## 3. 算法包接入规范

1. 核心算法独立成包，不感知服务层：不 import fastapi、不接收 Request 对象、
   返回纯 dict。
2. 服务层只调算法包**唯一入口**（同步函数：请求 dict 进 → 结果 dict 出），
   禁止 import 算法内部模块；算法内部重构不影响服务层。
3. 参数校验与默认值填充在算法入口内一次完成；服务层不重复校验、
   不拼算法内部参数，透传请求体即可的接口直接透传。
4. 算法错误一律抛异常带上下文上抛（不吞、不用返回码表示失败）；
   用异常类型区分错误类别（如参数/数据错误 `ValueError`），服务层按第 7 节映射。
5. 同步算法在 async 路由中用 `await asyncio.to_thread(入口, 请求)` 调用，
   不卡事件循环；单算法直调、无编排需要时不建 manager，路由直接调。

## 4. FastAPI 接口规范

1. 路由函数一律 `async def`；CPU 密集 → `asyncio.to_thread()`，
   长任务 → `BackgroundTasks`。
2. 请求体一律 Pydantic 模型（见第 2、5 节），不收裸 dict。
3. 依赖注入统一 `Depends(工厂函数)`，工厂集中在 `dependencies.py`；
   重资源（模型、连接池）进程内单例，不随请求重建。
4. app 装配固定四件：`lifespan`（启停与清理）、跨域、请求日志中间件、
   `/api/health`（返回 `{status, message, timestamp}`）。
   跨域策略：`CORSMiddleware(allow_origins=["*"], allow_methods=["*"],
   allow_credentials=True, allow_headers=["*"])`，前后端分离部署时按需收紧 origins。
5. 状态轮询接口只返回 `{status, progress, detailed_status, error_message}`，
   不返回全量 `result_data`。
6. 启动脚本：`argparse --port`（默认值按项目约定）+ `uvicorn.run(...)`。

## 5. 请求方法与参数规范

方法选择：

1. 无参数的纯读取 → GET（健康检查、可用列表类）。
2. 带参数的操作一律 POST，查询也不例外（分页/筛选/状态轮询）：
   复杂筛选条件放 body，不受 URL 长度限制，且直接对齐 Pydantic 模型。
3. 不用 PUT/PATCH/DELETE：改、删统一 POST `/update`、`/delete`；
   路径动词式命名（`/create` `/page` `/execute/xxx`），小写，多词用连字符。
4. 实时双向通信用 WebSocket（`@router.websocket`）。

参数三类，各走各的通道：

1. 请求体：每路由一个 Pydantic 模型（命名 `XxxReq`），字段
   `Field(..., description="中文说明")`；标识符（id 等）放 body，不用路径参数。
2. 查询参数：仅轻量可选项，`Query(默认值, description=...)`。
3. 文件上传：`UploadFile`，批量上传收 `List[UploadFile]`。
- 依赖对象一律 `Depends(工厂)` 注入，不手动实例化。
- 每个路由函数配中文 docstring：`【接口标题】一行说明`。

## 6. 返回值规范

所有接口统一经 `make_response` 包装返回：

```
{"success": bool, "message": str, "data": Any, "timestamp": ISO时间}
```

- 成功：`success=True`，结果放 `data`（经 `jsonable_encoder`）；
- 失败：`success=False`，原因放 `message`；
- 不裸返回 dict/list，不在路由里手拼响应结构。

## 7. 异常处理规范

状态码语义化：HTTP 状态码管"哪类错误"，body `code` 管具体业务子码；
不用"一律 200、错误只看 body"的写法。

两层兜底 + manager 包装：

1. manager 层每方法 try/except 全包并按类型包装上抛：
   - `ValueError`（不存在/参数错）→ `logger.warning` + 带 `[模块]-{id}-` 前缀重抛 `ValueError`；
   - 其它异常 → `logger.error`（含 `traceback.format_exc()`）+ `raise RuntimeError(错误消息)`。
2. 路由层/异常 handler 捕获：可预期业务错误 → 语义化状态码
   （参数/数据错 400、不存在 404、状态冲突 409）+
   `make_response(success=False, message=..., code=业务子码)`；
   算法抛的参数/数据类异常按类型同样映射。
3. 全局 exception handler：未捕获异常 → 500 +
   `{"success": False, "message": "Internal Server Error: 原因", "data": None}`。
- service/算法层不吞错：禁止 catch 后返回 None。

## 8. 报错信息规范

- 中文、具体、可定位：指明对象与冲突（如"未找到批次 xxx 下类型为 xx 的任务"），
  参数错误指明字段名。
- manager 包装格式：`[模块名]-{对象id}- 操作描述: {原因}`。
- `message` 面向用户可直接展示；堆栈只进日志，不进 message。

## 9. Manager/Service 与数据库规范（有数据库的项目适用）

- 全异步：`create_async_engine` + `AsyncSession`（asyncpg）。
- 表模型：SQLAlchemy 2.0 声明式，统一 Base。
- service 三件套：`self.logger = logging.getLogger(类名)`、`self.model = 表模型`、
  `self.sql_provider = SqlProvider(model=..., sql_config=...)`。
- service CRUD 固定写法：create 用 `model_dump(exclude_none=True)`；update 用
  `exclude_unset=True)`；单查按条件 dict；列表分页 `page/page_size` +
  `order_by create_time desc()`。
- manager 构造注入所需 service（及其它 manager），`self.logger` 同款；
  职责四类：跨 service 流程编排、SQL 端聚合统计（拒绝全量明细进内存）、
  后台长任务（`run_xxx_background`）、存储路径转预签名 URL。
