# 触发测试

> 新会话里说出"语句"，检查是否加载"应命中"的 skill，且没有加载"不应命中"的。
> "CLAUDE.md"表示该语句由常驻规则处理，不应加载任何 skill。
> 新增或修改 skill 后按 skill `manage-skills` 补行。

| 语句 | 应命中 | 不应命中 |
|---|---|---|
| 给天气读取加个缺列检查 | handle-input-data | change-code |
| CSV 里有 NaN 怎么处理 | handle-input-data | verify-results |
| 公共输入挪到一处统一读 | handle-input-data | design-module-layering |
| 把出力公式推导写出来 | write-math-formula | maintain-project-docs |
| 改一下目标函数这一项 | write-math-formula | change-code |
| 代码里补约束式出处注释 | write-math-formula | write-python-code |
| 把 MATLAB 脚本翻成 Python | port-legacy-code | write-python-code |
| 一比一复刻这份旧代码 | port-legacy-code | change-code |
| 旧部署脚本重构成分层包 | port-legacy-code | design-module-layering |
| 这个函数拆不拆 | write-python-code | change-code |
| 补类型注解 | write-python-code | change-code |
| 两个函数签名统一一下 | write-python-code | change-code |
| 建一个机型参数数据类 | write-python-code | handle-input-data |
| 这个类太大拆一下 | write-python-code | design-module-layering |
| 字段注释格式怎么写 | write-python-code | maintain-project-docs |
| 新建 evaluate.py | write-python-code | bootstrap-project |
| 建个工具脚本 | write-python-code | bootstrap-project |
| 文件头格式补上 | write-python-code | maintain-project-docs |
| common 和 wind 依赖理一下 | design-module-layering | change-code |
| 新模块放哪层 | design-module-layering | bootstrap-project |
| pipeline 能反向 import 吗 | design-module-layering | change-code |
| 改 CLAUDE.md 分工段 | manage-skills | maintain-project-docs |
| 这规则放 skill 还是 DESIGN | manage-skills | maintain-project-docs |
| 进度能直接更新吗 | maintain-project-docs | plan-project |
| 建个 DESIGN.md | maintain-project-docs | bootstrap-project |
| DESIGN 章节怎么排 | maintain-project-docs | manage-skills |
| 重构设计写哪 | maintain-project-docs | port-legacy-code |
| 重写 README | maintain-project-docs | bootstrap-project |
| 算法全流程怎么写 | maintain-project-docs | write-math-formula |
| 收尾把 README 定稿 | maintain-project-docs | bootstrap-project |
| 接口加字段文档同步了吗 | maintain-project-docs | build-fastapi-service |
| README 跟着改吗 | maintain-project-docs | change-code |
| 哪些文档要更新 | maintain-project-docs | manage-skills |
| 画个算法流程图 | maintain-project-docs | write-python-code |
| 维度流转怎么写 | maintain-project-docs | write-math-formula |
| 照参考图出一张网络图 | maintain-project-docs | write-python-code |
| 注释别那么啰嗦 | CLAUDE.md | write-python-code |
| 文档措辞改平实 | CLAUDE.md | maintain-project-docs |
| AI 味太重重写 | CLAUDE.md | maintain-project-docs |
| 新开一个子项目 | plan-project | bootstrap-project |
| 重构分几步走 | plan-project | port-legacy-code |
| 细化方案先给我看 | plan-project | change-code |
| 先出个计划 | plan-project | maintain-project-docs |
| 修一下这个 bug | change-code | plan-project |
| 报错原因在哪 | change-code | verify-results |
| 改这里影响哪些调用方 | change-code | design-module-layering |
| 加个功能 | change-code | plan-project |
| 先别改给我方案 | CLAUDE.md | plan-project |
| 你会动哪些文件 | CLAUDE.md | change-code |
| 改完汇报一下 | CLAUDE.md | verify-results |
| 跑回归对基准 | verify-results | port-legacy-code |
| 结果容差多少 | verify-results | write-math-formula |
| 核对过程记进日志 | verify-results | maintain-project-docs |
| 和原来一致吗 | verify-results | port-legacy-code |
| 为什么要空气密度修正 | CLAUDE.md | write-math-formula |
| GPR 采样数什么意思 | CLAUDE.md | write-math-formula |
| 这段代码在干嘛 | CLAUDE.md | change-code |
| 从零建个新仓库 | bootstrap-project | plan-project |
| 新项目目录怎么搭 | bootstrap-project | design-module-layering |
| 初始化环境和 git | bootstrap-project | change-code |
| 给 pipeline 包一层 HTTP | build-fastapi-service | design-module-layering |
| 加一个 POST 接口 | build-fastapi-service | change-code |
| 异常怎么映射状态码 | build-fastapi-service | change-code |
| 加个配置下发接口格式 | build-fastapi-service | maintain-project-docs |
| categories 改数组吧 | build-fastapi-service | change-code |
| 曲线数据怎么组装返回 | build-fastapi-service | change-code |
| 加个 skill | manage-skills | bootstrap-project |
| 这条规则放哪 | manage-skills | maintain-project-docs |
| 这个 skill 要不要拆 | manage-skills | maintain-project-docs |
| 两个 skill 合并一下 | manage-skills | maintain-project-docs |
| 改一下 skill 的 description | manage-skills | maintain-project-docs |
