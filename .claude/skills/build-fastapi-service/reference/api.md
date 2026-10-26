# 接口、参数、返回值

## 1. Schema（三处分工，不混放）

1. `api/schema/*_schema.py`：HTTP 请求体，`BaseModel` + `Field(..., description="中文说明")`
2. `core/schema/*_dto.py`：数据层 DTO 四种
   - Create：必填字段 + `Optional` 带默认值
   - Update：全部 `Optional`
   - Filter：查询条件字段（模糊查询后缀 `_like`）
   - Response：聚合统计等强类型返回结构（嵌套 Item 模型）
3. `core/table/xxx.py`：表模型内嵌 JSONB 字段的结果结构（Pydantic）；状态用 `@unique class Xxx(IntEnum)`，编码按十位递增（10/20/30）

## 2. 路由与 app

1. 路由函数一律 `async def`；CPU 密集用 `asyncio.to_thread()`，长任务用 `BackgroundTasks`
2. 请求体一律用 Pydantic 模型，不收裸 dict
3. 依赖注入统一 `Depends(工厂函数)`，工厂集中在 `dependencies.py`；重资源（模型、连接池）进程内单例，不随请求重建
4. app 固定四项：`lifespan`（启停与清理）、跨域、请求日志中间件、`/api/health`（返回 `{status, message, timestamp}`）
5. 跨域 `CORSMiddleware(allow_methods=["*"], allow_headers=["*"], ...)`：
   - 不用 cookie、不登录：`allow_origins=["*"]`，`allow_credentials=False`
   - 要带凭据：显式列出 origins，`allow_credentials=True`
   - `"*"` 和 `True` 不同时用
6. 状态轮询接口只返回 `{status, progress, detailed_status, error_message}`，不返回全量 `result_data`
7. 启动脚本：`argparse --port`（默认值按项目约定）+ `uvicorn.run(...)`

## 3. 请求方法

1. 没有参数的纯读取用 GET（健康检查、可用列表）
2. 带参数的操作一律 POST，查询也一样（分页、筛选、状态轮询）：筛选条件放 body，不受 URL 长度限制，直接对应 Pydantic 模型
3. 不用 PUT/PATCH/DELETE：改、删统一 POST `/update`、`/delete`；路径用动词命名（`/create` `/page` `/execute/xxx`），小写，多个词用连字符
4. 实时双向通信用 WebSocket（`@router.websocket`）

## 4. 参数（三类，各走各的通道）

1. 请求体：每个路由一个 Pydantic 模型（命名 `XxxReq`），字段写 `Field(..., description="中文说明")`；id 等标识放 body，不用路径参数
2. 查询参数：只放轻量可选项，`Query(默认值, description=...)`
3. 文件上传：`UploadFile`，批量上传收 `List[UploadFile]`

- 依赖对象一律 `Depends(工厂)` 注入，不手动实例化
- 每个路由函数写中文 docstring：`【接口标题】一行说明`

## 5. 返回值

所有接口统一经 `make_response` 包装：

```
{"success": bool, "message": str, "data": Any, "timestamp": ISO时间}
```

- 成功：`success=True`，结果放 `data`（经 `jsonable_encoder`）
- 失败：`success=False`，原因放 `message`
- 不裸返回 dict 或 list，不在路由里手拼响应结构
