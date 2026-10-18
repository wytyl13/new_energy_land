# -*- coding: utf-8 -*-
"""陆上全省风电出力部署入口：一组机组参数生成一组省级出力。"""
from pathlib import Path
import json
import re
import importlib.util
import sys
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'onshore_province_wind_power_config.json'
ENC = 'utf-8-sig'


def parse_grid_name(value):
    m = re.search(r'_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)$', value)
    if not m:
        raise ValueError(f'无法解析格点名称: {value}')
    return float(m.group(1)), float(m.group(2))


def read_config():
    with CONFIG.open('r', encoding='utf-8') as f:
        return json.load(f)


def read_city_weather(path, assignment, prefix, source_prefix):
    data = pd.read_csv(path, encoding=ENC)
    time = pd.to_datetime(data['time'], errors='coerce') + pd.Timedelta(hours=8)
    result = {}
    for (code, city), block in assignment.groupby(['city_code', 'city'], sort=True):
        cols = []
        for grid in block['wind_grid']:
            lat, lon = parse_grid_name(grid)
            cols.append(f'{source_prefix}_lat_{lat:.2f}_lon_{lon:.2f}')
        result[f'{int(code)}_{city}'] = pd.Series(data[cols].mean(axis=1).to_numpy(float), index=time)
    idx = pd.date_range(f'{prefix}-01-01', f'{prefix}-12-31 23:00:00', freq='h')
    return pd.DataFrame(result).reindex(idx).interpolate(method='time', limit_direction='both')


def load_generated_wind(scene_root, city_key, case, target_index):
    city_name = city_key.split('_', 1)[-1]
    folders = list(scene_root.parent.glob(f'海南{city_name}三类风速场景_地市独立生成*全局分位数50%校正版'))
    if not folders:
        raise FileNotFoundError(f'未找到{city_name}的生成风速场景目录')
    path = folders[0] / case / f'01_{city_name}8760风速场景.csv'
    data = pd.read_csv(path, encoding=ENC)
    values = pd.to_numeric(data.iloc[:, 1], errors='coerce').to_numpy(float)
    if len(values) != 8760:
        raise ValueError(f'{path}不是8760小时场景')
    nonleap = target_index[~((target_index.month == 2) & (target_index.day == 29))]
    return pd.Series(values, index=nonleap).reindex(target_index).interpolate(method='time', limit_direction='both')


def convert_power(wind, temp_k, pressure_pa, turbine, loss_factor):
    hub = float(turbine['hub_height_m'])
    vin = float(turbine['cut_in_m_s'])
    vr = float(turbine['rated_m_s'])
    vout = float(turbine['cut_out_m_s'])
    eta = float(turbine.get('efficiency', 1.0))
    alpha = float(turbine.get('wind_shear_exponent', 0.15))
    vh = wind * (hub / 100.0) ** alpha
    th = temp_k - 0.0065 * (hub - 2.0)
    ph = pressure_pa * np.exp(-9.80665 * hub / (287.05 * th))
    rho_factor = (ph / (287.05 * th)) / 1.225
    frac = np.zeros_like(vh)
    ramp = (vh >= vin) & (vh < vr)
    rated = (vh >= vr) & (vh <= vout)
    frac[ramp] = np.clip(rho_factor[ramp] * (vh[ramp] ** 3 - vin ** 3) / (vr ** 3 - vin ** 3), 0, 1)
    frac[rated] = 1.0
    return frac * eta * float(loss_factor)


def main():
    cfg = read_config()
    root = Path(cfg['input']['project_root'])
    assignment_path = root / cfg['input']['assignment_file']
    weather_root = root / cfg['input']['weather_grid_dir']
    scene_root = root / cfg['input']['scene_root']
    output_dir = Path(cfg['output_dir'])
    output_dir.mkdir(parents=True, exist_ok=True)
    if cfg.get('scenario', {}).get('generate_wind_scenario', True):
        generate_all_city_wind_scenarios(root, assignment_path)
    year = int(cfg['scenario']['year'])
    case = cfg['scenario']['resource_year_type']
    idx = pd.date_range(f'{year}-01-01', f'{year}-12-31 23:00:00', freq='h')
    assignment = pd.read_csv(assignment_path, encoding=ENC)
    assignment = assignment.loc[assignment['assignment_method'].eq('inside')].copy()
    cities = [f'{int(code)}_{city}' for (code, city), _ in assignment.groupby(['city_code', 'city'], sort=True)]
    wind = pd.concat({city: load_generated_wind(scene_root, city, case, idx) for city in cities}, axis=1)
    temp = read_city_weather(weather_root / 'temperature' / f'hainan_temperature_{year}.csv', assignment, str(year), 'temperature_2m_k')
    pressure = read_city_weather(weather_root / 'pressure' / f'hainan_pressure_{year}.csv', assignment, str(year), 'surface_pressure_pa')
    if 'city_configs' not in cfg or set(cities) - set(cfg['city_configs']):
        missing = sorted(set(cities) - set(cfg.get('city_configs', {})))
        raise ValueError(f'配置文件缺少地市多机型配置: {missing}')
    city_capacities = pd.Series({city: float(cfg['city_configs'][city]['capacity_mw']) for city in cities}, dtype=float)
    if city_capacities.sum() <= 0:
        raise ValueError('city_configs中的装机容量总和必须大于0')
    weights = city_capacities / city_capacities.sum()
    total_capacity = float(city_capacities.sum())
    power = np.zeros(len(idx))
    city_output = {}
    for city in cities:
        city_cfg = cfg['city_configs'][city]
        city_capacity = float(city_cfg['capacity_mw'])
        turbines = city_cfg['turbines']
        if not turbines:
            raise ValueError(f'{city}未配置turbines')
        configured_capacity = sum(float(t['count']) * float(t['rated_power_mw']) for t in turbines)
        scale = city_capacity / max(configured_capacity, 1e-9)
        city_power = np.zeros(len(idx))
        for turbine in turbines:
            fraction = convert_power(wind[city].to_numpy(float), temp[city].to_numpy(float), pressure[city].to_numpy(float), turbine, cfg.get('loss_factor', 1.0))
            city_power += float(turbine['count']) * float(turbine['rated_power_mw']) * scale * fraction
        city_output[city] = city_power
        power += city_power
    out = pd.DataFrame({'time': idx, 'province_wind_speed_m_s': sum(float(weights.get(c, 0.0)) * wind[c] for c in cities), 'province_power_mw': power, 'province_power_pu': power / total_capacity})
    if cfg.get('output_city_power', True):
        for city, city_power in city_output.items():
            out[f'{city}_power_mw'] = city_power
    out.to_csv(output_dir / f'onshore_wind_power_{year}_{case}.csv', index=False, encoding=ENC)
    (output_dir / '运行参数记录.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已生成: {output_dir / f"onshore_wind_power_{year}_{case}.csv"}')


def generate_all_city_wind_scenarios(root, assignment_path):
    """调用当前地市独立风速生成算法，为所有地市生成三类8760小时场景。"""
    source_path = HERE / 'generate_city_wind_scenarios_template.py'
    if not source_path.exists():
        raise FileNotFoundError(f'部署目录缺少风速场景生成代码: {source_path}')
    assignment = pd.read_csv(assignment_path, encoding=ENC)
    cities = assignment.loc[assignment['assignment_method'].eq('inside'), ['city_code', 'city']].drop_duplicates()
    source = source_path.read_text(encoding='utf-8')
    for code, city in cities.itertuples(index=False):
        key = f'{int(code)}_{city}'
        out_name = f'海南{city}三类风速场景_地市独立生成_严格边界_2000_2024训练_2025目标_全局分位数50%校正版'
        replacements = {
            'BASE_DIR = Path(__file__).resolve().parent': f'BASE_DIR = Path(r"{root}")',
            'CITY = "460400_儋州市"': f'CITY = "{key}"',
            '儋州市': city,
            '海南儋州市三类风速场景_地市独立生成_严格边界_2000_2024训练_2025目标_全局分位数50%校正版': out_name,
        }
        code_text = source
        for old, new in replacements.items():
            code_text = code_text.replace(old, new)
        namespace = {'__name__': '__main__', '__file__': str(source_path)}
        exec(compile(code_text, str(source_path), 'exec'), namespace)


if __name__ == '__main__':
    main()
