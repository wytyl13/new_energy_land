# 计划与进度

## 当前状态
- 正在做：skills 重构已完成，等用户核对
- 下一步：处理待决定 Q-01，再按待办逐项另开任务
- 阻塞：无

## 待决定
- Q-01 原 skill `api-json-format` 里定稿的四块格式（config 下发/提交、categories、曲线、views）和"改格式时"一节，对应的是 datasets/jobs 接口和 `service/defaults.py`、`tests/test_api_upload.py`，本项目没有这些。去处：A 搬到它所属项目的 API.md / B 本项目 API.md 只写本项目接口，这部分不要，待定。原文见提交 afb7cd1 的 `.claude/skills/api-json-format/SKILL.md`

## 步骤
| 编号 | 步骤 | 状态 | 验证结果 |
|---|---|---|---|
| P1 | 重构 skills 与 CLAUDE.md | 已完成 | 见本次提交说明 |

## 待办
- [ ] 本项目 DESIGN.md 迁移到新结构（算法设计 + 工程实现，每节标〔草案〕或〔已确认〕）
- [ ] README.md 精简为只写怎么用；接口说明移到新建的 API.md
- [ ] 原始资料（如 `新能源出力_陆上.md`）整理到 `docs/ref/`
- [ ] `api/server/main_server.py` 跨域改为 `allow_credentials=False`（当前 `"*"` 和 `True` 同时用）
