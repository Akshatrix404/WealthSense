# eval_v2: stricter evaluation (answers to reviewer-style criticisms)

* `berka_prep.py` – builds the real-data table from the public Berka dataset (not included; see the paper).
* `exp_forecast.py <berka|synth> 5` – 12 forecasting methods, 5 seeds, account-level bootstrap CIs.
* `exp_norm.py` – effect of whole-series vs 30-day-window normalisation on the originally shipped weights.
* `exp_anom.py`, `exp_anom2.py` – Isolation Forest: validation-only ablation, contamination sensitivity, rule baselines, user-level CIs.
* `exp_anom_berka.py` – semi-synthetic anomaly injection into real Berka debits.
* `exp_hash.py` – brute-force recovery of the v1 hash and checks of the v2 HMAC commitment.
* `exp_latency.py` – host-CPU timing proxy (not a phone).
* `results/` – the JSON outputs used in the paper.
