"""
Trains an Isolation Forest anomaly detector on the labeled synthetic dataset
(ml-training/data/synthetic_transactions_with_anomalies.csv), evaluates it
against the known ground-truth labels, and saves the trained model + a
fitted feature encoder for export.

FEATURE SET -- empirically reduced from the original 4-feature spec, not
matching it as originally proposed. The spec called for "amount, category,
day-of-week, rolling merchant-frequency." Initial results with all 4
(category one-hot-encoded, merchant-frequency substituted with
category-frequency -- see note below) were poor: F1 = 0.325, with recall
on amount_spike anomalies at just 9.3%. Diagnosis: raw amount alone can't
tell "Rs 3,000 is unusual for Food" from "Rs 3,000 is normal for
Rent/EMI" -- so a category-conditional amount_zscore feature was added
(how many std-devs this amount is from THIS user's own historical mean
for THIS category, using only prior transactions, no leakage). That
alone roughly doubled F1 to 0.694, but a subsequent ablation across
feature subsets (documented in the project's ml-training README) showed
that adding one-hot category, day-of-week, or category-frequency back in
each independently HURT performance (down to 0.34-0.61) -- likely because
Isolation Forest picks a feature uniformly at random at each split, so
diluting the feature space with several near-uninformative dimensions
reduces how often the model splits on the two genuinely informative ones.

Final feature set, chosen by evidence not by the original spec:
  - amount (raw rupees)
  - amount_zscore (category-conditional unusualness, see above)

"rolling merchant-frequency" -> not used in the final model (empirically
hurt performance), but category_freq is still computed and left in the
feature-engineering code for transparency/future experimentation.

Reads:  ml-training/data/synthetic_transactions_with_anomalies.csv
Writes: ml-training/models/isolation_forest.joblib   (trained sklearn model)
        ml-training/eval_output/anomaly_precision_recall.txt
        ml-training/eval_output/anomaly_scatter.png

Run:
    ./venv/bin/python3 train_anomaly_detector.py
"""

import os
import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "synthetic_transactions_with_anomalies.csv")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
EVAL_DIR = os.path.join(os.path.dirname(__file__), "eval_output")

MODEL_PATH = os.path.join(MODEL_DIR, "isolation_forest.joblib")

SEED = 17
TRAIN_FRAC = 0.70
# Known true injection rate from generate_anomaly_labels.py -- used as the
# IsolationForest contamination parameter. In a real deployment this would
# be a rough estimate rather than a known ground truth, but using the true
# value here gives an honest best-case evaluation of the algorithm itself,
# separate from contamination-estimation error.
CONTAMINATION = 0.05
N_ESTIMATORS = 100  # matches the paper's default (Liu et al., 2008)

CATEGORY_FREQ_WINDOW_DAYS = 30  # trailing window for the frequency feature

rng = np.random.default_rng(SEED)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Builds the feature set for each expense transaction:
      - amount (raw rupees)
      - category (one-hot, fit on TRAIN data only, see main())
      - day_of_week (0=Monday .. 6=Sunday)
      - category_freq: fraction of this user's transactions in the trailing
        30 days that were in the same category as this transaction
        (rolling merchant-frequency substitute, see module docstring)
      - amount_zscore: how many standard deviations this amount is from
        THIS USER's own historical mean for THIS CATEGORY, using only
        transactions strictly before this one (expanding window, no
        leakage). This feature was added after diagnosing a real recall
        problem: raw amount alone can't distinguish "Rs 3,000 is unusual
        for Food" from "Rs 3,000 is totally normal for Rent/EMI" -- a
        flat amount feature gets swamped by legitimately-large categories
        like Rent/EMI, so amount_spike anomalies (which are defined
        relative to a user's own per-category mean, see
        generate_anomaly_labels.py) were going undetected. This feature
        encodes that category-conditional relationship directly.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["day_of_week"] = df["date"].dt.dayofweek

    df = df.sort_values(["user_id", "date"]).reset_index(drop=True)

    freqs = np.zeros(len(df))
    zscores = np.zeros(len(df))

    for user_id, group in df.groupby("user_id"):
        idx = group.index.to_numpy()
        dates = group["date"].to_numpy()
        cats = group["category"].to_numpy()
        amounts = group["amount"].to_numpy()

        for i in range(len(idx)):
            # --- category_freq (unchanged) ---
            window_start = dates[i] - np.timedelta64(CATEGORY_FREQ_WINDOW_DAYS, "D")
            in_window = (dates < dates[i]) & (dates >= window_start)
            if in_window.sum() == 0:
                freqs[idx[i]] = 0.0
            else:
                same_cat = (cats[in_window] == cats[i]).sum()
                freqs[idx[i]] = same_cat / in_window.sum()

            # --- amount_zscore: expanding window, same category, strictly
            # prior transactions only ---
            prior_same_cat = (dates < dates[i]) & (cats == cats[i])
            prior_amounts = amounts[prior_same_cat]
            if len(prior_amounts) < 2:
                # Not enough history for this user+category yet -- neutral
                # z-score rather than a misleading extreme value.
                zscores[idx[i]] = 0.0
            else:
                mean = prior_amounts.mean()
                std = max(prior_amounts.std(), 1e-6)
                zscores[idx[i]] = (amounts[i] - mean) / std

    df["category_freq"] = freqs
    df["amount_zscore"] = zscores
    return df


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(EVAL_DIR, exist_ok=True)

    print(f"Loading {DATA_PATH} ...")
    raw = pd.read_csv(DATA_PATH)
    expenses = raw[~raw["is_credit"]].copy()
    print(f"Expense transactions: {len(expenses):,} "
          f"({expenses['is_anomaly'].sum():,} labeled anomalous)")

    print("Building features (amount, category, day-of-week, "
          "rolling category-frequency) ...")
    features_df = build_features(expenses)

    # --- Split BY USER, same principle as the forecaster: no user's
    # transactions cross the train/test boundary. ---
    all_users = sorted(features_df["user_id"].unique())
    shuffled = rng.permutation(all_users)
    n_train = int(len(shuffled) * TRAIN_FRAC)
    train_users = set(shuffled[:n_train])
    test_users = set(shuffled[n_train:])

    train_df = features_df[features_df["user_id"].isin(train_users)]
    test_df = features_df[features_df["user_id"].isin(test_users)]
    print(f"Users: {len(train_users)} train / {len(test_users)} test")
    print(f"Rows:  {len(train_df):,} train / {len(test_df):,} test")

    # --- Feature matrix: amount + amount_zscore only (see module
    # docstring for the ablation evidence behind this choice). category
    # and day_of_week are still computed above for transparency/future
    # experimentation but are NOT fed to the model. ---
    FEATURE_COLS = ["amount", "amount_zscore"]

    def to_feature_matrix(d: pd.DataFrame) -> np.ndarray:
        return d[FEATURE_COLS].to_numpy()

    X_train = to_feature_matrix(train_df)
    X_test = to_feature_matrix(test_df)
    y_test = test_df["is_anomaly"].to_numpy()

    print(f"\nFeature matrix shape: train {X_train.shape}, test {X_test.shape}")

    # --- Train. Isolation Forest is trained on the FULL train set,
    # including its ~5% naturally-occurring anomalies -- this matches how
    # it will actually be used in production (scoring live, unlabeled
    # transactions), and the original iForest paper (Liu et al., 2008,
    # Section 5.4) shows it degrades only slightly with contamination
    # present in training versus training on normal-only data. ---
    print(f"\nTraining IsolationForest (n_estimators={N_ESTIMATORS}, "
          f"contamination={CONTAMINATION}) ...")
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        random_state=SEED,
        n_jobs=-1,
    )
    model.fit(X_train)

    # --- Evaluate on held-out test set against KNOWN ground truth. ---
    # sklearn's predict() returns -1 for anomaly, 1 for normal.
    raw_pred = model.predict(X_test)
    y_pred = (raw_pred == -1).astype(int)

    precision = precision_score(y_test, y_pred, zero_division=0)
    recall = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()

    print("\n" + "=" * 60)
    print("EVALUATION (held-out test set, known ground truth)")
    print("=" * 60)
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1:        {f1:.4f}")
    print(f"\nConfusion matrix:")
    print(f"  TN={tn:,}  FP={fp:,}")
    print(f"  FN={fn:,}  TP={tp:,}")
    print(f"\nTotal test transactions: {len(y_test):,}, "
          f"actual anomalies: {y_test.sum():,}, "
          f"flagged anomalies: {y_pred.sum():,}")

    # Save metrics to a text file for the report (Prompt 7 deliverable)
    metrics_path = os.path.join(EVAL_DIR, "anomaly_precision_recall.txt")
    with open(metrics_path, "w") as f:
        f.write("Isolation Forest -- Held-Out Test Set Evaluation\n")
        f.write("=" * 50 + "\n")
        f.write(f"Test users: {len(test_users)}\n")
        f.write(f"Test transactions: {len(y_test):,}\n")
        f.write(f"Actual anomalies: {int(y_test.sum()):,}\n\n")
        f.write(f"Precision: {precision:.4f}\n")
        f.write(f"Recall:    {recall:.4f}\n")
        f.write(f"F1:        {f1:.4f}\n\n")
        f.write(f"Confusion Matrix:\n")
        f.write(f"              Predicted Normal  Predicted Anomaly\n")
        f.write(f"Actual Normal      {tn:>10,}        {fp:>10,}\n")
        f.write(f"Actual Anomaly     {fn:>10,}        {tp:>10,}\n")
    print(f"\nSaved metrics -> {metrics_path}")

    # 2D scatter of the two model features (amount, amount_zscore),
    # normal vs flagged anomalies (Prompt 7 deliverable).
    plot_scatter(X_test, y_test, y_pred, FEATURE_COLS)

    # --- Save model ---
    joblib.dump(model, MODEL_PATH)
    print(f"\nSaved model -> {MODEL_PATH}")
    print(f"Feature set used: {FEATURE_COLS}")
    print("=" * 60)


def plot_scatter(X_test, y_true, y_pred, feature_names):
    # amount_zscore can blow up to extreme values when a (user, category)
    # pair has very low historical variance (guarded at a 1e-6 minimum std
    # to avoid divide-by-zero) -- a handful of these outliers were
    # squashing the entire plot into an unreadable flat line on a linear
    # y-axis. symlog keeps them visible without hiding the bulk of the
    # data near zero.
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    ax = axes[0]
    ax.scatter(X_test[y_true == 0, 0], X_test[y_true == 0, 1], s=8, alpha=0.4,
               color="#4F46E5", label="Normal (true)")
    ax.scatter(X_test[y_true == 1, 0], X_test[y_true == 1, 1], s=14, alpha=0.8,
               color="#DC2626", label="Anomalous (true)")
    ax.set_yscale("symlog")
    ax.set_title("Ground Truth")
    ax.set_xlabel(feature_names[0])
    ax.set_ylabel(f"{feature_names[1]} (symlog scale)")
    ax.legend(fontsize=9)

    ax = axes[1]
    ax.scatter(X_test[y_pred == 0, 0], X_test[y_pred == 0, 1], s=8, alpha=0.4,
               color="#4F46E5", label="Normal (predicted)")
    ax.scatter(X_test[y_pred == 1, 0], X_test[y_pred == 1, 1], s=14, alpha=0.8,
               color="#DC2626", label="Flagged anomalous (predicted)")
    ax.set_yscale("symlog")
    ax.set_title("Isolation Forest Predictions")
    ax.set_xlabel(feature_names[0])
    ax.set_ylabel(f"{feature_names[1]} (symlog scale)")
    ax.legend(fontsize=9)

    fig.suptitle("Isolation Forest: Normal vs Anomalous Transactions",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.94])

    out_path = os.path.join(EVAL_DIR, "anomaly_scatter.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
