import sys,json,os,warnings; warnings.filterwarnings('ignore')
sys.path.insert(0,'/home/claude/wealthsense/ml-training'); sys.path.insert(0,'/home/claude/j2work')
import numpy as np, pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score, average_precision_score
import train_anomaly_detector as T
import pickle
CK='/home/claude/j2work/ck_anom_feats.pkl'
if os.path.exists(CK): fd=pickle.load(open(CK,'rb'))
else:
    raw=pd.read_csv(T.DATA_PATH); fd=T.build_features(raw[~raw.is_credit].copy()); pickle.dump(fd,open(CK,'wb'))
print(len(fd),fd.is_anomaly.sum(),flush=True)
users=sorted(fd.user_id.unique()); r=np.random.default_rng(11); u=r.permutation(users); n=len(u)
tr,va,te=set(u[:int(.7*n)]),set(u[int(.7*n):int(.85*n)]),set(u[int(.85*n):])
fd['dow']=fd.date.dt.dayofweek
cats=sorted(fd.category.unique())
for c in cats: fd['cat_'+c]=(fd.category==c).astype(float)
CATC=['cat_'+c for c in cats]
subsets={'amount':['amount'],'amount+z (shipped)':['amount','amount_zscore'],'z only':['amount_zscore'],'amount+z+dow':['amount','amount_zscore','dow'],
 'amount+z+category one-hot':['amount','amount_zscore']+CATC,'amount+z+cat_freq':['amount','amount_zscore','category_freq'],'all four':['amount','amount_zscore','dow','category_freq']+CATC}
D={k:fd[fd.user_id.isin(s)] for k,s in [('tr',tr),('va',va),('te',te)]}
def fit(cols,cont,seed,n_est=100): return IsolationForest(n_estimators=n_est,contamination=cont,random_state=seed,n_jobs=2).fit(D['tr'][cols].to_numpy())
def prf(y,p):
    a,b,c,_=precision_recall_fscore_support(y,p,average='binary',zero_division=0); return a,b,c
out={}
# 1) ablation on VALIDATION only (10 seeds)
abl={}
for k,cols in subsets.items():
    f=[]
    for sd in range(5):
        m=fit(cols,0.05,sd); p=(m.predict(D['va'][cols].to_numpy())==-1).astype(int); f.append(prf(D['va'].is_anomaly,p)[2])
    abl[k]=(float(np.mean(f)),float(np.std(f))); print('val',k,abl[k],flush=True)
out['ablation_val_f1']=abl
best=max(abl,key=lambda k:abl[k][0]); out['selected_on_val']=best
cols=subsets['amount+z (shipped)']
# 2) contamination sensitivity on TEST for shipped features, plus validation-tuned threshold
sens={}
for cont in [0.01,0.02,0.03,0.05,0.08,0.10,'auto']:
    rows=[]
    for sd in range(10):
        m=fit(cols,cont,sd); p=(m.predict(D['te'][cols].to_numpy())==-1).astype(int); rows.append(prf(D['te'].is_anomaly,p)+(p.mean(),))
    a=np.array(rows); sens[str(cont)]=dict(P=a[:,0].mean(),R=a[:,1].mean(),F1=a[:,2].mean(),F1_sd=a[:,2].std(),flag_rate=a[:,3].mean())
    print('cont',cont,sens[str(cont)],flush=True)
out['contamination_sensitivity_test']=sens
# validation-tuned threshold (quantile of anomaly score) - does not use the true injection rate as a parameter
res_t=[];qs=np.linspace(0.90,0.995,40)
for sd in range(10):
    m=fit(cols,'auto',sd); sv=-m.score_samples(D['va'][cols].to_numpy()); yv=D['va'].is_anomaly.to_numpy()
    f1s=[prf(yv,(sv>=np.quantile(sv,q)).astype(int))[2] for q in qs]; q=qs[int(np.argmax(f1s))]; thr=np.quantile(sv,q)
    st=-m.score_samples(D['te'][cols].to_numpy()); yt=D['te'].is_anomaly.to_numpy(); p=(st>=thr).astype(int)
    res_t.append(prf(yt,p)+(q,roc_auc_score(yt,st),average_precision_score(yt,st)))
a=np.array(res_t); out['val_tuned_threshold_test']=dict(P=a[:,0].mean(),R=a[:,1].mean(),F1=a[:,2].mean(),F1_sd=a[:,2].std(),q=a[:,3].mean(),AUROC=a[:,4].mean(),AP=a[:,5].mean())
print(out['val_tuned_threshold_test'],flush=True)
# 3) baselines on test (threshold tuned on validation): |z| rule, amount-only rule
def tune(score_va,yv):
    qs=np.linspace(0.90,0.995,60); f=[prf(yv,(score_va>=np.quantile(score_va,q)).astype(int))[2] for q in qs]; return np.quantile(score_va,qs[int(np.argmax(f))])
yv=D['va'].is_anomaly.to_numpy(); yt=D['te'].is_anomaly.to_numpy()
bl={}
for name,fn in [('|z| rule',lambda d:np.abs(d.amount_zscore.to_numpy())),('amount rule',lambda d:d.amount.to_numpy())]:
    thr=tune(fn(D['va']),yv); s=fn(D['te']); p=(s>=thr).astype(int); bl[name]=dict(zip(['P','R','F1'],prf(yt,p)),AUROC=roc_auc_score(yt,s),AP=average_precision_score(yt,s))
m=fit(cols,'auto',0); s=-m.score_samples(D['te'][cols].to_numpy()); bl['IF (amount, z), seed 0']=dict(AUROC=roc_auc_score(yt,s),AP=average_precision_score(yt,s))
out['baselines_test']=bl; print(bl,flush=True)
# 4) user-bootstrap CI for the shipped configuration (contamination 0.05, seed 17 as shipped) -- resample test users
m=fit(cols,0.05,17); D['te']=D['te'].assign(pred=(m.predict(D['te'][cols].to_numpy())==-1).astype(int))
g={uid:(d.is_anomaly.to_numpy(),d.pred.to_numpy()) for uid,d in D['te'].groupby('user_id')}
ks=list(g); rb=np.random.default_rng(0); F=[]
for _ in range(1000):
    pick=rb.choice(len(ks),len(ks)); y=np.concatenate([g[ks[i]][0] for i in pick]); p=np.concatenate([g[ks[i]][1] for i in pick]); F.append(prf(y,p))
F=np.array(F); out['shipped_test_ci']=dict(n_test_users=len(te),n_test_txns=len(D['te']),n_anoms=int(yt.sum()),P=[F[:,0].mean(),*np.percentile(F[:,0],[2.5,97.5])],R=[F[:,1].mean(),*np.percentile(F[:,1],[2.5,97.5])],F1=[F[:,2].mean(),*np.percentile(F[:,2],[2.5,97.5])])
print(out['shipped_test_ci'])
out['hyperparameters']=dict(n_estimators=100,max_samples='auto (256)',max_features=1.0,bootstrap=False,contamination=0.05,random_state=17)
out['split']=dict(train=len(tr),val=len(va),test=len(te),rows=dict((k,len(v)) for k,v in D.items()))
json.dump(out,open('/home/claude/j2work/res_anom.json','w'),indent=1,default=float)
