# -*- coding: utf-8 -*-
"""陆上全省光伏部署入口：生成各地市辐照度场景并转换为出力。"""
from pathlib import Path
import json
import re
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'onshore_province_pv_power_config.json'
ENC = 'utf-8-sig'; CASES = ['偏小资源年', '正常资源年', '偏大资源年']


def parse_grid(value):
    m = re.search(r'_lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)$', value)
    if not m: raise ValueError(value)
    return float(m.group(1)), float(m.group(2))


def city_grid_info(root, assignment_path):
    a = pd.read_csv(assignment_path, encoding=ENC)
    a = a.loc[a.assignment_method.eq('inside')].copy()
    info = {}
    for (code, city), block in a.groupby(['city_code', 'city'], sort=True):
        key = f'{int(code)}_{city}'
        coords = [parse_grid(x) for x in block.wind_grid]
        info[key] = {'name': city, 'lat': float(np.mean([x[0] for x in coords])), 'lon': float(np.mean([x[1] for x in coords])), 'grids': coords}
    return info


def solar_poa(ghi, times, lat, lon, tilt, azimuth, albedo=.2):
    n = times.dayofyear.to_numpy(); hour = times.hour.to_numpy() + times.minute.to_numpy() / 60
    dec = np.deg2rad(23.45 * np.sin(np.deg2rad(360 * (284 + n) / 365))); ha = np.deg2rad(15 * (hour - 12 + (120 - lon) / 15)); la = np.deg2rad(lat)
    cz = np.clip(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha), 0, 1); z = np.arccos(cz); beta = np.deg2rad(tilt); az = np.deg2rad(azimuth)
    safe = np.maximum(cz, .02); dni = np.divide(ghi, safe, out=np.zeros_like(ghi), where=cz > 0); kt = np.clip(ghi / (1367 * safe), 0, 1); kd = np.where(ghi > 20, np.clip(1 - 1.13 * kt, .05, 1), 1); dhi = kd * ghi
    ci = np.cos(z) * np.cos(beta) + np.sin(z) * np.sin(beta) * np.cos(az - np.pi)
    return np.maximum(0, dni * np.maximum(ci, 0) + dhi * (1 + np.cos(beta)) / 2 + albedo * ghi * (1 - np.cos(beta)) / 2)


def generate_all_city_solar(root, assignment_path):
    source_path = HERE / 'generate_danzhou_solar_scenarios_city_specific.py'
    if not source_path.exists():
        raise FileNotFoundError(f'部署目录缺少辐照度场景生成代码: {source_path}')
    a = pd.read_csv(assignment_path, encoding=ENC); cities = a.loc[a.assignment_method.eq('inside'), ['city_code', 'city']].drop_duplicates()
    source = source_path.read_text(encoding='utf-8')
    for code, city in cities.itertuples(index=False):
        key = f'{int(code):06d}_{city}'; out_name = f'海南{city}三类辐照度场景_地市独立生成_严格边界_2000_2024训练_2025目标'
        repl = {'BASE_DIR = Path(__file__).resolve().parent': f'BASE_DIR = Path(r"{root}")', 'CITY_CODE = "460400"': f'CITY_CODE = "{int(code):06d}"', 'CITY_NAME = "儋州市"': f'CITY_NAME = "{city}"', 'CITY_COLUMN = f"{CITY_CODE}_{CITY_NAME}"': f'CITY_COLUMN = "{key}"', '儋州市': city, '海南儋州市三类辐照度场景_地市独立生成_严格边界_2000_2024训练_2025目标': out_name}
        text = source
        for old, new in repl.items(): text = text.replace(old, new)
        exec(compile(text, str(source_path), 'exec'), {'__name__': '__main__', '__file__': str(source_path)})


def main():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8')); root = Path(cfg['input']['project_root']); assignment_path = root / cfg['input']['assignment_file']; outdir = Path(cfg['output_dir']); outdir.mkdir(parents=True, exist_ok=True)
    info = city_grid_info(root, assignment_path)
    year = int(cfg['scenario']['year']); idx = pd.date_range(f'{year}-01-01', f'{year}-12-31 23:00:00', freq='h'); idx = idx[~((idx.month == 2) & (idx.day == 29))]  # 删闰日，与 8760 小时场景轴按日对齐
    times = pd.date_range('2025-01-01', periods=8760, freq='h')
    if cfg['scenario'].get('generate_solar_scenario', True): generate_all_city_solar(root, assignment_path)
    temp = {}
    path = root / cfg['input']['weather_grid_dir'] / 'temperature' / f'hainan_temperature_{year}.csv'
    d = pd.read_csv(path, encoding=ENC); t = pd.to_datetime(d.time, errors='coerce') + pd.Timedelta(hours=8); d = d.drop(columns=['time']); d.index = t
    d = d.loc[~((d.index.month == 2) & (d.index.day == 29))]
    for key, meta in info.items():
        cols = [f'temperature_2m_k_lat_{lat:.2f}_lon_{lon:.2f}' for lat, lon in meta['grids']]; temp[key] = d[cols].mean(axis=1).reindex(idx).interpolate(method='time', limit_direction='both').to_numpy(float)
    totals = {case: np.zeros(8760) for case in CASES}; city_rows = []
    for key, meta in info.items():
        city_cfg = cfg['city_configs'][key]; cap = float(city_cfg['capacity_mw']); city_dir = outdir / key; city_dir.mkdir(parents=True, exist_ok=True); configured = sum(float(m['count']) * float(m['rated_power_mw']) for m in city_cfg['modules']); scale = cap / max(configured, 1e-9)
        for case in CASES:
            folder = next(root.glob(f'海南{meta["name"]}三类辐照度场景_地市独立生成*')); fs = list((folder / case).glob('*8760*辐照度场景.csv')); scene = pd.read_csv(fs[0], encoding=ENC); ghi = pd.to_numeric(scene['生成辐照度_W_m2'], errors='coerce').to_numpy(float); total = np.zeros(8760); out = pd.DataFrame({'time': times, 'generated_ghi_w_m2': ghi})
            for module in city_cfg['modules']:
                poa = solar_poa(ghi, times, meta['lat'], meta['lon'], float(module['tilt_deg']), float(module.get('azimuth_deg', 180)), float(module.get('albedo', .2))); tc = temp[key] - 273.15 + (float(module['noct_c']) - 20) / 800 * poa; frac = np.clip(poa / 1000 * (1 + float(module['gamma_p_per_c']) * (tc - 25)) * float(module.get('efficiency', 1.0)) * float(cfg.get('loss_factor', 1.0)), 0, 1.2); p = float(module['count']) * float(module['rated_power_mw']) * scale * frac; total += p; out[f'{module["model"]}_power_mw'] = p
            out[f'{key}_total_power_mw'] = total; out[f'{key}_total_power_pu'] = total / cap; out.to_csv(city_dir / f'{case}_{meta["name"]}_光伏出力场景.csv', index=False, encoding=ENC); totals[case] += total; city_rows.append({'地市': key, '资源年类型': case, '装机容量_MW': cap, '年利用小时_h': total.sum() / cap, '容量因子': total.mean() / cap})
    total_cap = sum(float(x['capacity_mw']) for x in cfg['city_configs'].values()); reg = outdir / '区域'; reg.mkdir(exist_ok=True)
    for case, value in totals.items(): pd.DataFrame({'time': times, 'province_power_mw': value, 'province_power_pu': value / total_cap}).to_csv(reg / f'区域_{case}_光伏出力场景.csv', index=False, encoding=ENC)
    pd.DataFrame(city_rows).to_csv(outdir / '各地市光伏出力摘要.csv', index=False, encoding=ENC); print('已生成光伏区域出力:', reg)


if __name__ == '__main__': main()
