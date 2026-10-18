# -*- coding: utf-8 -*-
"""通用多地市、多组件光伏物理出力模型。"""
from __future__ import annotations
import json,re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

BASE=Path(__file__).resolve().parent; GRID=BASE/"hainan_era5_grid_csv"; ASSIGN=BASE/"海南地市分区"/"海南共同80格点_地市归属.csv"; CFG=BASE/"danzhou_pv_physical_model_config.json"; OUT=BASE/"海南区域光伏物理模型出力场景_多地市版"; CASES=["偏小资源年","正常资源年","偏大资源年"]; ENC="utf-8-sig"
def idx(): return pd.date_range("2025-01-01",periods=8760,freq="h")
def parse(c):
    m=re.search(r"lat_(-?\d+\.\d+)_lon_(-?\d+\.\d+)$",c); return (round(float(m.group(1)),2),round(float(m.group(2)),2)) if m else None
def city_grids(city):
    a=pd.read_csv(ASSIGN,encoding=ENC); return {(round(float(x),2),round(float(y),2)) for x,y in a[(a.city==city)&(a.assignment_method=="inside")][["latitude","longitude"]].itertuples(index=False)}
def weather(kind,city):
    wanted=city_grids(city); vals=[]; prefix="temperature" if kind=="temperature" else "pressure"
    for y in range(2000,2025):
        fs=list((GRID/kind).glob(f"hainan_{prefix}_{y}.csv"));
        if not fs: continue
        d=pd.read_csv(fs[0],encoding=ENC); d.index=pd.to_datetime(d.pop("time"))+pd.Timedelta(hours=8); cols=[c for c in d.columns if parse(c) in wanted]; s=d.loc[~((d.index.month==2)&(d.index.day==29)),cols].mean(axis=1)
        if len(s)==8760: vals.append(s.to_numpy())
    if not vals: raise FileNotFoundError(f"{city}没有可用的{kind}历史数据")
    return pd.Series(np.nanmean(np.vstack(vals),axis=0),index=idx())
def read_scene(city,case,cc):
    d=Path(cc["scenario_dir"]); d=BASE/d if not d.is_absolute() else d; fs=list((d/case).glob("*8760*辐照度场景.csv"));
    if not fs: raise FileNotFoundError(f"找不到{city}的辐照度场景")
    x=pd.read_csv(fs[0],encoding=ENC); return pd.Series(pd.to_numeric(x["生成辐照度_W_m2"],errors="coerce").to_numpy(float),index=idx()).clip(lower=0)
def poa(ghi,t,p):
    n=t.dayofyear.to_numpy(); h=t.hour.to_numpy()+t.minute.to_numpy()/60; dec=np.deg2rad(23.45*np.sin(np.deg2rad(360*(284+n)/365))); ha=np.deg2rad(15*(h-12+(120-p["longitude"])/15)); la=np.deg2rad(p["latitude"]); cz=np.clip(np.sin(la)*np.sin(dec)+np.cos(la)*np.cos(dec)*np.cos(ha),0,1); z=np.arccos(cz); beta=np.deg2rad(p["tilt_deg"]); az=np.deg2rad(p["azimuth_deg"]); safe=np.maximum(cz,.02); dni=np.divide(ghi,safe,out=np.zeros_like(ghi),where=cz>0); kd=np.where(ghi>20,np.clip(1-1.13*np.clip(ghi/(1367*safe),0,1),.05,1),1); dhi=kd*ghi; ci=np.cos(z)*np.cos(beta)+np.sin(z)*np.sin(beta)*np.cos(az-np.pi); return np.maximum(0,dni*np.maximum(ci,0)+dhi*(1+np.cos(beta))/2+p["albedo"]*ghi*(1-np.cos(beta))/2)
def main():
    cfg=json.loads(CFG.read_text(encoding="utf-8")); OUT.mkdir(exist_ok=True); rows=[]; all_city=[]
    for city,cc in cfg["cities"].items():
        name=city.split("_",1)[-1]; temp=weather("temperature",name)-273.15; press=weather("pressure",name); cap=float(cc["capacity_mw"]); city_dir=OUT/city; city_dir.mkdir(exist_ok=True); fig,ax=plt.subplots(3,2,figsize=(14,10))
        for i,case in enumerate(CASES):
            ghi=read_scene(name,case,cc); out=pd.DataFrame({"时间":idx(),"生成辐照度_W_m2":ghi.values,"地面温度_C":temp.values,"地面气压_Pa":press.values}); total=np.zeros(8760)
            for comp in cc["components"]:
                q=dict(comp); q.update({"latitude":cc["latitude"],"longitude":cc["longitude"],"albedo":cfg.get("albedo",.2)}); g=poa(ghi.values,idx(),q); tc=temp.values+(q["noct_c"]-20)/800*g; p=cap*float(comp["capacity_share"])*float(cfg.get("loss_factor",1))*float(comp.get("efficiency",1))*np.clip(g/1000*(1+q["gamma_p_per_c"]*(tc-25)),0,1.2); out[f"{comp['name']}_POA_W_m2"]=g; out[f"{comp['name']}_组件温度_C"]=tc; out[f"{comp['name']}_出力_MW"]=p; total+=p
            out[f"{name}_总出力_MW"]=total; out[f"{name}_总出力_pu"]=total/cap; out.to_csv(city_dir/f"{case}_{name}_光伏出力场景.csv",index=False,encoding=ENC); all_city.append((city,case,total)); rows.append({"地市":city,"资源年类型":case,"装机容量_MW":cap,"平均出力_MW":total.mean(),"最大出力_MW":total.max(),"年利用小时_h":total.sum()/cap,"容量因子":total.mean()/cap}); ax[i,0].plot(ghi.values,lw=.5); ax[i,0].set_title(case+"：辐照度"); ax[i,1].plot(total,lw=.5); ax[i,1].set_title(case+"：光伏出力")
        fig.tight_layout(); fig.savefig(city_dir/f"{name}三类光伏出力场景曲线.png",dpi=180); plt.close(fig)
    total_cap=sum(float(x["capacity_mw"]) for x in cfg["cities"].values()); reg=OUT/"区域"; reg.mkdir(exist_ok=True)
    for case in CASES:
        d=pd.DataFrame(index=idx()); total=np.zeros(8760)
        for city,ca,p in all_city:
            if ca==case: d[f"{city}_总出力_MW"]=p; total+=p
        d["区域总出力_MW"]=total; d["区域总出力_pu"]=total/total_cap; d.to_csv(reg/f"区域_{case}_光伏出力场景.csv",index_label="时间",encoding=ENC)
    pd.DataFrame(rows).to_csv(OUT/"各地市光伏出力摘要.csv",index=False,encoding=ENC); (OUT/"物理模型参数.json").write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8"); print(pd.DataFrame(rows).to_string(index=False)); print("输出目录:",OUT)
if __name__=="__main__": main()
