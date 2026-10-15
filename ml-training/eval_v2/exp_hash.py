import time, json, itertools, os, sys
sys.path.insert(0,'/home/claude/wealthsense/backend')
from Crypto.Hash import keccak
import commitment as C
def k256(b): return keccak.new(digest_bits=256,data=b).digest()
cats=["Food & Dining","Shopping","Entertainment","Transport","Health","Bills","Groceries","Travel"]
# v1 target: amount in paise, category, timestamp
target_amt, target_cat, target_ts = 42000,"Food & Dining",1791800142
h=k256(f"{target_amt}|{target_cat}|{target_ts}".encode())
# Attack space: attacker knows time within a 60-second window (e.g. from block time), 8 public categories,
# amounts up to Rs 500.00 in paise (50,000 values) -> 50,000 * 8 * 60 = 24,000,000 candidates
ts0=target_ts-30; t=time.time(); n=0; found=None
for ts in range(ts0,ts0+60):
    for cat in cats:
        pre=f"|{cat}|{ts}".encode()
        for a in range(0,50000):
            n+=1
            if k256(str(a).encode()+pre)==h: found=(a,cat,ts); break
        if found: break
    if found: break
dt=time.time()-t
rate=n/dt
space_full=1_000_000*8*600  # Rs 10,000 at paise resolution, 8 categories, 10-minute window
res=dict(found=found,candidates_tested=n,seconds=dt,rate_per_s=rate,
 full_space=space_full,est_hours_single_cpu_core=space_full/rate/3600)
# HMAC: attacker without K cannot test candidates; demonstrate that a wrong-key guess never matches and key space size
K=os.urandom(32)
c=C.commitment(K,target_amt,target_cat,target_ts,"Swiggy","EXPENSE")
res['hmac_commitment']=c.hex()
res['hmac_match_with_wrong_key']=C.verify(os.urandom(32),c,target_amt,target_cat,target_ts,"Swiggy","EXPENSE")
res['hmac_match_with_right_key']=C.verify(K,c,target_amt,target_cat,target_ts,"Swiggy","EXPENSE")
res['hmac_tamper_detected']=not C.verify(K,c,target_amt+100,target_cat,target_ts,"Swiggy","EXPENSE")
# unambiguity: length-prefix encoding distinguishes ("ab","c") from ("a","bc")
res['encoding_unambiguous']=C.canonical_message(1,"ab",2,"c","T")!=C.canonical_message(1,"a",2,"bc","T")
# fixed test vector
vk=bytes(range(32)); res['test_vector']=C.commitment(vk,42000,"Food & Dining",1791800142,"Swiggy","EXPENSE").hex()
json.dump(res,open('res_hash.json','w'),indent=1); print(json.dumps(res,indent=1))
