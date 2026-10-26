# 异常与报错

## 1. 默认做法（先用这一档）

- service 层和算法层不吞错：不 catch 后返回 None
- 全局 handler：`ValueError`（参数或数据错）→ 400；其他未捕获异常 → 500，body 为 `{"success": False, "message": "Internal Server Error: 原因", "data": None}`
- 失败一律经 `make_response(success=False, message=...)` 返回

## 2. 出现"不存在""状态冲突"时再升级

- 建业务异常类：基类 `BizError` 带 `status_code` 和 `code`（业务子码），按需加子类，如 `NotFoundError`（404）、`ConflictError`（409）、`ParamError`（400）
- manager 每个方法 try/except，加上下文后按原类型重抛，不统一转成 `RuntimeError`
- handler 按异常类型映射状态码，body 带 `code`
- HTTP 状态码表示哪类错误，body 的 `code` 表示具体业务子码；不用"一律 200、错误只看 body"

## 3. 报错信息

- 中文、具体、能定位：指明对象和冲突（如"未找到批次 xxx 下类型为 xx 的任务"）；参数错误指明字段名
- manager 包装格式：`[模块名]-{对象id}- 操作描述: {原因}`
- 可预期的错误用 `logger.warning`；其他用 `logger.error`，带 `traceback.format_exc()`
- `message` 给用户直接看；堆栈只进日志，不进 `message`
