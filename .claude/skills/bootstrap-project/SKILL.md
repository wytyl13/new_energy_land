---
name: bootstrap-project
description: 从零新建项目或仓库时使用，定义开工要建的文档、目录结构、环境和 git 初始化。当用户说从零建个新仓库、新项目目录怎么搭、初始化环境和 git 时使用。已有项目里加模块用 design-module-layering。
---
# 新建项目

## 1. 先设计后建目录

- 先给出：项目类型（纯算法 / 算法 + 服务 / …）、分层图、模块列表，用户确认后再建目录
- 目录按 `design-module-layering` 分层，从第一天就单向依赖

## 2. 开工先建的文档

- CLAUDE.md、DESIGN.md、PLAN.md、DECISIONS.md、README.md；有服务层再建 API.md
- 写法见 `maintain-project-docs`

## 3. 目录最小集

- 目录、包、模块、文件名一律用英文；交流和文档用中文

```
<project>/
  CLAUDE.md  DESIGN.md  PLAN.md  DECISIONS.md  README.md
  requirements.txt 或 environment.yml
  src/ 或 <包名>/    按分层组织
  data/              输入数据（不进 git 的写进 .gitignore，并在 README 说明）
  docs/ref/          原始资料，只读
  tests/             最小可运行检查
```

## 4. 环境

- 第一天锁定依赖版本
- conda 环境名和项目对应，只写在 README「Quick Start」

## 5. 第一条可运行路径

- 完成标准：一条命令跑通"读一份最小输入 → 出一份最小输出"，之后再按模块充实（读取校验见 `handle-input-data`）

## 6. git

- 首次提交只含目录结构和首批代码；数据、密钥、临时文件进 .gitignore
- 一步一提交，提交信息说清做了什么
