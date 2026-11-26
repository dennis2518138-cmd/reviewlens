from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from functools import lru_cache

import chromadb

from app.config import CHROMA_PATH, get_embedder
from app.rules import clean_rows, clean_text

COLLECTION = "reviews"
BATCH = 32


class BgeChromaEF:

    def __init__(self, model):
        self._m = model

    def __call__(self, input):
        return self._m.embed(list(input)).tolist()

    def embed_query(self, input):
        return self.__call__(input)

    def name(self):
        return "bge-small-zh-v1.5-onnx"


def get_collection(reset: bool = False):
    """拿集合。注意：原来每次 search 都新建 PersistentClient，实测容器内 warm 也要 ~10ms，
    冷启动首开 1.0s（chroma 库在 Windows 绑挂载卷上）。所以这里缓存住，reset 时才重建。"""
    if reset:
        _collection_cached.cache_clear()
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        return client.get_or_create_collection(
            COLLECTION,
            embedding_function=BgeChromaEF(get_embedder()),
            metadata={"hnsw:space": "cosine"},
        )
    return _collection_cached()


@lru_cache(maxsize=1)
def _collection_cached():
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_or_create_collection(
        COLLECTION,
        embedding_function=BgeChromaEF(get_embedder()),
        metadata={"hnsw:space": "cosine"},
    )


def index_reviews(rows: list[dict], reset: bool = False) -> dict:
    col = get_collection(reset=reset)
    kept, dropped = clean_rows(rows)

    by_reason: dict[str, int] = {}
    for _, reason in dropped:
        by_reason[reason] = by_reason.get(reason, 0) + 1

    docs, ids, metas = [], [], []
    for r in kept:
        docs.append(clean_text(r))
        ids.append(r["review_id"])
        metas.append({"product_id": r["product_id"], "brand": r["brand"],
                      "category": r["category"], "sku": r["sku"],
                      "rating": int(r["rating"]), "date": r["date"]})

    for i in range(0, len(docs), BATCH):
        col.upsert(ids=ids[i:i + BATCH], documents=docs[i:i + BATCH], metadatas=metas[i:i + BATCH])
    return {"indexed": len(docs), "skipped": len(dropped),
            "by_reason": by_reason, "count": col.count()}


def search_dense(query: str, k: int = 3, where: dict | None = None) -> list[dict]:
    """纯向量召回（原 search 的实现，保留作基线与降级路径）。"""
    col = get_collection()
    r = col.query(query_texts=[query], n_results=k, where=where)
    out = []
    for i, doc in enumerate(r["documents"][0]):
        d = r["distances"][0][i]
        out.append({"id": r["ids"][0][i], "text": doc, "distance": round(d, 4),
                    "similarity": round(1 - d, 4), **r["metadatas"][0][i]})
    return out


# ---------------- 稀疏召回：BM25（自写，零新依赖） ----------------
# 为什么不用 rank_bm25/jieba：本机都没装，且中文按「字 bigram」切分对短查询够用，
# 不必为一个演示引入分词依赖。缺点也写清：不做词性/停用词，同义改写仍靠向量补。
BM25_K1 = 1.5
BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    t = (text or "").lower()
    toks = re.findall(r"[a-z0-9]+", t)                      # 英文/数字整词
    for seg in re.findall(r"[\u4e00-\u9fff]+", t):          # 中文：字 bigram（单字段保留单字）
        toks += [seg] if len(seg) == 1 else [seg[i:i + 2] for i in range(len(seg) - 1)]
    return toks


class _BM25:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.tfs: list[Counter] = []
        self.lens: list[int] = []
        df: Counter = Counter()
        for r in rows:
            toks = _tokenize(r["text"])
            tf = Counter(toks)
            self.tfs.append(tf)
            self.lens.append(len(toks))
            df.update(tf.keys())
        n = len(rows)
        self.avgdl = (sum(self.lens) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def topn(self, query: str, n: int) -> list[dict]:
        q = _tokenize(query)
        if not q or not self.rows:
            return []
        scores = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            dl = self.lens[i] or 1
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / (self.avgdl or 1)))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [dict(self.rows[i], bm25=round(s, 4)) for s, i in scores[:n]]


@lru_cache(maxsize=1)
def _bm25_index() -> _BM25:
    """从 chroma 里取全量文档建 BM25 索引（一次建好常驻；库里数据变了要 reset_bm25()）。"""
    col = get_collection()
    got = col.get(include=["documents", "metadatas"])
    rows = [{"id": i, "text": d or "", "meta": m or {}}
            for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]
    return _BM25(rows)


def reset_bm25() -> None:
    """索引重建后调用（build_index.py 里会调）。"""
    _bm25_index.cache_clear()


def docs_containing(token: str) -> list[str]:
    """返回正文里含该字符串的 review_id（评测稀有 token 召回用，大小写不敏感）。"""
    t = (token or "").lower()
    return [r["id"] for r in _bm25_index().rows if t in (r["text"] or "").lower()]


def search_bm25(query: str, k: int = 3) -> list[dict]:
    """稀疏召回。返回形状对齐 search_dense：id/text/similarity + 元数据，另带 bm25 分。"""
    hits = _bm25_index().topn(query, k)
    out = []
    for h in hits:
        out.append({"id": h["id"], "text": h["text"], "distance": None,
                    "similarity": 0.0, **h["meta"], "bm25": h["bm25"]})
    return out


# ---------------- 融合：RRF ----------------
RRF_K = 60.0


def search_hybrid(query: str, k: int = 3, pool: int = 20,
                  w_dense: float = 1.0, w_bm25: float = 1.0) -> list[dict]:
    """向量 + BM25 双路召回，按 RRF(score = Σ w/(k+rank)) 融合。

    为什么要 RRF 而不是加权分数相加：两路分数量纲不同（cosine 0~1 vs BM25 无上界），
    直接相加会被 BM25 的量级碾压。RRF 只看名次，是业界默认做法（k=60 平滑常数）。
    """
    dense = search_dense(query, k=pool)
    sparse = search_bm25(query, k=pool)
    fused: dict[str, dict] = {}
    for rank, h in enumerate(dense, 1):
        d = fused.setdefault(h["id"], dict(h, rrf=0.0, rank_dense=None, rank_bm25=None))
        d["rrf"] += w_dense / (RRF_K + rank)
        d["rank_dense"] = rank
    for rank, h in enumerate(sparse, 1):
        d = fused.setdefault(h["id"], dict(h, rrf=0.0, rank_dense=None, rank_bm25=None))
        d["rrf"] += w_bm25 / (RRF_K + rank)
        d["rank_bm25"] = rank
        d.setdefault("bm25", h.get("bm25"))
    ranked = sorted(fused.values(), key=lambda x: -x["rrf"])[:k]
    for x in ranked:
        x["rrf"] = round(x["rrf"], 6)
    return ranked


def search(query: str, k: int = 3, where: dict | None = None) -> list[dict]:
    """运行时入口：默认混合召回；RETRIEVAL_MODE=dense 可降级回纯向量。

    注：BM25 这一路不支持 where 过滤（索引是全量的），传了 where 时自动走纯向量。
    """
    mode = os.getenv("RETRIEVAL_MODE", "hybrid").lower()
    if mode == "dense" or where is not None:
        return search_dense(query, k, where)
    return search_hybrid(query, k)


def warmup() -> dict:
    """预热：把 ONNX、chroma 集合、BM25 索引先建好。

    实测（容器内）：首次 search 1.04~1.44s，warm 只有 17~27ms。这笔钱不该让第一个用户付，
    所以在 lifespan 启动阶段预热一次（HEALTHCHECK 的 start-period=25s 足够覆盖）。
    """
    t = time.monotonic()
    get_embedder()
    t1 = time.monotonic()
    get_collection()
    t2 = time.monotonic()
    idx = _bm25_index()
    t3 = time.monotonic()
    return {"embedder_s": round(t1 - t, 3), "collection_s": round(t2 - t1, 3),
            "bm25_s": round(t3 - t2, 3), "docs": len(idx.rows), "total_s": round(t3 - t, 3)}