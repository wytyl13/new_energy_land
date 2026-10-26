# 文件头

新建 .py 文件从第一行起按此模板写：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/19 11:05
@Author  : weiyutao
@File    : example.py
"""

```

- 文件头后空一行，再写 import 或代码
- 模块说明写进同一个三引号块（`@File` 行后空一行接正文），不另开第二个 docstring；没有说明就只有三行元信息
- `@Time`：文件创建时刻，格式 `年/月/日 时:分`，用下面的命令取，不估算：
  `python -c "import datetime; print(datetime.datetime.now().strftime('%Y/%m/%d %H:%M'))"`
- `@Author`：固定 `weiyutao`，不随执行者变
- `@File`：文件名，不带路径
- 标签补齐到 9 个字符再接 `: `（`@Time`、`@File` 后补 4 个空格，`@Author` 后补 2 个），三行冒号对齐
- 只对新建文件生效，不回补旧文件；用户要求批量补齐时一次补完
