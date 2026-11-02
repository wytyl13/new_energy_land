# new_energy_land

海南陆上风电/光伏出力场景生成：读 ERA5 格点气象 CSV 与装机配置（统一 JSON），生成指定输出年三类资源年（偏小/正常/偏大）的逐小时出力场景，输出出力 CSV + 结果 JSON 双份。

> 算法与工程设计见 [DESIGN.md](DESIGN.md)；HTTP 接口格式见 [API.md](API.md)；设计决策见 [DECISIONS.md](DECISIONS.md)。

## 安装

```bash
git clone https://github.com/wytyl13/new_energy_land.git
cd new_energy_land
conda create -n newenergy_dgzy python=3.10
conda activate newenergy_dgzy
pip install -r requirements.txt
```

> 原始气象数据（1.9G）不入库：将海南 ERA5 格点数据放到 `data/hainan_era5_grid_csv/`（子目录 `wind/`、`solar/`、`temperature/`、`pressure/`，逐年一份 CSV），缺数据文件会报错指明文件。

## 运行

```bash
# CLI（仓库根目录）
python run.py wind tests/fixtures/wind_minimal.json   # 风电，单市约 20s，全省约 4 分钟
python run.py solar tests/fixtures/solar_danzhou.json # 光伏，儋州约 1 分钟

# HTTP 服务（端点与请求/返回示例见 API.md）
python -m api.server.main_server --port 9998          # 端口默认 9998

# 业务层直调（CLI 与 HTTP 同路）
python -c "from biz.runner import run; print(run('wind', 'tests/fixtures/wind_minimal.json'))"
```

配置模板：`.refactor_baseline/config.json`（全省）、`tests/fixtures/`（单市/最简/自定义区域）。请求字段表见 [API.md](API.md)。

## 测试

```bash
python -m tests.new_energy.test_models    # 算法包合成数据测试（~17s，不依赖气象数据）
python -m tests.biz.test_runner_real      # Runner 真实数据全流程：风/光各一单市（~40s，写 tests/output/）
python -m tests.wind_run_selftest         # 风电业务层回归：全省对冻结基准（~4 分钟）+ 一致性/默认值/非法请求
python -m tests.solar_selftest            # 光伏业务层回归：全流程对冻结基准 + 默认值/非法请求
python -m tests.biz.test_schema           # 以下为 biz 单元测试（真实数据，秒级）
python -m tests.biz.test_assignment
python -m tests.biz.test_years
python -m tests.biz.test_grids
python -m tests.biz.test_reader
python -m tests.biz.test_export
python -m tests.biz.test_runner
python -m tests.new_energy.test_time_axis
```

回归判据与冻结基准容差一致（风电：总量 ≤1e-6、逐元素 ≤1e-4；光伏：场景与出力全量比对）；基准件 `.refactor_baseline/`。

## 目录

```
run.py            CLI 入口（薄壳，走 biz.runner.run）
api/              HTTP 服务层（routers 透传统一 JSON）
biz/              业务层：schema 校验 / assignment 归属 / years 年份 / grids 取数 /
                  reader 编排 / runner 总装 / export 写出 / constants 常数
new_energy/       算法层（只算内存）：
                  wind.py solar.py    风/光模型（run_wind/run_solar 入口）
                  align.py            数据对齐（共用）
                  config.py           配置类（构造即校验）
                  constants.py        冻结常数（种子/物理常数/共用生成参数）
                  result.py           结果对象（PowerResult/WindResult/SolarResult）
                  evaluate.py         可选评估
                  utils/              time_axis 时间轴查表、common 零散共用工具
tests/            测试（见上）；fixtures 请求样例
data/             气象格点 CSV 与归属表（不入库）；docs/ 设计文档与流程图脚本
```

## 协作（Fork + PR）

```bash
# 首次：Fork 仓库 → 克隆自己的 Fork → git remote add upstream https://github.com/wytyl13/new_energy_land.git
# 开发前：git checkout main && git pull upstream main && git push origin main
# 开发：git checkout -b feature/algo-update → 改 new_energy/ → commit → push origin
# 提交：网页开 PR（你的分支 → upstream:main），所有者审核合并
# 合并后：git checkout main && git pull upstream main && git push origin main && git branch -d feature/algo-update
```
