from __future__ import annotations

import glob
import os

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class BgeSmallZh:
    def __init__(self, model_dir, max_len: int = 256):
        model_dir = str(model_dir)
        onnx_file = glob.glob(os.path.join(model_dir, "*.onnx"))[0]

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(onnx_file, sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.max_len = max_len
        self.dim = 512

    def _encode(self, texts):
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

    def embed(self, texts):
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        hidden = self.sess.run(None, self._encode(texts))[0]
        v = hidden[:, 0, :].astype(np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
        return v