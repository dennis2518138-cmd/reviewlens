"""bge-small-zh-v1.5 的 ONNX 参考实现（验证过语义排序，无需 torch / sentence-transformers）。

⚠️ 这是参考实现，不是要你直接调用的库。D3 请自己重写进 app/embedding.py ——
   面试会问「为什么用 CLS pooling 而不是 mean pooling」「为什么要 normalize」。

要点（都是踩过的）：
  1. 模型输入需要 token_type_ids；Xenova 的导出用不到 position_ids，缺了会报 InvalidArgument。
  2. bge 系列用 **CLS pooling**（取 hidden_state[:, 0, :]），不是 mean。
  3. 必须 L2 归一化，之后余弦相似度 = 点积，chroma 的 cosine 空间才对得上。
  4. 走 hf-mirror.com 下，21MB/s；直连 huggingface.co 是 000，chroma 自带的 S3 源 36KB/s。
"""
from __future__ import annotations

import glob
import os

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class BgeSmallZh:
    def __init__(self, model_dir: str | os.PathLike, max_len: int = 256):
        model_dir = str(model_dir)
        onnx_file = glob.glob(os.path.join(model_dir, "*.onnx"))[0]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(onnx_file, sess_options=so, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.max_len = max_len
        self.dim = 512

    def _feeds(self, texts: list[str]) -> dict[str, np.ndarray]:
        encs = [self.tok.encode(t or " ") for t in texts]
        n = max(1, min(max(len(e.ids) for e in encs), self.max_len))
        ids, mask, types = [], [], []
        for e in encs:
            k = min(len(e.ids), n)
            pad = n - k
            ids.append(e.ids[:k] + [0] * pad)
            mask.append(e.attention_mask[:k] + [0] * pad)
            types.append(e.type_ids[:k] + [0] * pad)
        feeds = {"input_ids": np.array(ids, np.int64),
                 "attention_mask": np.array(mask, np.int64),
                 "token_type_ids": np.array(types, np.int64)}
        return {k: v for k, v in feeds.items() if k in self.input_names}

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, 512) 的 L2 归一化向量。"""
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        out = self.sess.run(None, self._feeds(texts))[0]      # (n, seq, 512)
        v = out[:, 0, :].astype(np.float32)                    # CLS pooling
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
        return v

    # 让 chromadb 的 EmbeddingFunction 接口也能直接吃
    def __call__(self, input):  # noqa: A002 - chroma 的签名就是 input
        return self.embed(list(input)).tolist()

    def name(self):
        return "bge-small-zh-v1.5-onnx"


if __name__ == "__main__":
    import sys
    from time import perf_counter

    d = sys.argv[1] if len(sys.argv) > 1 else str(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "models" / "bge-small-zh-v1.5")
    m = BgeSmallZh(d)
    texts = ["物流太慢", "快递很慢，等了一周", "音质很好，低音扎实", "电池不耐用，一天两充"]
    t0 = perf_counter()
    v = m.embed(texts)
    print(f"dim={v.shape[1]}  {(perf_counter()-t0)*1000:.0f}ms / {len(texts)} 条")
    print(f"  sim(物流太慢, 快递很慢)   = {float(v[0] @ v[1]):.3f}   <- 应该最高")
    print(f"  sim(物流太慢, 音质很好)   = {float(v[0] @ v[2]):.3f}")
    print(f"  sim(物流太慢, 电池不耐用) = {float(v[0] @ v[3]):.3f}")
