"""
Exports the trained Isolation Forest to ONNX and proves it actually works
before it touches Kotlin.

Framework choice -- ONNX, not TFLite: unlike the forecaster (a Keras
Sequential model with a native, well-supported TFLite export path),
scikit-learn's IsolationForest has no first-class TFLite converter --
TFLite's tooling targets TensorFlow graphs, and tree-ensemble models like
IsolationForest don't map onto that cleanly. skl2onnx, by contrast, has
explicit, well-maintained support for exporting IsolationForest to ONNX
(it's one of scikit-learn's most common converter targets). Shipping this
means adding `onnxruntime-android` to the Android app rather than reusing
the forecaster's `org.tensorflow:tensorflow-lite` dependency -- two
inference runtimes in one app is a real cost, but it's the honest
consequence of two structurally different model types (a Keras RNN vs a
scikit-learn tree ensemble), not an oversight. Worth stating plainly in
the report rather than pretending both models share one pipeline.

Reads:  ml-training/models/isolation_forest.joblib
        ml-training/data/synthetic_transactions_with_anomalies.csv
Writes: ml-training/exports/isolation_forest.onnx

Run:
    ./venv/bin/python3 export_anomaly_detector_onnx.py
"""

import os
import numpy as np
import pandas as pd
import joblib
import onnxruntime as ort
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

from train_anomaly_detector import build_features, TRAIN_FRAC, rng, MODEL_PATH, DATA_PATH

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
ONNX_PATH = os.path.join(EXPORT_DIR, "isolation_forest.onnx")

FEATURE_COLS = ["amount", "amount_zscore"]
N_SANITY_SAMPLES = 10


def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)

    print(f"Loading model -> {MODEL_PATH}")
    model = joblib.load(MODEL_PATH)

    print("Converting to ONNX ...")
    initial_type = [("input", FloatTensorType([None, len(FEATURE_COLS)]))]
    onnx_model = convert_sklearn(
        model, initial_types=initial_type,
        target_opset={"": 17, "ai.onnx.ml": 3},
        options={id(model): {"score_samples": True}},
    )

    with open(ONNX_PATH, "wb") as f:
        f.write(onnx_model.SerializeToString())

    file_size_kb = os.path.getsize(ONNX_PATH) / 1024
    print(f"Saved -> {ONNX_PATH} ({file_size_kb:.1f} KB)")

    # --- Sanity check: compare sklearn predictions vs ONNX Runtime
    # predictions on real held-out test samples, not synthetic noise. ---
    print(f"\nLoading test data for a real sanity check ...")
    raw = pd.read_csv(DATA_PATH)
    expenses = raw[~raw["is_credit"]].copy()
    features_df = build_features(expenses)

    all_users = sorted(features_df["user_id"].unique())
    shuffled = rng.permutation(all_users)
    n_train = int(len(shuffled) * TRAIN_FRAC)
    test_users = set(shuffled[n_train:])
    test_df = features_df[features_df["user_id"].isin(test_users)]

    sample_idx = np.random.default_rng(3).choice(len(test_df), size=N_SANITY_SAMPLES, replace=False)
    X_sample = test_df.iloc[sample_idx][FEATURE_COLS].to_numpy().astype(np.float32)

    sklearn_pred = model.predict(X_sample)  # -1 anomaly, 1 normal
    sklearn_scores = model.decision_function(X_sample)

    session = ort.InferenceSession(ONNX_PATH)
    input_name = session.get_inputs()[0].name
    output_names = [o.name for o in session.get_outputs()]
    print(f"ONNX model outputs: {output_names}")

    onnx_outputs = session.run(None, {input_name: X_sample})
    onnx_pred = onnx_outputs[0].flatten()

    print("\n" + "=" * 70)
    print(f"{'Sample':<8}{'amount':<12}{'zscore':<12}{'sklearn pred':<15}{'ONNX pred':<12}{'Match':<8}")
    print("=" * 70)
    all_match = True
    for i in range(N_SANITY_SAMPLES):
        match = int(sklearn_pred[i]) == int(onnx_pred[i])
        all_match = all_match and match
        print(f"{i:<8}{X_sample[i][0]:<12.2f}{X_sample[i][1]:<12.4f}"
              f"{int(sklearn_pred[i]):<15}{int(onnx_pred[i]):<12}{'YES' if match else 'NO -- MISMATCH':<8}")
    print("=" * 70)

    if all_match:
        print(f"\nPASS: all {N_SANITY_SAMPLES} sanity samples match between "
              f"sklearn and the exported ONNX model.")
    else:
        print(f"\nFAIL: ONNX export diverges from the original sklearn model "
              f"on at least one sample. Do not ship this export.")

    print(f"\nDefinition of Done check:")
    print(f"  isolation_forest.onnx exists: {'YES' if os.path.exists(ONNX_PATH) else 'NO'}")
    print(f"  File size reasonable: {file_size_kb:.1f} KB")
    print(f"  sklearn/ONNX prediction match: {'PASS' if all_match else 'FAIL'}")


if __name__ == "__main__":
    main()
