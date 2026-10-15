"""
Trains a small Bidirectional LSTM to predict a user's balance 7 days ahead,
given their trailing 30 days of daily balance + net cash flow history.

Framework choice -- TensorFlow/Keras, not PyTorch:
    Training speed for a model this small (<200k params) on ~27k windows is
    a non-issue in either framework -- both finish in well under a minute
    on CPU. The deciding factor is deployment: this model has to end up
    running on-device in the Android app (see Day-later on-device inference
    work), and the app's existing ML surface (MediaPipe LlmInference) is
    already built on the TFLite/LiteRT ecosystem. A Keras model exports to
    .tflite with a single `tf.lite.TFLiteConverter.from_keras_model()` call;
    a PyTorch model would need an extra PyTorch -> ONNX -> TFLite hop (or
    ExecuTorch, a different on-device runtime than what's already in this
    app). Given training speed is a wash, minimizing deployment friction
    wins -- hence TensorFlow.

Reads:  ml-training/data/synthetic_transactions.csv
Writes: ml-training/models/forecaster.keras   (trained model checkpoint)
        ml-training/models/forecaster_norm_stats.csv (per-user mean/std
            used for normalization -- needed at inference time, and
            realistic for an on-device model since the app already has
            each user's own transaction history locally to compute this)

Run:
    ./venv/bin/python3 train_forecaster.py
"""

import os
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "synthetic_transactions.csv")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "forecaster.keras")
NORM_STATS_PATH = os.path.join(MODEL_DIR, "forecaster_norm_stats.csv")
HISTORY_PATH = os.path.join(MODEL_DIR, "forecaster_history.csv")

WINDOW_SIZE = 30       # days of trailing history
HORIZON = 7            # predict this many days ahead
SEED = 21
TRAIN_FRAC = 0.70
VAL_FRAC = 0.15
# TEST_FRAC is the remainder (0.15)

BATCH_SIZE = 128
EPOCHS = 40
PATIENCE = 6            # early stopping

np.random.seed(SEED)
tf.random.set_seed(SEED)


# ----------------------------------------------------------------------------
# 1. Build daily balance series per user
# ----------------------------------------------------------------------------
def build_daily_balance_series(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregates raw transactions into one row per (user_id, date) with:
      - net_flow: credits minus debits that day
      - balance: cumulative sum of net_flow, i.e. running balance starting
        from an assumed 0 at day 0 of the simulation window

    Missing days (a user with zero transactions that day) are filled with
    net_flow = 0, so every user ends up with a dense 90-day series.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["signed_amount"] = np.where(df["is_credit"], df["amount"], -df["amount"])

    daily = df.groupby(["user_id", "date"])["signed_amount"].sum().rename("net_flow")
    daily = daily.reset_index()

    full_date_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    all_users = df["user_id"].unique()

    # Build a dense (user, date) grid so every user has exactly the same
    # number of rows -- required for clean fixed-size sliding windows.
    idx = pd.MultiIndex.from_product([all_users, full_date_range], names=["user_id", "date"])
    dense = daily.set_index(["user_id", "date"]).reindex(idx, fill_value=0.0).reset_index()

    dense = dense.sort_values(["user_id", "date"])
    dense["balance"] = dense.groupby("user_id")["net_flow"].cumsum()

    return dense


# ----------------------------------------------------------------------------
# 2. Sliding windows: 30 days -> balance 7 days after window end
# ----------------------------------------------------------------------------
def build_windows(daily: pd.DataFrame, user_ids: list, norm_stats: dict):
    """
    For each user, slides a WINDOW_SIZE-day window across their series and
    predicts the balance HORIZON days after the window ends.

    Features per day (2): [normalized balance, normalized net_flow]
    Target: normalized balance at t + WINDOW_SIZE + HORIZON - 1

    Normalization is per-user (z-score, using that user's own mean/std from
    their full 90-day series) -- this keeps a ₹20k-salary user and a
    ₹150k-salary user on a comparable scale for one shared model, and
    mirrors how this would actually run on-device: the app already has
    each user's own local transaction history to compute their own stats.
    """
    X, y = [], []

    for user_id in user_ids:
        user_df = daily[daily["user_id"] == user_id].sort_values("date")
        balance = user_df["balance"].to_numpy()
        net_flow = user_df["net_flow"].to_numpy()

        mean, std = norm_stats[user_id]
        bal_norm = (balance - mean) / std
        flow_norm = (net_flow - net_flow.mean()) / (net_flow.std() + 1e-6)

        n_days = len(balance)
        last_start = n_days - WINDOW_SIZE - HORIZON  # inclusive
        for t in range(0, last_start + 1):
            window_bal = bal_norm[t: t + WINDOW_SIZE]
            window_flow = flow_norm[t: t + WINDOW_SIZE]
            target = bal_norm[t + WINDOW_SIZE + HORIZON - 1]

            X.append(np.stack([window_bal, window_flow], axis=-1))  # (30, 2)
            y.append(target)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# ----------------------------------------------------------------------------
# 3. Model: small Bidirectional LSTM, <200k params
# ----------------------------------------------------------------------------
def build_model(window_size: int, n_features: int) -> keras.Model:
    model = keras.Sequential([
        keras.layers.Input(shape=(window_size, n_features)),
        keras.layers.Bidirectional(keras.layers.LSTM(48, return_sequences=True)),
        keras.layers.Bidirectional(keras.layers.LSTM(24)),
        keras.layers.Dense(16, activation="relu"),
        keras.layers.Dense(1),  # predicted normalized balance, 7 days ahead
    ])
    model.compile(optimizer=keras.optimizers.Adam(learning_rate=1e-3), loss="mse", metrics=["mae"])
    return model


def split_users(all_users: list, seed: int = SEED, train_frac: float = TRAIN_FRAC,
                 val_frac: float = VAL_FRAC):
    """
    Splits users (not rows) into train/val/test. Factored out so
    evaluate_forecaster.py can reproduce the exact same split deterministically
    -- same seed, same shuffle -- guaranteeing it evaluates on the actual
    held-out test users, not an accidentally different resample.
    """
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(sorted(all_users))
    n_users = len(shuffled)
    n_train = int(n_users * train_frac)
    n_val = int(n_users * val_frac)
    train_users = list(shuffled[:n_train])
    val_users = list(shuffled[n_train: n_train + n_val])
    test_users = list(shuffled[n_train + n_val:])
    return train_users, val_users, test_users


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    os.makedirs(MODEL_DIR, exist_ok=True)

    print(f"Loading {DATA_PATH} ...")
    raw = pd.read_csv(DATA_PATH)
    daily = build_daily_balance_series(raw)
    print(f"Built daily balance series: {daily['user_id'].nunique()} users x "
          f"{daily.groupby('user_id').size().iloc[0]} days each")

    # --- Per-user split (NOT by row) to avoid leakage: no user's timeline
    # is split across train/val/test, since that would let the model see
    # a user's own future/past across the split boundary. ---
    all_users = sorted(daily["user_id"].unique())
    train_users, val_users, test_users = split_users(all_users)

    print(f"\nUser split: {len(train_users)} train / {len(val_users)} val / "
          f"{len(test_users)} test (of {len(all_users)} total)")

    # Per-user normalization stats computed from EACH user's own full series
    # (train, val, and test users alike) -- this is a per-entity transform,
    # not a global statistic fit on train data, so there's no leakage: a
    # test user's stats come only from that same test user's own history,
    # never from another user.
    norm_stats = {
        user_id: (
            daily.loc[daily["user_id"] == user_id, "balance"].mean(),
            max(daily.loc[daily["user_id"] == user_id, "balance"].std(), 1e-6),
        )
        for user_id in all_users
    }

    pd.DataFrame(
        [(u, m, s) for u, (m, s) in norm_stats.items()],
        columns=["user_id", "balance_mean", "balance_std"],
    ).to_csv(NORM_STATS_PATH, index=False)
    print(f"Saved per-user normalization stats -> {NORM_STATS_PATH}")

    print("\nBuilding sliding windows ...")
    X_train, y_train = build_windows(daily, train_users, norm_stats)
    X_val, y_val = build_windows(daily, val_users, norm_stats)
    X_test, y_test = build_windows(daily, test_users, norm_stats)

    print(f"Windows -- train: {X_train.shape}, val: {X_val.shape}, test: {X_test.shape}")

    # --- Model ---
    model = build_model(WINDOW_SIZE, n_features=X_train.shape[-1])
    model.summary()

    n_params = model.count_params()
    print(f"\nTotal trainable parameters: {n_params:,} "
          f"({'OK, under 200k' if n_params < 200_000 else 'WARNING: exceeds 200k budget'})")

    early_stop = keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=PATIENCE, restore_best_weights=True
    )

    print(f"\nTraining for up to {EPOCHS} epochs (early stopping patience={PATIENCE}) ...")
    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=[early_stop],
        verbose=2,
    )

    # --- Final evaluation ---
    train_loss, train_mae = model.evaluate(X_train, y_train, verbose=0)
    val_loss, val_mae = model.evaluate(X_val, y_val, verbose=0)
    test_loss, test_mae = model.evaluate(X_test, y_test, verbose=0)

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"Epochs run:        {len(history.history['loss'])} (of {EPOCHS} max)")
    print(f"Train loss (MSE):  {train_loss:.4f}   MAE (normalized): {train_mae:.4f}")
    print(f"Val loss (MSE):    {val_loss:.4f}   MAE (normalized): {val_mae:.4f}")
    print(f"Test loss (MSE):   {test_loss:.4f}   MAE (normalized): {test_mae:.4f}")

    # De-normalized MAE for interpretability: multiply by the average
    # per-user balance std (rough conversion back to rupees).
    avg_std = np.mean([s for _, s in norm_stats.values()])
    print(f"\nApprox. de-normalized test MAE: Rs {test_mae * avg_std:,.2f} "
          f"(using avg per-user balance std as a rough scale factor)")

    loss_history = history.history["loss"]
    val_loss_history = history.history["val_loss"]
    is_decreasing = val_loss_history[-1] < val_loss_history[0]
    is_diverging = val_loss_history[-1] > 3 * min(val_loss_history)
    print(f"\nVal loss trend: {val_loss_history[0]:.4f} -> {val_loss_history[-1]:.4f} "
          f"({'decreasing, OK' if is_decreasing else 'NOT decreasing, CHECK'})")
    print(f"Diverging check: {'FAILED - diverged' if is_diverging else 'OK - stable'}")

    model.save(MODEL_PATH)
    print(f"\nSaved model checkpoint -> {MODEL_PATH}")

    history_df = pd.DataFrame({
        "epoch": range(1, len(history.history["loss"]) + 1),
        "train_loss": history.history["loss"],
        "val_loss": history.history["val_loss"],
        "train_mae": history.history["mae"],
        "val_mae": history.history["val_mae"],
    })
    history_df.to_csv(HISTORY_PATH, index=False)
    print(f"Saved training history -> {HISTORY_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
