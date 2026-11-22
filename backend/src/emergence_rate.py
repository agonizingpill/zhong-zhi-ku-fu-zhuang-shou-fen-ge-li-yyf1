import hashlib,json,math
import numpy as np
from sklearn.linear_model import BayesianRidge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error

# 同作物历次播量、播期(年内日序)与实测出苗率，按小样本回归给出预测区间。
# 与活力模型同口径：固定seed32拆分训练/留出，缩放器与模型只fit训练集，
# 区间为高斯预测均值±1.96预测标准差，裁到0–100。样本不足时调用方转人工核定。
SEED=32
def fit_interval(rows,sowing_doy,min_samples):
    # rows: [{'sown':int,'doy':int,'emerged':int,'rate_pct':float}, ...]
    n=len(rows)
    base={'model':'StandardScaler(train only) + BayesianRidge on crop emergence history',
          'seed':SEED,'min_samples':min_samples,'sample_rows':n,
          'interval':'Gaussian predictive mean ±1.96 predictive standard deviation, clipped to 0–100',
          'inputs':[{'sown':r['sown'],'sowing_doy':r['doy'],'emerged':r['emerged'],'rate_pct':r['rate_pct']} for r in rows],
          'target_sowing_doy':sowing_doy}
    if n<min_samples:
        return base|{'reliable':False,'lower_pct':None,'upper_pct':None,'estimate_pct':None,
                     'needed_seeds_lower_bound':None,
                     'note':'同作物历史样本不足，区间仅供参考，需主管人工核定重播需种量'}
    data=np.array([[r['sown'],r['doy'],r['rate_pct']] for r in rows],dtype=float)
    idx=np.arange(n)
    if n>=2*min_samples:
        train,hold=train_test_split(idx,test_size=.25,random_state=SEED)
    else:
        # 小样本不做留出，全部参与拟合，覆盖率等留出指标不报告。
        train,hold=idx,np.array([],dtype=int)
    scaler=StandardScaler();x=scaler.fit_transform(data[train,:2])
    model=BayesianRidge();model.fit(x,data[train,2])
    value,sigma=model.predict(scaler.transform([[data[:,0].mean(),sowing_doy]]),return_std=True)
    mean=float(np.asarray(value).ravel()[0]);spread=float(np.asarray(sigma).ravel()[0])
    lower=max(0.0,mean-1.96*spread);upper=min(100.0,mean+1.96*spread)
    out=base|{'reliable':True,'estimate_pct':round(mean,3),'lower_pct':round(lower,3),'upper_pct':round(upper,3),
              'train_rows':len(train),'holdout_rows':len(hold),
              'train_indices':list(train),'holdout_indices':list(hold),
              'source_checksum':hashlib.sha256(json.dumps(base['inputs'],separators=(',',':')).encode()).hexdigest()}
    if len(hold):
        hm,hs=model.predict(scaler.transform(data[hold,:2]),return_std=True)
        out['holdout_mae_percentage_points']=round(float(mean_absolute_error(data[hold,2],hm)),4)
        out['holdout_interval95_coverage']=round(float(np.mean((data[hold,2]>=hm-1.96*hs)&(data[hold,2]<=hm+1.96*hs))),4)
    else:
        out['holdout_mae_percentage_points']=None;out['holdout_interval95_coverage']=None
        out['note']='样本量不足以拆分留出集，未做覆盖率校验'
    return out

def needed_by_lower_bound(target_plants,lower_pct):
    # 按下界出苗率反推：需种量 = 目标株数 / 下界率，向上取整，至少1粒。
    if lower_pct is None or lower_pct<=0:return None
    return max(1,math.ceil(target_plants/(lower_pct/100.0)))
