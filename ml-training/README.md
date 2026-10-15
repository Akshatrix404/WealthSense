# ml-training

Training pipeline for WealthSense's transaction categorization / forecasting
models. Isolated Python venv — **not** wired into the Gradle build in any way.

## Setup

```bash
cd ml-training
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

(Windows: `venv\Scripts\pip install -r requirements.txt`)

## Synthetic forecast dataset

```bash
./venv/bin/python3 generate_forecast_data.py
```

Generates `data/synthetic_transactions.csv` — 500 simulated Indian users ×
90 days, with realistic patterns:

- **Rent/EMI**: fixed amount, fixed day of month, per user
- **Food & Drinks**: 2-4 small transactions/day (₹80-400 range)
- **Shopping / Entertainment**: irregular, weekend-biased
- **Salary**: one income credit/month
- **Electricity / Mobile Recharge**: monthly, amount varies ±15-40%

Deterministic (`SEED = 42`) — re-running regenerates an identical file, so
the CSV doesn't strictly need to be committed, but it's checked in here for
convenience/reproducibility while there's no training pipeline consuming it
yet.

Columns: `user_id, date, amount, category, is_credit`

The script prints sanity-check stats after generating (avg monthly spend
per user, category distribution by amount and by count). As of the last
run: **Rent/EMI is the largest expense category by amount** (~16%,
narrowly ahead of Food & Drinks), and **Food & Drinks is by far the most
frequent category by transaction count** (~89% of all rows) — both
expected for realistic spend data.

## Labeled anomaly dataset (for Isolation Forest, Day 10)

```bash
./venv/bin/python3 generate_anomaly_labels.py
```

Reads `data/synthetic_transactions.csv`, writes a **copy** with anomalies
injected: `data/synthetic_transactions_with_anomalies.csv` (the clean file
is untouched, and the forecaster can keep training on it).

5% of expense transactions are replaced with anomalies, split ~50/50:
- **`amount_spike`**: the transaction's amount is replaced with 5-10x that
  user's own typical amount for that category
- **`novel_category`**: the transaction is moved into one of 6 categories
  that never appear anywhere in the base generator (Electronics, Travel,
  Medical Emergency, Jewelry, Furniture, Insurance Claim Payout) — this
  needed a disjoint category pool because every simulated user naturally
  touches all 6 base categories within 90 days, so there was no genuinely
  "unused" category to borrow from within the base set

Ground truth columns added: `is_anomaly` (0/1), `anomaly_type` (string,
empty for normal rows).

## EDA

```bash
./venv/bin/python3 eda.py
```

Manual eyeball pass before trusting the data — 3 PNGs in `eda_output/`,
all sourced from the anomaly-labeled dataset so both models eventually
train against one consistent ground truth:

1. `daily_spend_3_users.png` — 90-day daily spend for 3 random users,
   anomaly days marked in red
2. `category_spend_distribution.png` — total spend by category
3. `amount_histogram_anomalies.png` — log-scale + zoomed amount
   histograms, normal vs anomalous

Confirmed by eye: anomalies clearly stand apart in all three (anomalous
median ~₹2,880 vs normal median ~₹198, roughly 14.6x).

## Forecaster: Bidirectional LSTM (30-day history -> 7-day-ahead balance)

```bash
./venv/bin/python3 train_forecaster.py
```

Trains on `data/synthetic_transactions.csv` (the clean dataset, not the
anomaly-labeled copy — forecasting normal balance trajectory and detecting
anomalies are different tasks, kept on separate but consistently-sourced
data).

- **Framework**: TensorFlow/Keras, not PyTorch — justified in a code
  comment at the top of the script. Training speed is a non-issue for a
  model this small; the deciding factor is TFLite export simplicity for
  eventual on-device deployment, matching the app's existing
  MediaPipe/TFLite ecosystem.
- **Features per day**: per-user-normalized balance + per-user-normalized
  net cash flow (z-score, each user normalized against their own 90-day
  stats — realistic for on-device inference, since the app already has
  each user's own local history to compute this).
- **Windowing**: 30 days trailing history -> balance 7 days after the
  window ends. ~54 windows/user, ~27k total across 500 users.
- **Split**: by user (350 train / 75 val / 75 test), not by row — no
  single user's timeline crosses a split boundary, so there's no leakage
  of a user's own future into their own training data.
- **Architecture**: 2 stacked Bidirectional LSTM layers (48, then 24
  units) + Dense(16) + Dense(1). **43,617 params**, well under the 200k
  on-device budget.
- **Training**: Adam, MSE loss, early stopping on val loss (patience 6,
  best weights restored). Stopped at epoch 35/40 — val loss went
  0.1825 -> 0.0703 (best), stable, no divergence.

Outputs:
- `models/forecaster.keras` — trained checkpoint (reload-tested: loads
  cleanly and runs inference)
- `models/forecaster_norm_stats.csv` — per-user (mean, std) needed at
  inference time to de-normalize predictions

## Evaluating the forecaster on held-out test data

```bash
./venv/bin/python3 evaluate_forecaster.py
```

Loads the trained checkpoint, reproduces the **exact same test split**
`train_forecaster.py` used (same seed, same `split_users()` function,
imported directly — not re-derived), and runs real inference on those
75 held-out users (4,050 windows) to get a defensible rupee-space MAE,
not just the rough normalized-space estimate `train_forecaster.py`
prints.

**Results:**
- **Model MAE: Rs 2,879.03** | **RMSE: Rs 4,561.83**
- Naive baseline (predict "no change from last known balance"):
  MAE Rs 17,398.24 — the model beats this by **83.5%**
- Prediction std (Rs 78,910) vs actual std (Rs 79,250) — ratio 1.00,
  i.e. **not flat-lining toward the mean**

The script also auto-flags a bad model (variance ratio < 0.3, or <5%
improvement over the naive baseline) and prints 2-3 concrete
architecture/hyperparameter suggestions in that case — not needed here,
since this model clears both bars comfortably.

Outputs in `eval_output/`:
- `loss_curve.png` — train/val loss over 35 epochs, best-epoch marked
- `predicted_vs_actual.png` — 3 sample test users, predicted (dashed)
  vs actual (solid) balance over time, per-user MAE annotated. Visually
  confirmed: the model tracks the real balance curve closely, including
  catching the sharp salary-credit jumps — not a flat line.

## Exporting to TFLite (on-device)

```bash
./venv/bin/python3 export_forecaster_tflite.py
```

**A real conversion obstacle came up, worth knowing about:** Keras's
Bidirectional LSTM uses a dynamic `TensorListReserve` mechanism internally
that TFLite's converter can't lower to static ops directly. Two fixes
exist with a real tradeoff:
- **Select TF ops (Flex delegate)**: converts fine, small file (~100KB),
  but full-integer (int8 *activation*) quantization is impossible this
  way (the calibrator can't execute Flex ops), and it adds the
  `org.tensorflow:tensorflow-lite-select-tf-ops` dependency (~7-8MB) to
  the Android app.
- **Unroll the LSTM** (`unroll=True`, both layers, weights copied from
  the trained checkpoint — verified mathematically identical, 0.0 max
  prediction diff): avoids the dynamic ops entirely, enabling true full
  int8 (weights + activations) with the standard TFLite runtime, no Flex
  delegate needed. Bigger file (~688KB, since unrolling duplicates the
  cell across all 30 timesteps before quantization helps), but
  self-contained — the better trade for this app.

The script uses the unroll approach, with Select-ops as an automatic
fallback if that ever fails.

**Then the int8 quantization delta check genuinely failed** on the first
attempt — max delta Rs 4,296 against a Rs 500 threshold (chosen as a
small fraction of the model's own ~Rs 2,879 test MAE), comparable to the
model's *entire* error budget. Root cause: per-user z-score normalization
means int8's 256 discretization levels get multiplied back out by each
user's balance std (often tens of thousands of rupees) when
de-normalizing — small int8 rounding becomes large rupee error. The
script generated a float16 build to compare, found its delta was Rs 3.83
(essentially lossless), and **automatically promoted float16 to be the
shipped `forecaster.tflite`** rather than leaving a model that fails its
own accuracy check as the named deliverable. The int8 attempt is kept for
reference at `forecaster_int8_reference.tflite`, clearly labeled as not
the shipped model.

**Final shipped artifact**: `exports/forecaster.tflite` — float16,
467.7 KB, standard TFLite runtime (no Flex delegate), sanity-checked
against the full-precision model on 3 held-out test samples (max delta
Rs 3.83), and independently re-verified by loading it fresh and running
inference through it (not just trusting the export call succeeded).

## Isolation Forest anomaly detector

```bash
./venv/bin/python3 train_anomaly_detector.py
```

**Feature set was empirically reduced, not spec-matched.** Original spec:
amount, category, day-of-week, rolling merchant-frequency. First attempt
with all 4 (category one-hot, merchant-frequency substituted with
category-frequency since this dataset has no merchant field) scored
**F1 = 0.325**, with only 9.3% recall on `amount_spike` anomalies.
Diagnosis: raw amount alone can't tell "Rs 3,000 is unusual for Food"
from "Rs 3,000 is normal for Rent/EMI" — Isolation Forest picks a
feature uniformly at random per split, so padding the feature space with
several under-informative dimensions diluted how often it split on
anything useful.

Fix: added `amount_zscore` (how many std-devs this amount is from THIS
user's own historical mean for THIS category, computed from strictly
prior transactions only — no leakage). An ablation across feature
subsets then showed category, day-of-week, and category-frequency each
independently **hurt** performance when added back in (down to
F1 0.34–0.61). Final model uses just two features:

- `amount` (raw rupees)
- `amount_zscore` (category-conditional unusualness)

**Results** (held-out test set, 150 users, 45,059 transactions, 2,267
true anomalies):

| Metric | Value |
|---|---|
| Precision | 0.7083 |
| Recall | 0.6811 |
| F1 | 0.6944 |

Per-type recall: `amount_spike` 75.0% (up from 9.3% before the fix),
`novel_category` 61.1%.

Outputs:
- `models/isolation_forest.joblib` — trained model
- `eval_output/anomaly_precision_recall.txt` — metrics + confusion matrix
- `eval_output/anomaly_scatter.png` — normal vs flagged, symlog-scaled
  (a handful of near-zero-variance category histories produce extreme
  z-score outliers that broke a linear-scale plot on the first attempt)

## Status

- [x] Synthetic dataset generator (forecast data)
- [x] Labeled anomaly dataset (for Isolation Forest)
- [x] EDA pass (3 PNGs, visually confirmed)
- [x] Forecaster: Bidirectional LSTM (30d -> 7d-ahead balance)
- [x] Forecaster evaluation on held-out test set (real rupee MAE/RMSE,
      loss curve + predicted-vs-actual PNGs, visually confirmed)
- [x] Forecaster exported to TFLite, on-device-ready, sanity-checked
      (float16 -- int8 was tried, failed its own accuracy check, and
      was correctly NOT shipped)
- [x] Isolation Forest anomaly detector: trained, evaluated on held-out
      test set (F1 0.694), feature set empirically derived via ablation
- [ ] Isolation Forest exported (ONNX) + Android integration
- [ ] On-device inference integration for the forecaster (Kotlin side)

Currently, WealthSense's Android app categorizes transactions using a
keyword lookup table and a user-authored rule engine (see
`../docs/sms-to-room-pipeline.md`) — there is no trained ML model yet. This
module is where that changes.

## Update (stricter evaluation)

The first set of numbers in this README (MAE Rs 2,879, F1 0.694) used whole-series normalisation
statistics for the forecaster and an oracle contamination setting for the Isolation Forest. See
`eval_v2/` and the paper for the corrected protocol. Under the app's real 30-day-window
normalisation the originally shipped weights give MAE Rs 9,346; the model retrained with that
normalisation (`retrain_window_norm.py`, now bundled as the app asset) has test MAE Rs 5,402
(no-change: Rs 17,398). The `eval_v2` scripts contain absolute paths from the authoring machine;
edit them before running.
