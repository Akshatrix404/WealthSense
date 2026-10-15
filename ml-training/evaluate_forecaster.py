"""
Evaluates the trained forecaster checkpoint on the held-out TEST split
(never seen during training or early-stopping decisions) -- this is what
turns the training script's rough, normalized-space MAE estimate into a
real, defensible rupee number.

Reuses build_daily_balance_series / split_users / build_windows directly
from train_forecaster.py, so the test set here is *guaranteed* to be the
exact same held-out users the training script excluded -- not a
re-shuffled, accidentally-different sample.

Reads:  ml-training/models/forecaster.keras
        ml-training/models/forecaster_norm_stats.csv
        ml-training/models/forecaster_history.csv
        ml-training/data/synthetic_transactions.csv
Writes: ml-training/eval_output/loss_curve.png
        ml-training/eval_output/predicted_vs_actual.png

Run:
    ./venv/bin/python3 evaluate_forecaster.py
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from tensorflow import keras

from train_forecaster import (
    build_daily_balance_series,
    split_users,
    build_windows,
    WINDOW_SIZE,
    HORIZON,
    DATA_PATH,
    MODEL_PATH,
    NORM_STATS_PATH,
    HISTORY_PATH,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "eval_output")
SEED = 5
N_SAMPLE_USERS = 3

rng = np.random.default_rng(SEED)

# Threshold for flagging a suspiciously bad model: if the trained model's
# rupee MAE isn't at least this much better than a trivial "predict no
# change from the last known balance" baseline, something's wrong --
# either it's flat-lining to the mean, or it hasn't actually learned the
# 7-day dynamics.
MIN_IMPROVEMENT_OVER_NAIVE = 0.05  # model MAE must beat naive by >=5%


# ----------------------------------------------------------------------------
# Rebuild the exact same test split + windows the training script used
# ----------------------------------------------------------------------------
def load_test_data():
    raw = pd.read_csv(DATA_PATH)
    daily = build_daily_balance_series(raw)

    all_users = sorted(daily["user_id"].unique())
    _, _, test_users = split_users(all_users)  # same SEED/fracs as training

    norm_stats_df = pd.read_csv(NORM_STATS_PATH)
    norm_stats = {
        row.user_id: (row.balance_mean, row.balance_std)
        for row in norm_stats_df.itertuples()
    }

    X_test, y_test = build_windows(daily, test_users, norm_stats)

    # Rebuild parallel (user_id, target_date) metadata in the SAME order
    # build_windows produced its rows in (same user list, same loop bounds),
    # so we can align rupee de-normalization and per-user plotting without
    # having to change build_windows's return signature.
    meta = []
    for user_id in test_users:
        user_df = daily[daily["user_id"] == user_id].sort_values("date")
        dates = user_df["date"].to_numpy()
        n_days = len(dates)
        last_start = n_days - WINDOW_SIZE - HORIZON
        for t in range(0, last_start + 1):
            target_date = dates[t + WINDOW_SIZE + HORIZON - 1]
            meta.append((user_id, target_date))

    assert len(meta) == len(X_test), (
        f"Metadata/window count mismatch ({len(meta)} vs {len(X_test)}) -- "
        f"test split or windowing logic has diverged from train_forecaster.py"
    )

    return daily, test_users, norm_stats, X_test, y_test, meta


# ----------------------------------------------------------------------------
# De-normalize predictions/targets back to rupees, per-window
# ----------------------------------------------------------------------------
def denormalize(y_norm: np.ndarray, meta: list, norm_stats: dict) -> np.ndarray:
    means = np.array([norm_stats[u][0] for u, _ in meta])
    stds = np.array([norm_stats[u][1] for u, _ in meta])
    return y_norm.flatten() * stds + means


# ----------------------------------------------------------------------------
# Naive baseline: predict "balance 7 days from now == balance at end of
# the 30-day input window" (i.e. no change). A model that can't beat this
# hasn't learned anything about the actual 7-day dynamics.
# ----------------------------------------------------------------------------
def naive_baseline_rupee(X_test_norm: np.ndarray, meta: list, norm_stats: dict) -> np.ndarray:
    last_day_balance_norm = X_test_norm[:, -1, 0]  # feature 0 = normalized balance
    means = np.array([norm_stats[u][0] for u, _ in meta])
    stds = np.array([norm_stats[u][1] for u, _ in meta])
    return last_day_balance_norm * stds + means


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading test data (same split as training) ...")
    daily, test_users, norm_stats, X_test, y_test_norm, meta = load_test_data()
    print(f"Test users: {len(test_users)}, test windows: {len(X_test)}")

    print(f"Loading model checkpoint -> {MODEL_PATH}")
    model = keras.models.load_model(MODEL_PATH)

    print("Running inference on test split ...")
    pred_norm = model.predict(X_test, verbose=0)

    actual_rupee = denormalize(y_test_norm, meta, norm_stats)
    pred_rupee = denormalize(pred_norm, meta, norm_stats)
    naive_rupee = naive_baseline_rupee(X_test, meta, norm_stats)

    # --- 2. MAE / RMSE in rupees ---
    errors = pred_rupee - actual_rupee
    mae = np.mean(np.abs(errors))
    rmse = np.sqrt(np.mean(errors ** 2))

    naive_errors = naive_rupee - actual_rupee
    naive_mae = np.mean(np.abs(naive_errors))
    naive_rmse = np.sqrt(np.mean(naive_errors ** 2))

    print("\n" + "=" * 60)
    print("TEST SET EVALUATION (rupees)")
    print("=" * 60)
    print(f"Model MAE:   Rs {mae:,.2f}")
    print(f"Model RMSE:  Rs {rmse:,.2f}")
    print(f"\nNaive baseline (predict no change from last known balance):")
    print(f"Naive MAE:   Rs {naive_mae:,.2f}")
    print(f"Naive RMSE:  Rs {naive_rmse:,.2f}")

    improvement = 1 - (mae / naive_mae) if naive_mae > 0 else 0
    print(f"\nModel improvement over naive baseline: {100 * improvement:.1f}%")

    # Prediction variance check: a model that's flat-lined to roughly the
    # per-user mean will have near-zero prediction variance relative to
    # the actual variance, even if MAE looks superficially OK.
    pred_std = np.std(pred_rupee)
    actual_std = np.std(actual_rupee)
    variance_ratio = pred_std / actual_std if actual_std > 0 else 0
    print(f"\nPrediction std: Rs {pred_std:,.2f}   Actual std: Rs {actual_std:,.2f}   "
          f"Ratio: {variance_ratio:.2f}")

    # --- 4. Flag + suggest fixes if the model looks bad ---
    is_flat_lined = variance_ratio < 0.3
    beats_naive = improvement >= MIN_IMPROVEMENT_OVER_NAIVE
    print("\n" + "-" * 60)
    if is_flat_lined or not beats_naive:
        print("WARNING: model quality looks questionable.")
        if is_flat_lined:
            print(f"  - Prediction variance is only {100*variance_ratio:.0f}% of actual "
                  f"variance -- predictions likely flat-lining toward the mean.")
        if not beats_naive:
            print(f"  - Model MAE (Rs {mae:,.0f}) doesn't clearly beat the naive "
                  f"'no change' baseline (Rs {naive_mae:,.0f}) -- the model may not "
                  f"have learned the actual 7-day dynamics.")
        print("\nSuggested next steps:")
        print("  1. Increase model capacity slightly (e.g. LSTM units 48->64, or add")
        print("     a 3rd stacked layer) -- still comfortably under the 200k budget.")
        print("  2. Add explicit calendar features (day-of-week, day-of-month) as extra")
        print("     input channels -- rent/EMI and salary are calendar-driven, and the")
        print("     model currently has to infer that purely from the raw balance/flow.")
        print("  3. Increase training epochs / reduce early-stopping patience less")
        print("     aggressively, or lower the learning rate (e.g. 1e-3 -> 3e-4) once")
        print("     val loss plateaus, to squeeze out remaining gains.")
    else:
        print(f"Model looks reasonable: beats naive baseline by {100*improvement:.1f}%, "
              f"prediction variance is {100*variance_ratio:.0f}% of actual variance.")
    print("-" * 60)

    # --- 3a. Loss curve PNG ---
    plot_loss_curve()

    # --- 3b. Predicted vs actual PNG, 3 sample users, MAE annotated ---
    plot_predicted_vs_actual(daily, model, test_users, norm_stats, mae)

    print(f"\nAll PNGs saved to {OUTPUT_DIR}/")
    print("=" * 60)


def plot_loss_curve():
    history_df = pd.read_csv(HISTORY_PATH)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(history_df["epoch"], history_df["train_loss"], color="#4F46E5", label="Train loss")
    ax.plot(history_df["epoch"], history_df["val_loss"], color="#DC2626", label="Val loss")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Loss (MSE, normalized space)")
    ax.set_title("Forecaster Training: Loss Over Epochs")
    ax.legend()
    ax.grid(alpha=0.3)

    best_epoch = history_df["val_loss"].idxmin()
    best_val = history_df["val_loss"].min()
    ax.scatter([history_df["epoch"][best_epoch]], [best_val], color="#DC2626", zorder=5, s=60,
               edgecolor="black", label="Best val loss")
    ax.annotate(f"best val_loss={best_val:.4f}\n(epoch {history_df['epoch'][best_epoch]})",
                xy=(history_df["epoch"][best_epoch], best_val),
                xytext=(10, 20), textcoords="offset points", fontsize=9,
                arrowprops=dict(arrowstyle="->", color="black"))

    fig.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "loss_curve.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")


def plot_predicted_vs_actual(daily, model, test_users, norm_stats, overall_mae):
    sample_users = rng.choice(test_users, size=N_SAMPLE_USERS, replace=False)

    fig, axes = plt.subplots(N_SAMPLE_USERS, 1, figsize=(12, 10), sharex=False)

    for ax, user_id in zip(axes, sample_users):
        user_df = daily[daily["user_id"] == user_id].sort_values("date")
        dates = user_df["date"].to_numpy()
        balance = user_df["balance"].to_numpy()
        net_flow = user_df["net_flow"].to_numpy()

        mean, std = norm_stats[user_id]
        bal_norm = (balance - mean) / std
        flow_norm = (net_flow - net_flow.mean()) / (net_flow.std() + 1e-6)

        n_days = len(balance)
        last_start = n_days - WINDOW_SIZE - HORIZON

        X_user, target_dates, actual_vals = [], [], []
        for t in range(0, last_start + 1):
            window_bal = bal_norm[t: t + WINDOW_SIZE]
            window_flow = flow_norm[t: t + WINDOW_SIZE]
            X_user.append(np.stack([window_bal, window_flow], axis=-1))
            target_dates.append(dates[t + WINDOW_SIZE + HORIZON - 1])
            actual_vals.append(balance[t + WINDOW_SIZE + HORIZON - 1])

        X_user = np.array(X_user, dtype=np.float32)
        pred_norm_user = model.predict(X_user, verbose=0).flatten()
        pred_vals = pred_norm_user * std + mean

        user_mae = np.mean(np.abs(np.array(pred_vals) - np.array(actual_vals)))

        ax.plot(target_dates, actual_vals, color="#111827", linewidth=1.8, label="Actual balance")
        ax.plot(target_dates, pred_vals, color="#4F46E5", linewidth=1.8, linestyle="--",
                label="Predicted balance (7d ahead)")
        ax.set_title(f"{user_id}  --  user MAE: Rs {user_mae:,.0f}", fontsize=10, loc="left")
        ax.set_ylabel("Balance (Rs)")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(alpha=0.3)

    axes[-1].set_xlabel("Date")
    fig.suptitle(f"Predicted vs Actual Balance (7-day-ahead) -- 3 Sample Test Users\n"
                 f"Overall test set MAE: Rs {overall_mae:,.2f}", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.93])

    out_path = os.path.join(OUTPUT_DIR, "predicted_vs_actual.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"Saved {out_path}")
    print(f"  Sample users: {list(sample_users)}")


if __name__ == "__main__":
    main()
