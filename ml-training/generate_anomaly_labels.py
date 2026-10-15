"""
Injects labeled anomalous transactions into a COPY of the synthetic
forecast dataset, for training/evaluating an anomaly detector (Isolation
Forest, Day 10) against known ground truth.

Reads:  ml-training/data/synthetic_transactions.csv
Writes: ml-training/data/synthetic_transactions_with_anomalies.csv
        (original file is untouched)

Anomaly types injected (roughly 50/50 split of the 5% injected rows):
  A) "amount_spike" -- existing transaction's amount replaced with
     5-10x that user's typical amount for that category
  B) "novel_category" -- a transaction added in a category that user
     has never had in their (clean) history, at a plausible amount for
     that category (drawn from the global category distribution, since
     by definition this user has no personal history for it)

Only EXPENSE transactions (is_credit == False) are eligible for
injection -- injecting a fake "anomalous salary" doesn't map onto what
an anomaly detector for spend behavior should be catching.

Run:
    ./venv/bin/python3 generate_anomaly_labels.py
"""

import os
import numpy as np
import pandas as pd

INPUT_PATH = os.path.join(os.path.dirname(__file__), "data", "synthetic_transactions.csv")
OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__), "data", "synthetic_transactions_with_anomalies.csv"
)

ANOMALY_FRACTION = 0.05  # of expense transactions
SPIKE_MULTIPLIER_RANGE = (5, 10)
SEED = 7

# Categories that NEVER appear in generate_forecast_data.py's output.
# Needed because every simulated user naturally touches all 6 base expense
# categories (Rent/EMI, Food & Drinks, Shopping, Entertainment, Electricity,
# Mobile Recharge) within a 90-day window -- there's no genuinely "unused"
# category among the base set to inject as a novelty anomaly. Pulling from
# a disjoint pool guarantees the injected transaction really is a category
# that user's clean history has zero precedent for.
NOVEL_CATEGORY_POOL = {
    "Electronics": (5000, 2500),
    "Travel": (8000, 4000),
    "Medical Emergency": (6000, 3500),
    "Jewelry": (12000, 6000),
    "Furniture": (7000, 3000),
    "Insurance Claim Payout": (15000, 5000),  # note: unused elsewhere, kept as expense-style outlier
}

rng = np.random.default_rng(SEED)


def main():
    df = pd.read_csv(INPUT_PATH)
    df["is_anomaly"] = 0
    df["anomaly_type"] = ""  # empty for normal rows; kept for inspection, not a model feature

    expense_mask = ~df["is_credit"]
    expense_idx = df[expense_mask].index.to_numpy()

    n_anomalies = int(round(len(expense_idx) * ANOMALY_FRACTION))
    anomaly_idx = rng.choice(expense_idx, size=n_anomalies, replace=False)

    # Split roughly 50/50 between the two anomaly types.
    rng.shuffle(anomaly_idx)
    split = n_anomalies // 2
    spike_idx = anomaly_idx[:split]
    novel_idx = anomaly_idx[split:]

    # Precompute per-user-per-category typical amount (mean), from the
    # CLEAN data, so injected spikes are relative to real behavior.
    user_category_mean = (
        df[expense_mask].groupby(["user_id", "category"])["amount"].mean().to_dict()
    )

    # --- Type A: amount spike ---
    for idx in spike_idx:
        user_id = df.at[idx, "user_id"]
        category = df.at[idx, "category"]
        base_amount = user_category_mean.get((user_id, category))
        if base_amount is None or base_amount <= 0:
            base_amount = df.at[idx, "amount"]  # fallback, shouldn't normally happen
        multiplier = rng.uniform(*SPIKE_MULTIPLIER_RANGE)
        df.at[idx, "amount"] = round(base_amount * multiplier, 2)
        df.at[idx, "is_anomaly"] = 1
        df.at[idx, "anomaly_type"] = "amount_spike"

    # --- Type B: novel category ---
    novel_category_names = list(NOVEL_CATEGORY_POOL.keys())
    for idx in novel_idx:
        new_category = rng.choice(novel_category_names)
        mean, std = NOVEL_CATEGORY_POOL[new_category]
        amount = max(50.0, rng.normal(mean, std))

        df.at[idx, "category"] = new_category
        df.at[idx, "amount"] = round(amount, 2)
        df.at[idx, "is_anomaly"] = 1
        df.at[idx, "anomaly_type"] = "novel_category"

    df.to_csv(OUTPUT_PATH, index=False)

    print(f"Read {len(pd.read_csv(INPUT_PATH)):,} rows from {INPUT_PATH}")
    print(f"Injected {n_anomalies:,} anomalies "
          f"({len(spike_idx):,} amount_spike, {len(novel_idx):,} novel_category)")
    print(f"Wrote {len(df):,} rows -> {OUTPUT_PATH}\n")

    print_sanity_checks(df)


def print_sanity_checks(df: pd.DataFrame):
    print("=" * 60)
    print("ANOMALY LABEL SANITY CHECKS")
    print("=" * 60)

    total = len(df)
    n_anom = int(df["is_anomaly"].sum())
    print(f"\nTotal rows: {total:,}")
    print(f"Anomalies:  {n_anom:,} ({100 * n_anom / total:.2f}% of all rows)")

    expense_total = int((~df["is_credit"]).sum())
    print(f"Anomalies as % of expense rows: {100 * n_anom / expense_total:.2f}% "
          f"(target: {100 * ANOMALY_FRACTION:.1f}%)")

    print("\n--- Anomaly type breakdown ---")
    print(df[df["is_anomaly"] == 1]["anomaly_type"].value_counts().to_string())

    print("\n--- Amount comparison: normal vs anomalous ---")
    normal_amt = df[df["is_anomaly"] == 0]["amount"].mean()
    anomaly_amt = df[df["is_anomaly"] == 1]["amount"].mean()
    print(f"Avg normal transaction amount:   Rs {normal_amt:,.2f}")
    print(f"Avg anomalous transaction amount: Rs {anomaly_amt:,.2f}")
    print(f"Ratio: {anomaly_amt / normal_amt:.1f}x")

    print("\n--- Per-category anomaly rate (expense categories) ---")
    expense_df = df[~df["is_credit"]]
    cat_anomaly_rate = expense_df.groupby("category")["is_anomaly"].mean().sort_values(ascending=False)
    for cat, rate in cat_anomaly_rate.items():
        print(f"  {cat:<20s} {100 * rate:5.2f}%")

    print("=" * 60)


if __name__ == "__main__":
    main()
