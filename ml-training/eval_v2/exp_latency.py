import time,json,resource,numpy as np
from ai_edge_litert.interpreter import Interpreter
import onnxruntime as ort
E='/home/claude/wealthsense/ml-training/exports/'
it=Interpreter(model_path=E+'forecaster.tflite',num_threads=2); it.allocate_tensors()
i=it.get_input_details()[0]; o=it.get_output_details()[0]
x=np.random.randn(*i['shape']).astype(np.float32)
for _ in range(50): it.set_tensor(i['index'],x); it.invoke()
t=[]
for _ in range(500):
    s=time.perf_counter(); it.set_tensor(i['index'],x); it.invoke(); _=it.get_tensor(o['index']); t.append((time.perf_counter()-s)*1e3)
t=np.array(t)
se=ort.InferenceSession(E+'isolation_forest.onnx',providers=['CPUExecutionProvider']); nm=se.get_inputs()[0].name
xa=np.array([[420.0,2.25]],dtype=np.float32)
for _ in range(50): se.run(None,{nm:xa})
u=[]
for _ in range(500):
    s=time.perf_counter(); se.run(None,{nm:xa}); u.append((time.perf_counter()-s)*1e3)
u=np.array(u)
import os
r=dict(tflite_median_ms=float(np.median(t)),tflite_p95_ms=float(np.percentile(t,95)),onnx_median_ms=float(np.median(u)),onnx_p95_ms=float(np.percentile(u,95)),
 tflite_bytes=os.path.getsize(E+'forecaster.tflite'),onnx_bytes=os.path.getsize(E+'isolation_forest.onnx'),note='host CPU, 2 threads, measured while other jobs were running; not a phone')
print(r); json.dump(r,open('res_latency.json','w'),indent=1)
