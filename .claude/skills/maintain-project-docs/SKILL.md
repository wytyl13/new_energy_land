---
name: maintain-project-docs
description: 新建或修改 DESIGN.md、PLAN.md、DECISIONS.md、API.md、README.md，画算法流程图、写维度流转，或代码改动后同步文档时使用。当用户说建个 DESIGN.md、画个算法流程图、维度流转怎么写、DESIGN 章节怎么排、重构设计写哪、重写 README、算法全流程怎么写、收尾把 README 定稿、接口加字段文档同步了吗、README 跟着改吗、哪些文档要更新、进度能直接更新吗时使用。判断一条规则该放哪用 manage-skills。
---
# 维护项目文档

各文件装什么见 CLAUDE.md「文档分工」；修改权限见 CLAUDE.md「改动确认」「固定不变的对外约定」。

## 共同规则

- 同一事实只写一处：进度只在 PLAN，决策只在 DECISIONS，接口格式只在 API.md，项目历史 = git 提交 + DECISIONS
- DESIGN 每节标题后标〔草案〕或〔已确认〕；〔草案〕可直接写，写完请用户确认
- 每完成一步设计或执行，先更新文档，再请用户确认；用户核对的是文档，不是聊天记录
- 缺的内容空着，不编造

## 按任务读

| 任务 | 读 |
| :--- | :--- |
| 写或改 DESIGN.md | reference/design.md，范例 examples/algo-design.md |
| 画算法流程图 | reference/design.md 流程图一节 + scripts/make_flowchart.py |
| 新建 PLAN.md、DECISIONS.md、README.md、API.md | reference/templates.md |
| 代码改了要同步文档、写 `[n]` 引用 | reference/sync.md |
