"""本地中文 embedding 探针：不装 torch 也能验 bge-small-zh-v1.5 的 ONNX 导出能不能用。

依赖: onnxruntime, tokenizers, numpy（chromadb 会一起带上）
模型目录里需要有: *.onnx, tokenizer.json
获取: curl -sL -o model.onnx https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model.onnx
（同目录再下 tokenizer.json / config.json / tokenizer_config.json）

用法: python probe_local_embedding.py <model_dir>

验收判据不是「能加载」，是**语义排序对**：
    sim(物流太慢, 快递很慢) > sim(物流太慢, 电池不耐用) > sim(物流太慢, 音质很好)
排序反了说明 pooling 或归一化写错了，别拿它入库。
"""
import glob
import os
import sys
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

model_dir = sys.argv[1]
onnx_file = glob.glob(os.path.join(model_dir, "*.onnx"))[0]
so = ort.SessionOptions()
so.log_severity_level = 3
sess = ort.InferenceSession(onnx_file, sess_options=so, providers=["CPUExecutionProvider"])
input_names = {i.name for i in sess.get_inputs()}
tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
print("model:", os.path.basename(onnx_file), "| inputs:", sorted(input_names))


def encode(texts, max_len=256):
    encs = [tok.encode(t or " ") for t in texts]
    n = min(max(1, max(len(e.ids) for e in encs)), max_len)
    ids, mask, types = [], [], []
    for e in encs:
        k = min(len(e.ids), n)
        pad = n - k
        ids.append(e.ids[:k] + [0] * pad)
        mask.append(e.attention_mask[:k] + [0] * pad)
        types.append(e.type_ids[:k] + [0] * pad)     # bge 系列导出需要 token_type_ids
    feeds = {"input_ids": np.array(ids, np.int64),
             "attention_mask": np.array(mask, np.int64),
             "token_type_ids": np.array(types, np.int64)}
    return {k: v for k, v in feeds.items() if k in input_names}


texts = ["物流太慢", "快递很慢，等了一周", "音质很好，低音扎实", "电池不耐用，一天两充"]
t0 = time.time()
out = sess.run(None, encode(texts))[0]          # (n, seq, hidden)
v = out[:, 0, :].astype(np.float32)             # CLS pooling
v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
print(f"embed {len(texts)} texts in {(time.time()-t0)*1000:.0f} ms, dim={v.shape[1]}")

sim = lambda a, b: float(v[a] @ v[b])
pairs = {
    "物流太慢 vs 快递很慢 (应最高)": sim(0, 1),
    "物流太慢 vs 电池不耐用 (中)": sim(0, 3),
    "物流太慢 vs 音质很好 (应最低)": sim(0, 2),
}
for k, val in pairs.items():
    print(f"  {k:30s} {val:.3f}")
ok = pairs["物流太慢 vs 快递很慢 (应最高)"] > pairs["物流太慢 vs 音质很好 (应最低)"]
print("VERDICT:", "PASS 语义排序正确" if ok else "FAIL 排序反了——检查 CLS pooling / 归一化 / token_type_ids")
