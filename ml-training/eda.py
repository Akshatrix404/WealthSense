"""
Quick manual EDA pass on the synthetic dataset before trusting it to train
on. Uses the anomaly-labeled dataset (a superset of the clean data) so all
three plots come from one consistent source.

Produces 3 PNGs in ml-training/eda_output/:
  1. daily_spend_3_users.png    -- daily spend over 90 days, 3 random users
  2. category_spend_distribution.png -- total spend by category
  3. amount_histogram_anomalies.png  -- transaction amount histogram,
                                          anomalies highlighted

Run:
    ./venv/bin/python3 eda.py
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless, no display needed
import matplotlib.pyplot as plt

DATA_PATH = os.path.join(
    os.path.dirname(__file__), "data", "synthetic_transactions_with_anomalies.csv"
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "eda_output")
SEED = 3

rng = np.random.default_rng(SEED)


def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    return df


def plot_daily_spend_3_users(df: pd.DataFrame):
    """(1) Daily spend per user over 90 days, for 3 random users."""
    expenses = df[~df["is_credit"]]
    all_users = expenses["user_id"].unique()
    chosen_users = rng.choice(all_users, size=3, replace=False)

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle("Daily Spend Over 90 Days -- 3 Random Users", fontsize=14)

    for ax, user_id in zip(axes, chosen_users):
        user_df = expenses[expenses["user_id"] == user_id]
        daily = user_df.groupby("date").agg(
            total_spend=("amount", "sum"),
            has_anomaly=("is_anomaly", "max"),
        ).reindex(pd.date_range(df["date"].min(), df["date"].max()), fill_value=0)
        daily["has_anomaly"] = daily["has_anomaly"].fillna(0)

        ax.plot(daily.index, daily["total_spend"], color="#4F46E5", linewidth=1, label="Daily spend")
        anomaly_days = daily[daily["has_anomaly"] == 1]
        if len(anomaly_days) > 0:
            ax.scatter(anomaly_days.index, anomaly_days["total_spend"],
                       color="#DC2626", zorder=5, s=30, label="Day with an anomaly")

        ax.set_title(f"{user_id}", fontsize=10, loc="left")
        ax.set_ylabel("Spend (Rs)")
        ax.legend(loc="upper right", fontsize=8)

    axes[-1].set_xlabel("Date")
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    out_path = os.path.join(OUTPUT_DIR, "daily_spend_3_users.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")
    print(f"  Users plotted: {list(chosen_users)}")


def plot_category_spend_distribution(df: pd.DataFrame):
    """(2) Category spend distribution (total amount, expenses only)."""
    expenses = df[~df["is_credit"]]
    cat_totals = expenses.groupby("category")["amount"].sum().sort_values(ascending=False)

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ["#4F46E5" if amt > 0 else "#999999" for amt in cat_totals.values]
    ax.barh(cat_totals.index[::-1], cat_totals.values[::-1], color=colors)
    ax.set_xlabel("Total Spend (Rs)")
    ax.set_title("Category Spend Distribution (all expense transactions,\nincl. injected anomalies)")

    for i, (cat, amt) in enumerate(cat_totals.items()):
        ax.text(amt, len(cat_totals) - 1 - i, f"  Rs {amt:,.0f}", va="center", fontsize=8)

    fig.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "category_spend_distribution.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")


def plot_amount_histogram_with_anomalies(df: pd.DataFrame):
    """(3) Histogram of transaction amounts, anomalies highlighted."""
    expenses = df[~df["is_credit"]]
    normal = expenses[expenses["is_anomaly"] == 0]["amount"]
    anomalous = expenses[expenses["is_anomaly"] == 1]["amount"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left: full range (log scale on x, since anomalies stretch it far)
    ax = axes[0]
    bins = np.logspace(np.log10(max(normal.min(), 1)), np.log10(expenses["amount"].max()), 60)
    ax.hist(normal, bins=bins, color="#4F46E5", alpha=0.7, label=f"Normal (n={len(normal):,})")
    ax.hist(anomalous, bins=bins, color="#DC2626", alpha=0.7, label=f"Anomalous (n={len(anomalous):,})")
    ax.set_xscale("log")
    ax.set_xlabel("Transaction Amount (Rs, log scale)")
    ax.set_ylabel("Count")
    ax.set_title("Full range (log scale)")
    ax.legend(fontsize=9)

    # Right: zoomed to typical daily-spend range, so the "normal" shape is visible
    ax = axes[1]
    zoom_max = normal.quantile(0.99)
    ax.hist(normal[normal <= zoom_max], bins=60, color="#4F46E5", alpha=0.7, label="Normal")
    ax.hist(anomalous[anomalous <= zoom_max], bins=60, color="#DC2626", alpha=0.7, label="Anomalous")
    ax.set_xlabel(f"Transaction Amount (Rs, zoomed to <= Rs {zoom_max:,.0f})")
    ax.set_ylabel("Count")
    ax.set_title("Zoomed to typical range")
    ax.legend(fontsize=9)

    fig.suptitle("Transaction Amount Distribution: Normal vs Anomalous", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.94])

    out_path = os.path.join(OUTPUT_DIR, "amount_histogram_anomalies.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")
    print(f"  Normal median: Rs {normal.median():,.2f}, Anomalous median: Rs {anomalous.median():,.2f}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df = load_data()
    print(f"Loaded {len(df):,} rows from {DATA_PATH}\n")

    plot_daily_spend_3_users(df)
    plot_category_spend_distribution(df)
    plot_amount_histogram_with_anomalies(df)

    print(f"\nAll plots saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
