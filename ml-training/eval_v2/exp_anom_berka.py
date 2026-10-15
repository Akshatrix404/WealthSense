import sys,json,warnings; warnings.filterwarnings('ignore')
sys.path.insert(0,'/home/claude/wealthsense/ml-training')
import numpy as np, pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_recall_fscore_support as prfs, roc_auc_score, average_precision_score
import train_anomaly_detector as T
t=pd.read_pickle('/home/claude/j2work/berka_trans.pkl')
t=t[t.type.isin(['VYDAJ','VYBER'])].copy()
t['category']=t.operation.fillna('')+'/'+t.k_symbol.fillna('').str.strip()
t=t.rename(columns={'account_id':'user_id'})[['user_id','date','amount','category']]
r=np.random.default_rng(3); accts=r.permutation(t.user_id.unique())[:300]; t=t[t.user_id.isin(accts)].copy()
t['is_credit']=False; t['is_anomaly']=0
# semi-synthetic injection into REAL histories: 3% of debits, log-uniform multiplier 3x-20x of the account's category median
idx=r.choice(t.index.to_numpy(),int(0.03*len(t)),replace=False)
med=t.groupby(['user_id','category']).amount.transform('median')
mult=np.exp(r.uniform(np.log(3),np.log(20),len(idx)))
t.loc[idx,'amount']=(med.loc[idx]*mult).round(2); t.loc[idx,'is_anomaly']=1
print(len(t),t.is_anomaly.sum(),flush=True)
fd=T.build_features(t)
u=r.permutation(fd.user_id.unique()); n=len(u); tr=set(u[:int(.7*n)]); te=set(u[int(.85*n):]); va=set(u[int(.7*n):int(.85*n)])
cols=['amount','amount_zscore']; d={k:fd[fd.user_id.isin(s)] for k,s in [('tr',tr),('va',va),('te',te)]}
yt=d['te'].is_anomaly.to_numpy(); res={}
for cont in [0.03,0.05,'auto']:
    f=[]
    for sd in range(5):
        m=IsolationForest(n_estimators=100,contamination=cont,random_state=sd,n_jobs=1).fit(d['tr'][cols].to_numpy())
        p=(m.predict(d['te'][cols].to_numpy())==-1).astype(int); f.append(list(prfs(yt,p,average='binary',zero_division=0)[:3])+[p.mean()])
    f=np.array(f); res[str(cont)]=dict(P=f[:,0].mean(),R=f[:,1].mean(),F1=f[:,2].mean(),flag=f[:,3].mean())
m=IsolationForest(n_estimators=100,contamination='auto',random_state=0).fit(d['tr'][cols].to_numpy()); s=-m.score_samples(d['te'][cols].to_numpy())
res['IF_AUROC']=roc_auc_score(yt,s); res['IF_AP']=average_precision_score(yt,s)
for nm,c in [('amount rule',d['te'].amount.to_numpy()),('|z| rule',np.abs(d['te'].amount_zscore.to_numpy()))]:
    res[nm+' AUROC']=roc_auc_score(yt,c); res[nm+' AP']=average_precision_score(yt,c)
res['n']=dict(accounts=len(accts),txns=len(t),anoms=int(t.is_anomaly.sum()),test_txns=len(d['te']),test_anoms=int(yt.sum()))
print(json.dumps(res,indent=1,default=float)); json.dump(res,open('res_anom_berka.json','w'),indent=1,default=float)
