"""
Retrains the BiLSTM forecaster with the SAME per-window (30-day) normalisation that the
app uses at inference (ForecastService.kt), then converts it to float16 TFLite.

Why: the originally shipped model was trained/evaluated with whole-90-day-series statistics,
which leak information from the forecast period. Measured effect (eval_v2/exp_norm.py): the
same weights give MAE Rs 9,346 instead of Rs 2,879 under the deployed protocol.

Run from ml-training/:   python3 retrain_window_norm.py
Outputs: exports/forecaster_windownorm_fp16.tflite, models/forecaster_windownorm.keras,
         eval_output/retrain_window_norm.json (test MAE to use as the display band).
After checking the numbers, copy the .tflite over android-app/app/src/main/assets/forecaster.tflite
and update TEST_MAE_RUPEES in ForecastService.kt.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "eval_v2"))
import numpy as np
import tensorflow as tf
from tensorflow import keras
import fx
from export_forecaster_tflite import convert_to_tflite_float16

HERE = os.path.dirname(__file__)
SEED = 0
S = fx.load_synth()
tr, va, te = fx.split_users(list(S))
rng = np.random.default_rng(5)
Btr, Ftr, Ytr, _ = fx.raw_windows(S, tr, stride=1)
Bva, Fva, Yva, _ = fx.raw_windows(S, va, stride=1)
Bte, Fte, Yte, Ute = fx.raw_windows(S, te, stride=1)
Xtr, m1, s1 = fx.norm_window(Btr, Ftr); ytr = fx.targets(Btr, Ytr, m1, s1)
Xva, m2, s2 = fx.norm_window(Bva, Fva); yva = fx.targets(Bva, Yva, m2, s2)
Xte, m3, s3 = fx.norm_window(Bte, Fte)
keras.utils.set_random_seed(SEED)
L = keras.layers
model = keras.Sequential([L.Input(shape=(30, 2)), L.Bidirectional(L.LSTM(48, return_sequences=True)), L.Bidirectional(L.LSTM(24)), L.Dense(16, activation="relu"), L.Dense(1)])
model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mae")
model.fit(Xtr, ytr, validation_data=(Xva, yva), epochs=40, batch_size=128, verbose=2,
          callbacks=[keras.callbacks.EarlyStopping(monitor="val_loss", patience=6, restore_best_weights=True)])
model.save(os.path.join(HERE, "models", "forecaster_windownorm.keras"))
p_fp = fx.to_money(model.predict(Xte, batch_size=2048, verbose=0)[:, 0], Bte, m3, s3)
mae = float(np.mean(np.abs(p_fp - Yte)))
tfl = convert_to_tflite_float16(model)
open(os.path.join(HERE, "exports", "forecaster_windownorm_fp16.tflite"), "wb").write(tfl)
it = tf.lite.Interpreter(model_content=tfl); it.allocate_tensors()
i, o = it.get_input_details()[0], it.get_output_details()[0]
idx = np.random.default_rng(1).choice(len(Xte), 300, replace=False); pl = []
for k in idx:
    it.set_tensor(i["index"], Xte[k:k + 1]); it.invoke(); pl.append(it.get_tensor(o["index"])[0, 0])
pl = fx.to_money(np.array(pl), Bte[idx], m3[idx], s3[idx])
res = dict(test_mae_inr=mae, naive_mae_inr=float(np.mean(np.abs(Bte[:, -1] - Yte))), n_test_windows=int(len(Xte)),
           fp16_max_abs_diff_inr=float(np.max(np.abs(pl - p_fp[idx]))), tflite_bytes=len(tfl))
print(res); json.dump(res, open(os.path.join(HERE, "eval_output", "retrain_window_norm.json"), "w"), indent=1)
