import numpy as np, pandas as pd
W,H=30,7
# ---------- data loaders: return dict user -> (bal[d], flow[d]) daily end-of-day balance and net flow
def load_berka(n_accounts=1500, min_span=730, seed=0):
    t=pd.read_pickle('/home/claude/j2work/berka_trans.pkl')
    g=t.groupby('account_id')
    span=g.date.agg(lambda s:(s.max()-s.min()).days+1)
    ok=span[span>=min_span].index.to_numpy()
    rng=np.random.default_rng(seed); ok=rng.permutation(ok)[:n_accounts]
    out={}
    sub=t[t.account_id.isin(ok)]
    for a,d in sub.groupby('account_id'):
        last=d.groupby('date').balance.last()
        idx=pd.date_range(last.index.min(),last.index.max())
        bal=last.reindex(idx).ffill().to_numpy(dtype=float)
        out[int(a)]=(bal,np.diff(bal,prepend=bal[0]))
    return out
def load_synth():
    df=pd.read_csv('/home/claude/wealthsense/ml-training/data/synthetic_transactions.csv')
    df['date']=pd.to_datetime(df['date']); df['s']=np.where(df.is_credit,df.amount,-df.amount)
    out={}
    full=pd.date_range(df.date.min(),df.date.max())
    for u,d in df.groupby('user_id'):
        f=d.groupby('date').s.sum().reindex(full,fill_value=0.0).to_numpy()
        out[u]=(np.cumsum(f),f)
    return out
def split_users(users,seed=21,fr=(0.7,0.15)):
    r=np.random.default_rng(seed); u=r.permutation(sorted(users)); n=len(u)
    a=int(n*fr[0]); b=int(n*(fr[0]+fr[1])); return list(u[:a]),list(u[a:b]),list(u[b:])
# ---------- windows (app-faithful: per-window z-score on cumulative relative balance and flow)
def raw_windows(series,users,stride=1,max_per_user=None,rng=None):
    """returns list arrays: bal (N,W) raw balance, flow (N,W), target raw bal at +H, uid"""
    B,F,Y,U=[],[],[],[]
    for u in users:
        bal,flow=series[u]; n=len(bal); starts=np.arange(0,n-W-H+1,stride)
        if max_per_user and len(starts)>max_per_user: starts=np.sort(rng.choice(starts,max_per_user,replace=False))
        for s in starts:
            B.append(bal[s:s+W]); F.append(flow[s:s+W]); Y.append(bal[s+W-1+H]); U.append(u)
    return np.array(B),np.array(F),np.array(Y),np.array(U)
def norm_window(B,F):
    rel=B-B[:,:1]
    floor=np.maximum(0.02*np.abs(B).mean(1,keepdims=True),1.0)   # robust floor: flat windows would otherwise give huge z-scores
    bm,bs=rel.mean(1,keepdims=True),np.maximum(rel.std(1,keepdims=True),floor)
    fm,fs=F.mean(1,keepdims=True),np.maximum(F.std(1,keepdims=True),1e-6)
    X=np.stack([(rel-bm)/bs,(F-fm)/fs],-1).astype(np.float32)
    return X,bm[:,0],bs[:,0]
def targets(B,Y,bm,bs):
    rel_t=Y-B[:,0]; return ((rel_t-bm)/bs).astype(np.float32)
def to_money(pn,B,bm,bs):
    pred_rel=pn*bs+bm; delta=pred_rel-(B[:,-1]-B[:,0]); return B[:,-1]+delta
# ---------- baselines on raw windows -> predicted balance at +H
def naive(B,F): return B[:,-1]
def drift(B,F): return B[:,-1]+H*F.mean(1)
def snaive7(B,F): return B[:,-1]+F[:,-7:].sum(1)
def snaive30(B,F): return B[:,-1]+F[:,:7].sum(1)   # same offset one window (30 days) earlier
def mean_flow7(B,F): return B[:,-1]+H*F[:,-7:].mean(1)
def metrics_user(pred,Y,U):
    e=np.abs(pred-Y); df=pd.DataFrame({'u':U,'e':e}); return df.groupby('u').e.mean()
def boot_ci(per_user,n=2000,seed=0):
    r=np.random.default_rng(seed); v=per_user.to_numpy(); m=[r.choice(v,len(v)).mean() for _ in range(n)]
    return v.mean(),np.percentile(m,2.5),np.percentile(m,97.5)
def paired_boot(a,b,n=2000,seed=0):
    r=np.random.default_rng(seed); d=(a-b).to_numpy(); m=[r.choice(d,len(d)).mean() for _ in range(n)]
    return d.mean(),np.percentile(m,2.5),np.percentile(m,97.5)
