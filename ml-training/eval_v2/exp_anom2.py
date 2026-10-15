import re
src=open('/home/claude/j2work/exp_anom.py').read().split("out={}")[0]
exec(src)
import json
res={}
for k in ['amount+z (shipped)','amount+z+dow','amount','all four']:
    cols=subsets[k]; rows=[]
    for sd in range(10):
        m=fit(cols,0.05,sd); p=(m.predict(D['te'][cols].to_numpy())==-1).astype(int); rows.append(prf(D['te'].is_anomaly,p))
    a=np.array(rows); res[k]=dict(P=a[:,0].mean(),R=a[:,1].mean(),F1=a[:,2].mean(),F1_sd=a[:,2].std()); print(k,res[k],flush=True)
json.dump(res,open('/home/claude/j2work/res_anom2.json','w'),indent=1,default=float)
