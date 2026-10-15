import sys,os,json; os.environ['TF_CPP_MIN_LOG_LEVEL']='3'; sys.path.insert(0,'/home/claude/j2work')
import numpy as np, pandas as pd, fx
from tensorflow import keras
S=fx.load_synth(); tr,va,te=fx.split_users(list(S))
m=keras.models.load_model('/home/claude/wealthsense/ml-training/models/forecaster.keras')
print('params',m.count_params())
B,F,Y,U=fx.raw_windows(S,te,stride=1); print(B.shape,len(te))
ns=pd.read_csv('/home/claude/wealthsense/ml-training/models/forecaster_norm_stats.csv').set_index('user_id')
mu=ns.loc[U,'balance_mean'].to_numpy(); sd=ns.loc[U,'balance_std'].to_numpy()
fm=np.array([S[u][1].mean() for u in U]); fs=np.array([S[u][1].std()+1e-6 for u in U])
# A: training-time protocol (user-level 90-day statistics, absolute cumulative balance)
XA=np.stack([(B-mu[:,None])/sd[:,None],(F-fm[:,None])/fs[:,None]],-1).astype(np.float32)
pA=m.predict(XA,batch_size=4096,verbose=0)[:,0]*sd+mu
# B: deployment protocol (30-day window statistics, relative balance), same trained weights
XB,bm,bs=fx.norm_window(B,F)
pB=fx.to_money(m.predict(XB,batch_size=4096,verbose=0)[:,0],B,bm,bs)
# naive for reference
pN=B[:,-1]
def per(p): return fx.metrics_user(p,Y,U)
res={}
for n,p in [('A_trained_protocol_90day',pA),('B_deployed_protocol_30day_same_weights',pB),('naive',pN)]:
    mean,lo,hi=fx.boot_ci(per(p)); res[n]=dict(mae=mean,lo=lo,hi=hi,rmse=float(np.sqrt(np.mean((p-Y)**2))))
d=fx.paired_boot(per(pB),per(pA)); res['B_minus_A_paired']=list(map(float,d))
print(json.dumps(res,indent=1)); json.dump(res,open('res_norm.json','w'),indent=1)
