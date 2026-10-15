import sys,json,time,os,warnings; warnings.filterwarnings('ignore')
os.environ['TF_CPP_MIN_LOG_LEVEL']='3'
sys.path.insert(0,'/home/claude/j2work')
import numpy as np, pandas as pd, fx, stat_models as sm
import tensorflow as tf
from tensorflow import keras
import lightgbm as lgb
from sklearn.linear_model import Ridge
ds=sys.argv[1]; SEEDS=int(sys.argv[2]) if len(sys.argv)>2 else 5
EV_PER_USER=int(os.environ.get('EVPU',30)); TR_CAP=int(os.environ.get('TRCAP',40)); EPOCHS=int(os.environ.get('EPOCHS',20))
S=fx.load_berka(1500) if ds=='berka' else fx.load_synth()
tr,va,te=fx.split_users(list(S)); print(ds,len(tr),len(va),len(te),flush=True)
rng=np.random.default_rng(5)
Btr,Ftr,Ytr,Utr=fx.raw_windows(S,tr,max_per_user=TR_CAP,rng=rng)
Bva,Fva,Yva,Uva=fx.raw_windows(S,va,max_per_user=20,rng=rng)
Bte,Fte,Yte,Ute=fx.raw_windows(S,te,max_per_user=EV_PER_USER,rng=np.random.default_rng(9))
print('windows',Btr.shape,Bva.shape,Bte.shape,flush=True)
Xtr,bmt,bst=fx.norm_window(Btr,Ftr); ytr=fx.targets(Btr,Ytr,bmt,bst)
Xva,bmv,bsv=fx.norm_window(Bva,Fva); yva=fx.targets(Bva,Yva,bmv,bsv)
Xte,bme,bse=fx.norm_window(Bte,Fte)
res={}  # name -> per-user MAE series (mean over seeds for stochastic)
import pickle
CK=f'/home/claude/j2work/ck_{ds}.pkl'
preds=pickle.load(open(CK,'rb')) if os.path.exists(CK) else {}
def save(): pickle.dump(preds,open(CK,'wb'))
def add(name,pred): preds.setdefault(name,[]).append(pred); save()
# classical
if 'naive' not in preds:
  for n in ['naive','drift','snaive7','snaive30']: add(n,getattr(fx,n)(Bte,Fte))
t=time.time()
for n in ['ar5','arima','ets']:
  if n not in preds: add(n,getattr(sm,n)(Bte,Fte)); print(n,time.time()-t,flush=True)
# ridge on normalised window
if 'ridge' not in preds:
  r=Ridge(alpha=1.0).fit(Xtr.reshape(len(Xtr),-1),ytr); add('ridge',fx.to_money(r.predict(Xte.reshape(len(Xte),-1)),Bte,bme,bse))
def mk(kind):
    L=keras.layers
    if kind=='bilstm': layers=[L.Bidirectional(L.LSTM(48,return_sequences=True)),L.Bidirectional(L.LSTM(24))]
    if kind=='lstm': layers=[L.LSTM(64,return_sequences=True),L.LSTM(32)]
    if kind=='gru': layers=[L.GRU(64,return_sequences=True),L.GRU(32)]
    m=keras.Sequential([L.Input(shape=(fx.W,2))]+layers+[L.Dense(16,activation='relu'),L.Dense(1)])
    m.compile(optimizer=keras.optimizers.Adam(1e-3),loss='mae'); return m
info={}
for seed in range(SEEDS):
    for kind in ['bilstm','lstm','gru']:
        if len(preds.get(kind,[]))>seed: continue
        keras.utils.set_random_seed(seed); m=mk(kind); t=time.time()
        h=m.fit(Xtr,ytr,validation_data=(Xva,yva),epochs=EPOCHS,batch_size=256,verbose=0,
                callbacks=[keras.callbacks.EarlyStopping(monitor='val_loss',patience=4,restore_best_weights=True)])
        add(kind,fx.to_money(m.predict(Xte,batch_size=2048,verbose=0)[:,0],Bte,bme,bse))
        info.setdefault(kind,{'params':m.count_params(),'epochs':[],'val':[]}); info[kind]['epochs'].append(len(h.history['loss'])); info[kind]['val'].append(min(h.history['val_loss']))
        print(ds,seed,kind,len(h.history['loss']),round(min(h.history['val_loss']),4),round(time.time()-t),'s',flush=True)
    if len(preds.get('lightgbm',[]))>seed: continue
    # lightgbm on flattened window + summary stats
    def feats(X,B,F): return np.hstack([X.reshape(len(X),-1),F[:,-7:].sum(1,keepdims=True)/ (np.abs(F).mean(1,keepdims=True)+1e-6),(B[:,-1:]-B[:,:1])/(B.std(1,keepdims=True)+1e-6)])
    g=lgb.LGBMRegressor(objective='l1',n_estimators=400,learning_rate=0.05,num_leaves=31,subsample=0.8,subsample_freq=1,colsample_bytree=0.8,random_state=seed,verbose=-1,n_jobs=2)
    g.fit(feats(Xtr,Btr,Ftr),ytr,eval_set=[(feats(Xva,Bva,Fva),yva)],callbacks=[lgb.early_stopping(30,verbose=False)])
    add('lightgbm',fx.to_money(g.predict(feats(Xte,Bte,Fte)),Bte,bme,bse))
rows=[];pu={}
for n,ps in preds.items():
    seedmae=[fx.metrics_user(p,Yte,Ute) for p in ps]
    mu=pd.concat(seedmae,axis=1).mean(1); pu[n]=mu
    mean,lo,hi=fx.boot_ci(mu)
    seedmeans=[s.mean() for s in seedmae]
    rows.append(dict(model=n,mae=mean,ci_lo=lo,ci_hi=hi,seed_sd=float(np.std(seedmeans)) if len(ps)>1 else 0.0,n_seeds=len(ps)))
R=pd.DataFrame(rows).sort_values('mae'); print(R.to_string(index=False))
base=pu['naive']
paired={n:fx.paired_boot(pu[n],base) for n in pu}
# RMSE too
rm={n:float(np.sqrt(np.mean([np.mean((p-Yte)**2) for p in ps]))) for n,ps in preds.items()}
json.dump(dict(table=R.to_dict('records'),paired_vs_naive={k:list(map(float,v)) for k,v in paired.items()},rmse=rm,info=info,
   n_users=dict(train=len(tr),val=len(va),test=len(te)),n_windows=dict(train=len(Xtr),val=len(Xva),test=len(Xte))),open(f'/home/claude/j2work/res_forecast_{ds}.json','w'),indent=1)
pd.DataFrame(pu).to_csv(f'/home/claude/j2work/peruser_{ds}.csv')
np.savez(f'/home/claude/j2work/pred_{ds}.npz',Y=Yte,U=Ute.astype(str),**{k:np.mean(v,0) for k,v in preds.items()})
