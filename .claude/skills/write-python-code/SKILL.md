---
name: write-python-code
description: 新建 .py 文件、写或改函数和类时使用，定义文件头、函数还是类的选择、docstring、类型注解、数据类字段注释。当用户说新建 evaluate.py、建个工具脚本、文件头格式补上、这个函数拆不拆、补类型注解、两个函数签名统一一下、建一个机型参数数据类、这个类太大拆一下、字段注释格式怎么写时使用。
---
# 写 Python 代码

## 共同规则

- 类型注解必写
- 一个函数或类只做一件事；取数、计算、保存文件不放在一起
- 全项目同一职责只有一个出口（如只有一处调底层库），其他地方复用，不各自实现

## 紧凑写法（用户确认 2026-10-28）

- 校验内联在使用处，不建一次性包装函数（fail/string/check_* 之类）；同一检查 3 处以上重复才提函数
- 分支少直接 if/else 写在使用处，不做表驱动配置（先建 KIND_OPTIONS 这类表再查表取差异项的写法）
- 校验下沉到数据类 `__post_init__`（构造即校验）；中间层只透传原始值，不重复查、不提前强转
- 取值一行：`payload.get(key, default)` 填默认值；`x = a if cond else b` 三元
- 简单守卫单行：`if not ok: raise ValueError("...")`

```python
payload = source if isinstance(source, dict) else json.loads(Path(source).read_text("utf-8"))
if not isinstance(payload, dict): raise ValueError("配置文件根必须是 JSON 对象")
```

## 按任务读

| 任务 | 读 |
| :--- | :--- |
| 新建 .py 文件 | reference/header.md |
| 写或改函数、决定用函数还是类 | reference/function.md |
| 写或改类、数据类 | reference/class.md |
