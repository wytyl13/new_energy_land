# 新能源出力接口

> 本服务属 new_energy_land 项目（海南陆上风电/光伏出力场景生成）：前端传指定输出年与计算单元（地市或框选区域）的装机配置，返回三类资源年（偏小/正常/偏大）的逐小时出力场景。实现在 `api/server/main_server.py`，端口 9998；同步阻塞（全省风电约 4 分钟），部署侧代理超时设 ≥300s。
> 改格式前先在 DECISIONS.md 记一条；同步改测试里的格式断言。

## 风电出力 POST /api/wind/run

读格点风速/温度/气压，按计算单元生成三类资源年的陆上风电逐小时出力（多机型叠加、空气密度修正、切变外推）。运行时长（实测）：全省 18 地市约 220s；单地市约 13s；单区域单元（全部格点聚合）约 14s。

传参：

```jsonc
{
  "scenario": {                                          // 场景参数
    "year": 2024,                                        // 必填，输出年（整数），须存在该年温度/气压数据文件
    "resource_year_type": "正常资源年"                    // 必填，∈ {偏小资源年, 正常资源年, 偏大资源年}
  },
  "output_dir": "E:\\result",                            // 必填，出力 CSV 写入目录
  "data_dir": "c:/data/hainan_era5_grid_csv",            // 可选（默认服务端 data/hainan_era5_grid_csv），格点数据根目录，须含 wind/temperature/pressure 子目录
  "assignment_file": "c:/全国格点地市归属.csv",           // 可选（默认根目录全国表），格点归属表
  "city_configs": [                                      // 可选（默认全省 18 地市 default_city_configs.json），计算单元列表；单元名匹配规则见 DESIGN「请求解析与单元匹配」
    {
      "city": "460100_海口市",                            // 必填，单元名 = DataV 行政区划码_城市名
      "capacity_mw": 22.29,                              // 必填（>0），单元装机权威值，机组容量合计等比缩放到它
      "turbines": [                                      // 必填，机型数组
        {
          "model": "6MW机组",                             // 必填，机型名
          "count": 1,                                    // 必填（正整数），台数
          "rated_power_mw": 6,                           // 必填（>0），单机额定 MW
          "hub_height_m": 140,                           // 必填，轮毂高度 m（切变外推用）
          "cut_in_m_s": 3,                               // 必填，切入风速 m/s
          "rated_m_s": 11,                               // 必填，额定风速 m/s
          "cut_out_m_s": 25,                             // 必填，切出风速 m/s
          "efficiency": 0.94,                            // 可选（默认 1.0），综合效率系数
          "wind_shear_exponent": 0.15                    // 可选（默认 0.15），风切变指数 α
        }
      ]
    }
  ],
  "loss_factor": 1.0,                                    // 可选（默认 1.0），出力折减系数
  "output_city_power": true,                             // 可选（默认 true），是否输出各单元出力列
  "evaluate": false,                                     // 可选（默认 false），是否运行评估指标
  "variable_prefixes": {                                 // 可选（默认 KIND_PREFIX 常量），kind→变量列前缀声明；命中列前缀不符报错
    "wind": "wind_speed_100m",                           // 风速列前缀
    "temperature": "temperature_2m_k",                   // 温度列前缀
    "pressure": "surface_pressure_pa"                    // 气压列前缀
  }
}
```

返回：

```jsonc
{
  "output_file": "E:\\result\\onshore_wind_power_2024_正常资源年.csv",  // 已写出的出力 CSV 路径
  "elapsed_s": 221.6,                                    // 计算耗时秒
  "scenario": {                                          // 回显请求的场景参数
    "year": 2024,                                        // 输出年
    "resource_year_type": "正常资源年"                    // 资源年类型
  },
  "cities": ["460100_海口市"],                           // 实际计算范围（聚合列名不随范围变，靠此字段自曝）
  "total_capacity_mw": 22.29,                            // 所传单元装机合计
  "power_table": {                                       // 列名 → 8784/8760 长数组的字典，与 CSV 同列同值
    "time": ["2024-01-01 00:00:00", "..."],              // 时间列（输出年时间轴）
    "province_power_mw": [0.0, "..."]                    // 全省/区域出力列（其余列同结构）
  }
}
```

## 光伏出力 POST /api/solar/run

读格点辐照度/温度，按计算单元生成三类资源年的光伏逐小时出力（POA 转换、组件温度修正、多组件叠加）。运行时长（实测）：儋州区域约 90s；单区域单元（全部格点聚合）约 45s。

传参：

```jsonc
{
  "scenario": {                                          // 场景参数
    "year": 2025,                                        // 必填，输出年（整数）
    "resource_year_type": "正常资源年"                    // 必填，∈ {偏小资源年, 正常资源年, 偏大资源年}
  },
  "output_dir": "E:\\result",                            // 必填，出力 CSV 写入目录
  "data_dir": "c:/data/hainan_era5_grid_csv",            // 可选（默认服务端 data/hainan_era5_grid_csv），格点数据根目录，须含 solar/temperature 子目录
  "assignment_file": "c:/全国格点地市归属.csv",           // 可选（默认根目录全国表），格点归属表
  "city_configs": [                                      // 可选（默认儋州 default_city_configs.json），计算单元列表
    {
      "city": "460400_儋州市",                            // 必填，单元名 = DataV 行政区划码_城市名
      "capacity_mw": 955.21,                             // 必填（>0），单元装机权威值，组件容量合计等比缩放到它
      "latitude": 19.56,                                 // 可选（命中单元=归属表格点中心；未命中单单元=数据格点均值），场站代表纬度，POA 与组件温度用
      "longitude": 109.58,                               // 可选（同上），场站代表经度
      "components": [                                    // 必填，组件数组
        {
          "name": "常规组件",                             // 必填，组件名
          "count": 3,                                    // 必填（正整数），台数
          "rated_power_mw": 200.0,                       // 必填（>0），单台额定 MW
          "tilt_deg": 18.0,                              // 必填，倾角（POA 用）
          "azimuth_deg": 180.0,                          // 必填，方位角（POA 用）
          "noct_c": 45.0,                                // 必填，NOCT 组件温度参数
          "gamma_p_per_c": -0.004,                       // 必填，功率温度系数 γ
          "efficiency": 1.0                              // 可选（默认 1.0），综合效率系数
        }
      ]
    }
  ],
  "loss_factor": 0.9,                                    // 可选（默认 1.0），出力折减系数
  "albedo": 0.2,                                         // 可选（默认 0.2），地面反射率（POA 地面反射分量用）
  "output_city_power": true,                             // 可选（默认 true），是否输出各单元出力列
  "variable_prefixes": {                                 // 可选（默认 KIND_PREFIX 常量），kind→变量列前缀声明；命中列前缀不符报错
    "solar": "irradiance_w_m2",                          // 辐照度列前缀
    "temperature": "temperature_2m_k"                    // 温度列前缀
  }
}
```

返回：

```jsonc
{
  "output_file": "E:\\result\\pv_power_2025_正常资源年.csv",  // 已写出的出力 CSV 路径
  "elapsed_s": 88.6,                                     // 计算耗时秒
  "scenario": {                                          // 回显请求的场景参数
    "year": 2025,                                        // 输出年
    "resource_year_type": "正常资源年"                    // 资源年类型
  },
  "cities": ["460400_儋州市"],                           // 实际计算范围
  "total_capacity_mw": 955.21,                           // 所传单元装机合计
  "power_table": {                                       // 列名 → 8760 长数组的字典，与 CSV 同列同值
    "时间": ["2025-01-01 00:00:00", "..."],              // 时间列
    "区域总出力_MW": [0.0, "..."]                        // 区域总出力列（其余列同结构）
  }
}
```

## 错误约定

- 请求/数据类错误一律 `ValueError`（消息指明字段/文件/单元）→ HTTP 400，返回体带错误信息；其它异常 → HTTP 500
- 未知字段逐层拒绝；单元名匹配失败、前缀不符、缺数据文件、行数不符均属 400
