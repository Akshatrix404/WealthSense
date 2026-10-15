"""
Converts the trained forecaster checkpoint to TFLite, int8-quantized, and
proves it actually works before any of this touches Kotlin.

A real conversion obstacle came up and is worth documenting here rather
than papering over: Keras's Bidirectional LSTM layers use a dynamic
`TensorListReserve`/`TensorListSetItem` mechanism internally for the
backward pass, and TFLite's converter can't lower that to static TFLite
builtin ops ("op requires element_shape to be static"). There are two
real fixes, with a real tradeoff between them:

  A) Add TF Select ops (`SELECT_TF_OPS`) so the unsupported ops fall back
     to full TensorFlow at runtime. This converts fine and produces a
     SMALL file (~100KB) via dynamic-range quantization (int8 weights,
     float32 activations) -- but full integer (int8 activation)
     quantization is NOT possible this way: calibration requires actually
     running the graph, and the standalone calibrator can't execute Flex
     ops. Shipping this also means adding the
     `org.tensorflow:tensorflow-lite-select-tf-ops` dependency to the
     Android app (~7-8MB added to the APK for the Flex delegate runtime)
     just to run one small model.

  B) Rebuild the identical architecture with `unroll=True` on both LSTM
     layers. Since the window size is a small, fixed 30 timesteps, this
     is cheap and mathematically identical (verified: max prediction
     diff vs the original model is exactly 0.0) -- it just unrolls the
     recurrence into 30 static copies of the cell logic at graph-build
     time, avoiding the dynamic TensorList mechanism entirely. This
     allows TRUE full-integer int8 quantization (int8 weights AND int8
     activations/I-O) with the standard TFLite runtime -- no Flex
     delegate needed at all. The file is bigger (~700KB, since unrolling
     duplicates the cell structure before quantization can help) but
     it's self-contained, which is the better trade for an Android app
     that isn't already pulling in the Select-ops runtime for anything
     else.

This script uses (B) as the primary path, with (A) kept as an automatic
fallback if unrolled conversion ever fails for some other reason.

Reads:  ml-training/models/forecaster.keras
        ml-training/models/forecaster_norm_stats.csv
        ml-training/data/synthetic_transactions.csv (for calibration + test
            samples, via the same train/val/test split as training)
Writes: ml-training/exports/forecaster.tflite

Run:
    ./venv/bin/python3 export_forecaster_tflite.py
"""

import os
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras

from train_forecaster import (
    build_daily_balance_series,
    split_users,
    build_windows,
    DATA_PATH,
    MODEL_PATH,
    NORM_STATS_PATH,
)

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
TFLITE_PATH = os.path.join(EXPORT_DIR, "forecaster.tflite")

N_CALIBRATION_SAMPLES = 200
N_SANITY_SAMPLES = 3
SEED = 11

# How much the quantized model's output is allowed to diverge from the
# full-precision model's output, in DE-NORMALIZED RUPEES, before we flag
# it. Chosen relative to the forecaster's own test-set MAE (~Rs 2,879,
# see evaluate_forecaster.py) -- a quantization delta that's a small
# fraction of the model's own error budget is fine; one comparable to or
# larger than the model's actual error would mean quantization is eating
# a meaningful chunk of accuracy for no good reason.
MAX_ACCEPTABLE_DELTA_RUPEES = 500.0

rng = np.random.default_rng(SEED)


def load_data():
    raw = pd.read_csv(DATA_PATH)
    daily = build_daily_balance_series(raw)
    all_users = sorted(daily["user_id"].unique())
    train_users, _, test_users = split_users(all_users)

    norm_stats_df = pd.read_csv(NORM_STATS_PATH)
    norm_stats = {
        row.user_id: (row.balance_mean, row.balance_std)
        for row in norm_stats_df.itertuples()
    }

    X_train, _ = build_windows(daily, train_users, norm_stats)
    X_test, y_test = build_windows(daily, test_users, norm_stats)

    return X_train, X_test, y_test, norm_stats, test_users


def build_unrolled_copy(model: keras.Model) -> keras.Model:
    """
    Rebuilds the same architecture with unroll=True on both LSTM layers
    and copies the trained weights over. Mathematically identical to the
    original (verified by the caller) -- unrolling only changes how the
    recurrence is represented in the graph, not what it computes.
    """
    input_shape = model.input_shape[1:]  # (30, 2)
    unrolled = keras.Sequential([
        keras.layers.Input(shape=input_shape),
        keras.layers.Bidirectional(keras.layers.LSTM(48, return_sequences=True, unroll=True)),
        keras.layers.Bidirectional(keras.layers.LSTM(24, unroll=True)),
        keras.layers.Dense(16, activation="relu"),
        keras.layers.Dense(1),
    ])
    unrolled.set_weights(model.get_weights())
    return unrolled


def make_representative_dataset(calibration_data: np.ndarray):
    def representative_dataset():
        for i in range(calibration_data.shape[0]):
            yield [calibration_data[i: i + 1].astype(np.float32)]
    return representative_dataset


def convert_full_int8_unrolled(model: keras.Model, X_train: np.ndarray) -> bytes:
    """Path (B): unrolled LSTM, true full-integer int8, no Flex ops needed."""
    unrolled_model = build_unrolled_copy(model)

    # Verify the unroll didn't change the model's actual computation.
    probe = X_train[:5]
    diff = np.max(np.abs(model.predict(probe, verbose=0) - unrolled_model.predict(probe, verbose=0)))
    print(f"Unrolled-vs-original prediction diff (should be ~0.0): {diff:.8f}")
    assert diff < 1e-4, "Unrolled model diverges from original -- weight copy or architecture mismatch"

    calib_indices = rng.choice(len(X_train), size=min(N_CALIBRATION_SAMPLES, len(X_train)), replace=False)
    calibration_data = X_train[calib_indices]

    converter = tf.lite.TFLiteConverter.from_keras_model(unrolled_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = make_representative_dataset(calibration_data)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    return converter.convert()


def convert_hybrid_int8_select_ops(model: keras.Model, X_train: np.ndarray) -> bytes:
    """Path (A) fallback: dynamic-range int8 weights via Select TF ops."""
    calib_indices = rng.choice(len(X_train), size=min(N_CALIBRATION_SAMPLES, len(X_train)), replace=False)
    calibration_data = X_train[calib_indices]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = make_representative_dataset(calibration_data)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS, tf.lite.OpsSet.SELECT_TF_OPS]
    converter._experimental_lower_tensor_list_ops = False
    return converter.convert()


def convert_to_tflite(model: keras.Model, X_train: np.ndarray) -> tuple:
    try:
        tflite_bytes = convert_full_int8_unrolled(model, X_train)
        return tflite_bytes, "full_int8_unrolled"
    except Exception as e:
        print(f"Unrolled full-int8 conversion failed ({type(e).__name__}: {e})")
        print("Falling back to hybrid int8 with Select TF ops "
              "(smaller file, but needs the Flex delegate at runtime) ...\n")
        tflite_bytes = convert_hybrid_int8_select_ops(model, X_train)
        return tflite_bytes, "hybrid_int8_select_ops"


def convert_to_tflite_float16(model: keras.Model) -> bytes:
    unrolled_model = build_unrolled_copy(model)
    converter = tf.lite.TFLiteConverter.from_keras_model(unrolled_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.target_spec.supported_types = [tf.float16]
    return converter.convert()


def run_tflite_inference(tflite_bytes: bytes, X: np.ndarray, mode: str) -> np.ndarray:
    interpreter = tf.lite.Interpreter(model_content=tflite_bytes)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]

    is_int8_io = input_details["dtype"] == np.int8

    preds = []
    for i in range(X.shape[0]):
        sample = X[i: i + 1]

        if is_int8_io:
            scale, zero_point = input_details["quantization"]
            sample_q = np.round(sample / scale + zero_point).astype(np.int8)
            interpreter.set_tensor(input_details["index"], sample_q)
        else:
            interpreter.set_tensor(input_details["index"], sample.astype(np.float32))

        interpreter.invoke()
        out = interpreter.get_tensor(output_details["index"])

        if is_int8_io:
            out_scale, out_zero_point = output_details["quantization"]
            out = (out.astype(np.float32) - out_zero_point) * out_scale

        preds.append(out.flatten()[0])

    return np.array(preds)


def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)

    print(f"Loading model checkpoint -> {MODEL_PATH}")
    model = keras.models.load_model(MODEL_PATH)

    print("Loading calibration + sanity-check data ...")
    X_train, X_test, y_test_norm, norm_stats, test_users = load_data()

    print(f"\nConverting to TFLite (int8) ...")
    tflite_bytes, mode = convert_to_tflite(model, X_train)

    with open(TFLITE_PATH, "wb") as f:
        f.write(tflite_bytes)

    file_size_kb = len(tflite_bytes) / 1024
    print(f"\nSaved -> {TFLITE_PATH}")
    print(f"Quantization mode achieved: {mode}")
    if mode == "full_int8_unrolled":
        print("  -> Weights AND activations int8, self-contained (standard TFLite")
        print("     runtime only, no Flex/Select-ops delegate needed on Android).")
    else:
        print("  -> Weights int8, activations float32, requires the")
        print("     'org.tensorflow:tensorflow-lite-select-tf-ops' Flex delegate")
        print("     dependency at runtime on Android (~7-8MB added to the APK).")
    print(f"File size: {file_size_kb:.1f} KB "
          f"({'OK, reasonable mobile asset size' if file_size_kb < 1024 else 'CHECK: larger than expected'})")

    # --- Sanity check: compare against full-precision model on N samples ---
    print(f"\nRunning standalone sanity-check inference (TFLite interpreter, "
          f"Python, no Android) on {N_SANITY_SAMPLES} test samples ...")
    sample_indices = rng.choice(len(X_test), size=N_SANITY_SAMPLES, replace=False)
    X_sample = X_test[sample_indices]
    y_sample_norm = y_test_norm[sample_indices]

    fp_pred_norm = model.predict(X_sample, verbose=0).flatten()
    tflite_pred_norm = run_tflite_inference(tflite_bytes, X_sample, mode)

    # De-normalize using the average per-user std as a rough rupee scale
    # (exact per-user de-norm isn't tracked per-sample here since these
    # are pooled test windows across many users -- this rough conversion
    # is only for reporting the delta in interpretable rupee terms).
    avg_std = np.mean([s for _, s in norm_stats.values()])

    print("\n" + "=" * 78)
    print(f"{'Sample':<8}{'True (norm)':<14}{'Full-precision':<18}{'TFLite':<28}{'Delta (Rs, approx)':<20}")
    print("=" * 78)
    deltas_rupees = []
    for i in range(N_SANITY_SAMPLES):
        delta_norm = abs(fp_pred_norm[i] - tflite_pred_norm[i])
        delta_rupees = delta_norm * avg_std
        deltas_rupees.append(delta_rupees)
        print(f"{i:<8}{y_sample_norm[i]:<14.4f}{fp_pred_norm[i]:<18.4f}{tflite_pred_norm[i]:<28.4f}"
              f"Rs {delta_rupees:,.2f}")

    max_delta = max(deltas_rupees)
    avg_delta = np.mean(deltas_rupees)

    print("=" * 78)
    print(f"\nMax delta:  Rs {max_delta:,.2f}")
    print(f"Avg delta:  Rs {avg_delta:,.2f}")
    print(f"Threshold:  Rs {MAX_ACCEPTABLE_DELTA_RUPEES:,.2f} "
          f"(chosen as a small fraction of the model's own ~Rs 2,879 test MAE)")

    quantization_ok = max_delta <= MAX_ACCEPTABLE_DELTA_RUPEES

    print("\n" + "-" * 78)
    if quantization_ok:
        print(f"PASS: quantization delta (Rs {max_delta:,.2f} max) is well within the "
              f"model's own error budget. int8 export is safe to ship.")
    else:
        print(f"WARNING: quantization delta (Rs {max_delta:,.2f} max) is significant "
              f"relative to the model's ~Rs 2,879 test MAE.")
        print(f"\nSuggestion: try float16 quantization instead of int8. For a small")
        print(f"recurrent model like this (43k params), the file-size difference")
        print(f"between int8 (~1/4 of float32) and float16 (~1/2 of float32) is only")
        print(f"a few hundred KB either way -- not a meaningful difference on a")
        print(f"modern phone -- but float16 keeps every activation in a format LSTM")
        print(f"gates handle natively and avoids the int8 calibration/rounding error")
        print(f"entirely. Given balance-forecast accuracy matters more than shaving")
        print(f"another ~100-200KB, float16 is very likely the better tradeoff here.")
        print(f"Generating a float16 build now for comparison ...")

        fp16_bytes = convert_to_tflite_float16(model)
        fp16_pred_norm = run_tflite_inference(fp16_bytes, X_sample, "float16")
        fp16_deltas = [abs(fp_pred_norm[i] - fp16_pred_norm[i]) * avg_std
                        for i in range(N_SANITY_SAMPLES)]
        fp16_max_delta = max(fp16_deltas)
        print(f"\nfloat16 candidate size: {len(fp16_bytes)/1024:.1f} KB")
        print(f"float16 max delta: Rs {fp16_max_delta:,.2f} (vs int8's Rs {max_delta:,.2f})")

        if fp16_max_delta <= MAX_ACCEPTABLE_DELTA_RUPEES:
            # float16 empirically clears the bar int8 failed -- ship THAT as
            # the actual forecaster.tflite, rather than leaving a model that
            # fails its own accuracy check as the named deliverable. Keep the
            # int8 attempt around too, clearly labeled, for comparison.
            int8_reference_path = os.path.join(EXPORT_DIR, "forecaster_int8_reference.tflite")
            with open(int8_reference_path, "wb") as f:
                f.write(tflite_bytes)  # the int8 bytes computed earlier
            with open(TFLITE_PATH, "wb") as f:
                f.write(fp16_bytes)

            file_size_kb = len(fp16_bytes) / 1024
            max_delta = fp16_max_delta
            mode = "float16 (promoted -- int8 failed its own delta check)"
            quantization_ok = True

            print(f"\nPROMOTED: float16 clears the delta threshold (int8 did not).")
            print(f"  {TFLITE_PATH} is now the float16 build ({file_size_kb:.1f} KB).")
            print(f"  The int8 attempt is kept for reference at {int8_reference_path} "
                  f"({len(tflite_bytes)/1024:.1f} KB) but is NOT the shipped model.")
        else:
            fp16_path = os.path.join(EXPORT_DIR, "forecaster_float16.tflite")
            with open(fp16_path, "wb") as f:
                f.write(fp16_bytes)
            print(f"\nfloat16 ALSO fails the delta threshold -- saved for reference at "
                  f"{fp16_path}, but neither quantized build is safe to ship as-is. "
                  f"forecaster.tflite remains the (failing) int8 build; consider the "
                  f"architecture/hyperparameter changes suggested above, or revisit "
                  f"the per-user normalization scheme before re-attempting export.")

    print("-" * 78)
    print(f"\nDefinition of Done check:")
    print(f"  forecaster.tflite exists: {'YES' if os.path.exists(TFLITE_PATH) else 'NO'}")
    print(f"  File size reasonable (few hundred KB): "
          f"{'YES' if file_size_kb < 1024 else 'CHECK'} ({file_size_kb:.1f} KB)")
    print(f"  Quantization delta check: {'PASS' if quantization_ok else 'FAILED - see warning above'}")


if __name__ == "__main__":
    main()
