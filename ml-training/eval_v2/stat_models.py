import numpy as np, warnings
warnings.filterwarnings('ignore')
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.ar_model import AutoReg
H=7
def ar5(B,F):  # AR(5) on net flow, sum of 7-step forecast
    out=[]
    for f in F:
        try:
            m=AutoReg(f,lags=5,trend='c').fit(); v=m.predict(len(f),len(f)+H-1).sum()
            out.append(v if np.isfinite(v) and abs(v)<=7*np.abs(f).max() else f.mean()*H)
        except Exception: out.append(f[-7:].sum())
    return B[:,-1]+np.array(out)
def arima(B,F): # ARIMA(1,1,1) on balance
    out=[]
    for b in B:
        try:
            m=ARIMA(b,order=(1,1,1)).fit(); out.append(m.forecast(H)[-1])
        except Exception: out.append(b[-1])
    return np.array(out)
def ets(B,F): # additive damped-trend exponential smoothing on balance
    out=[]
    for b in B:
        try:
            m=ExponentialSmoothing(b,trend='add',damped_trend=True).fit(); out.append(m.forecast(H)[-1])
        except Exception: out.append(b[-1])
    return np.array(out)
