---
name: code-header
description: Python 文件头规则。新建 .py 文件的 shebang、编码声明、@Time/@Author/@File 元信息块格式与字段填写。新建任何 .py 文件前读。
---
# Python 文件头规则

边界：只管新建 `.py` 文件的文件头。文件内代码组织归 `code-function`/`code-class`，
注释与 docstring 用词归 `style-wording`。

## 1. 文件头格式（红线）

每个新建 `.py` 文件，第一行起按此模板写：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/19 11:05
@Author  : weiyutao
@File    : example.py
"""

```

- 文件头结束空一行，再接 import 或代码。
- 模块职责说明写进**同一个**三引号块（`@File` 行之后空一行接正文），
  不另开第二个 docstring；无说明则只有三行元信息。

## 2. 字段填写

- `@Time`：文件创建时刻，格式 `年/月/日 时:分`（如 `2026/10/19 11:05`）。
  创建文件时用 `date "+%Y/%m/%d %H:%M"` 取当前时间，不估算。
- `@Author`：固定 `weiyutao`，不随谁执行变。
- `@File`：文件名，不带路径。

## 3. 对齐与旧文件

- 标签补齐到 9 字符宽再接 `: `（`@Time`/`@File` 后补 4 空格，
  `@Author` 后补 2 空格），三行冒号对齐。
- 默认只对新建文件生效，不主动回补旧文件（避免无信息量的大改动）；
  用户明示要求批量同步时一次性补齐，并在 DESIGN.md §7 留痕。
